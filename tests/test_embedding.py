# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Embedding providers: factory wiring, batching, dimension validation, backoff, degrade."""

import pytest
import requests
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from django_lookup.constants import EMBEDDING_DIM
from django_lookup.embedding import transport
from django_lookup.embedding.base import EmbeddingError, ImageLayerDisabled
from django_lookup.embedding.factory import (
    current_model_id,
    dimension_mismatch,
    embedding_config,
    get_embedding_provider,
)
from django_lookup.embedding.http_provider import HttpEmbeddingProvider
from django_lookup.embedding.null_provider import NullEmbeddingProvider
from django_lookup.embedding.voyage_provider import VoyageEmbeddingProvider
from tests import fake_embedding

# A literal public address: the SSRF guard resolves hostnames, and the suite has no DNS.
URL = "https://93.184.216.34/embeddings"
HTTP_SETTINGS = {"provider": "http", "url": URL, "model": "siglip", "dim": EMBEDDING_DIM, "api_key": "k"}
IMAGES = [b"one", b"two"]


class _Response:
    """Just enough of `requests.Response` for the transport layer."""

    def __init__(self, payload: dict | None = None, status_code: int = 200):
        self.payload, self.status_code = payload or {}, status_code

    @property
    def ok(self) -> bool:
        return self.status_code < 400

    def json(self) -> dict:
        return self.payload


def _body(count: int, dim: int = EMBEDDING_DIM) -> dict:
    return {"model": "siglip", "data": [{"index": i, "embedding": [0.1] * dim} for i in range(count)]}


class _Backend:
    """Records the requests the transport made and hands back queued answers."""

    def __init__(self):
        self.requests: list[dict] = []
        self.answers: list[_Response] = []

    def post(self, url, json, headers, timeout):  # noqa: A002 — mirrors requests.post
        self.requests.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if self.answers:
            return self.answers.pop(0)
        return _Response(_body(len(json.get("input", json.get("inputs", [])))))

    def __len__(self) -> int:
        return len(self.requests)

    def __getitem__(self, index: int) -> dict:
        return self.requests[index]

    def __iter__(self):
        return iter(self.requests)


@pytest.fixture
def posted(monkeypatch) -> _Backend:
    backend = _Backend()
    monkeypatch.setattr(transport.requests, "post", backend.post)
    monkeypatch.setattr(transport.time, "sleep", lambda _seconds: None)
    return backend


def test_the_factory_builds_the_provider_named_in_settings():
    for name, expected in (("http", HttpEmbeddingProvider), ("none", NullEmbeddingProvider)):
        with override_settings(LOOKUP_EMBEDDING={**HTTP_SETTINGS, "provider": name}):
            assert isinstance(get_embedding_provider(), expected)
    with override_settings(LOOKUP_EMBEDDING={**HTTP_SETTINGS, "provider": "voyage"}):
        assert isinstance(get_embedding_provider(), VoyageEmbeddingProvider)


def test_a_dotted_path_is_a_provider_too():
    assert isinstance(get_embedding_provider(), fake_embedding.FakeEmbeddingProvider)


def test_an_unknown_provider_is_a_configuration_error():
    with override_settings(LOOKUP_EMBEDDING={"provider": "telepathy"}), pytest.raises(ImproperlyConfigured):
        get_embedding_provider()


def test_unknown_settings_keys_do_not_break_the_config():
    with override_settings(LOOKUP_EMBEDDING={**HTTP_SETTINGS, "future_knob": 1}):
        assert embedding_config().url == URL


def test_the_none_provider_refuses_instead_of_pretending():
    with override_settings(LOOKUP_EMBEDDING={"provider": "none"}):
        assert current_model_id() == "none"
        with pytest.raises(ImageLayerDisabled):
            get_embedding_provider().embed_images([b"x"])


def test_the_model_id_is_what_gets_stamped_on_a_row():
    with override_settings(LOOKUP_EMBEDDING=HTTP_SETTINGS):
        assert current_model_id() == "siglip"


def test_a_configured_dimension_that_contradicts_the_column_is_reported():
    with override_settings(LOOKUP_EMBEDDING={**HTTP_SETTINGS, "dim": EMBEDDING_DIM + 1}):
        assert "halfvec" in dimension_mismatch()
    with override_settings(LOOKUP_EMBEDDING=HTTP_SETTINGS):
        assert dimension_mismatch() == ""


def test_the_http_provider_sends_data_urls_with_the_bearer_key(posted):
    with override_settings(LOOKUP_EMBEDDING=HTTP_SETTINGS):
        results = get_embedding_provider().embed_images(IMAGES)
    assert [result.dim for result in results] == [EMBEDDING_DIM, EMBEDDING_DIM]
    assert posted[0]["headers"] == {"Authorization": "Bearer k"}
    assert all(value.startswith("data:image/jpeg;base64,") for value in posted[0]["json"]["input"])


def test_vectors_follow_the_declared_index_not_the_arrival_order(posted):
    scrambled = {"model": "siglip", "data": [{"index": 1, "embedding": [2.0]}, {"index": 0, "embedding": [1.0]}]}
    posted.answers.append(_Response(scrambled))
    with override_settings(LOOKUP_EMBEDDING={**HTTP_SETTINGS, "dim": 1}):
        assert [result.vector for result in get_embedding_provider().embed_images(IMAGES)] == [[1.0], [2.0]]


def test_a_dimension_mismatch_raises_rather_than_poisoning_the_index(posted):
    posted.answers.append(_Response(_body(2, dim=EMBEDDING_DIM - 1)))
    with override_settings(LOOKUP_EMBEDDING=HTTP_SETTINGS), pytest.raises(EmbeddingError, match="dimension"):
        get_embedding_provider().embed_images(IMAGES)


def test_a_short_answer_raises(posted):
    posted.answers.append(_Response(_body(1)))
    with override_settings(LOOKUP_EMBEDDING=HTTP_SETTINGS), pytest.raises(EmbeddingError, match="1 vectors"):
        get_embedding_provider().embed_images(IMAGES)


def test_more_images_than_one_batch_are_split(posted):
    many = [f"image-{index}".encode() for index in range(transport.MAX_BATCH + 5)]
    with override_settings(LOOKUP_EMBEDDING=HTTP_SETTINGS):
        assert len(get_embedding_provider().embed_images(many)) == len(many)
    assert sorted(len(request["json"]["input"]) for request in posted) == [5, transport.MAX_BATCH]


def test_a_busy_backend_is_retried_then_given_up_on(posted):
    posted.answers.extend(_Response(status_code=503) for _ in range(3))
    with override_settings(LOOKUP_EMBEDDING=HTTP_SETTINGS), pytest.raises(EmbeddingError, match="busy"):
        get_embedding_provider().embed_images([b"x"])
    assert len(posted) == 3


def test_a_busy_backend_that_recovers_is_not_an_error(posted):
    posted.answers.extend([_Response(status_code=429), _Response(_body(1))])
    with override_settings(LOOKUP_EMBEDDING=HTTP_SETTINGS):
        assert len(get_embedding_provider().embed_images([b"x"])) == 1


def test_a_transport_failure_becomes_an_embedding_error(monkeypatch):
    """A timeout is a degrade signal, never a stack trace escaping into a 500."""
    monkeypatch.setattr(transport.requests, "post", _raise_timeout)
    with override_settings(LOOKUP_EMBEDDING=HTTP_SETTINGS), pytest.raises(EmbeddingError, match="request failed"):
        get_embedding_provider().embed_images([b"x"])


def test_a_private_embedding_host_needs_the_allowlist(posted):
    private = {**HTTP_SETTINGS, "url": "http://embed:7997/embeddings"}
    with override_settings(LOOKUP_EMBEDDING=private), pytest.raises(EmbeddingError, match="SSRF"):
        get_embedding_provider().embed_images([b"x"])
    with override_settings(LOOKUP_EMBEDDING=private, LOOKUP_EMBED_ALLOWED_HOSTS=["embed"]):
        assert len(get_embedding_provider().embed_images([b"x"])) == 1


def test_the_voyage_provider_wraps_every_image_in_a_content_list(posted):
    with override_settings(LOOKUP_EMBEDDING={**HTTP_SETTINGS, "provider": "voyage", "url": ""}):
        assert len(get_embedding_provider().embed_images(IMAGES)) == 2
    assert posted[0]["url"].startswith("https://api.voyageai.com/")
    assert posted[0]["json"]["inputs"][0]["content"][0]["type"] == "image_base64"


def test_info_reports_what_the_backend_actually_returns(posted):
    posted.answers.append(_Response({"model": "someone-swapped-me", "data": [{"index": 0, "embedding": [0.0] * 7}]}))
    with override_settings(LOOKUP_EMBEDDING=HTTP_SETTINGS):
        info = get_embedding_provider().info()
    assert (info.model_id, info.dim) == ("someone-swapped-me", 7)


def _raise_timeout(*args, **kwargs):
    raise requests.Timeout("too slow")
