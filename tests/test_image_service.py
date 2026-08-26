# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The catalog side of the image layer: fetch, hash, embed, write — and what it skips."""

from pathlib import Path

import pytest
from django.test import override_settings

from django_lookup.embedding.base import EmbeddingError
from django_lookup.enums import FingerprintKind
from django_lookup.models import Fingerprint
from django_lookup.providers.base import ProviderItem
from django_lookup.services import image_service
from django_lookup.services.image_prep import InvalidImage
from django_lookup.services.image_service import embed_refs, prepare_query
from tests import fake_embedding, images

pytestmark = pytest.mark.django_db

KIND = FingerprintKind.PIM_PRODUCT


@pytest.fixture
def catalog(pim_provider, tmp_path):
    """Two fingerprints whose provider items point at real files on disk (the PIM shape)."""

    def add(ref: str, seed: int) -> str:
        path = tmp_path / f"{ref}.png"
        path.write_bytes(images.encode(images.product_image(seed)))
        pim_provider.add(ProviderItem(ref=ref, name_by_lang={"pl": ref}, image_path_or_url=str(path)))
        Fingerprint.objects.create(kind=KIND, ref=ref, name_norm=ref)
        return str(path)

    return {"SKU-1": add("SKU-1", 11), "SKU-2": add("SKU-2", 22)}


def _row(ref: str) -> Fingerprint:
    return Fingerprint.objects.get(kind=KIND, ref=ref)


def test_a_run_hashes_and_embeds_every_ref(catalog):
    assert embed_refs(KIND, ["SKU-1", "SKU-2"]) == 2
    row = _row("SKU-1")
    assert row.phash is not None
    assert row.image_sha1 and row.vec_model == fake_embedding.MODEL_ID
    assert len(row.image_vec) == len(fake_embedding.vector_for(b"x"))


def test_one_batch_reaches_the_provider(catalog):
    embed_refs(KIND, ["SKU-1", "SKU-2"])
    assert fake_embedding.CALLS == [2]


def test_an_unchanged_picture_is_skipped_on_the_next_run(catalog):
    embed_refs(KIND, ["SKU-1"])
    fake_embedding.CALLS.clear()
    assert embed_refs(KIND, ["SKU-1"]) == 0
    assert fake_embedding.CALLS == []


def test_a_changed_picture_is_re_embedded(catalog):
    embed_refs(KIND, ["SKU-1"])
    before = _row("SKU-1").image_sha1
    Path(catalog["SKU-1"]).write_bytes(images.encode(images.product_image(99)))
    assert embed_refs(KIND, ["SKU-1"]) == 1
    assert _row("SKU-1").image_sha1 != before


def test_a_row_embedded_by_another_model_is_re_embedded(catalog):
    embed_refs(KIND, ["SKU-1"])
    Fingerprint.objects.filter(ref="SKU-1").update(vec_model="older-model")
    assert embed_refs(KIND, ["SKU-1"]) == 1
    assert _row("SKU-1").vec_model == fake_embedding.MODEL_ID


def test_a_dead_backend_still_writes_the_hashes(catalog):
    """notes §Remote GPU obligation 2: degrade, do not fail — and retry on the next run."""
    fake_embedding.fail_with(EmbeddingError)
    assert embed_refs(KIND, ["SKU-1"]) == 1
    row = _row("SKU-1")
    assert row.phash is not None
    assert row.image_vec is None and row.vec_model == ""

    fake_embedding.fail_with(None)
    assert embed_refs(KIND, ["SKU-1"]) == 1
    assert _row("SKU-1").image_vec is not None


def test_a_ref_without_a_picture_is_left_alone(pim_provider):
    pim_provider.add(ProviderItem(ref="SKU-9", name_by_lang={"pl": "x"}))
    Fingerprint.objects.create(kind=KIND, ref="SKU-9")
    assert embed_refs(KIND, ["SKU-9"]) == 0


def test_a_missing_file_skips_the_row_not_the_batch(catalog, pim_provider, tmp_path):
    Fingerprint.objects.create(kind=KIND, ref="SKU-GONE")
    pim_provider.add(ProviderItem(ref="SKU-GONE", name_by_lang={"pl": "x"}, image_path_or_url=str(tmp_path / "no.png")))
    assert embed_refs(KIND, ["SKU-GONE", "SKU-1"]) == 1
    assert _row("SKU-1").phash is not None


def test_broken_bytes_skip_the_row(pim_provider, tmp_path):
    path = tmp_path / "broken.png"
    path.write_bytes(b"this is not a png")
    pim_provider.add(ProviderItem(ref="SKU-BAD", name_by_lang={"pl": "x"}, image_path_or_url=str(path)))
    Fingerprint.objects.create(kind=KIND, ref="SKU-BAD")
    assert embed_refs(KIND, ["SKU-BAD"]) == 0


def test_a_remote_image_goes_through_the_guard(monkeypatch, pim_provider):
    fetched = []
    monkeypatch.setattr(
        image_service,
        "fetch_remote",
        lambda url, allowed_hosts=(): fetched.append(url) or _png(),  # noqa: ARG005
    )
    pim_provider.add(
        ProviderItem(ref="SKU-URL", name_by_lang={"pl": "x"}, image_path_or_url="https://example.test/a.png")
    )
    Fingerprint.objects.create(kind=KIND, ref="SKU-URL")
    assert embed_refs(KIND, ["SKU-URL"]) == 1
    assert fetched == ["https://example.test/a.png"]


def test_a_remote_answer_that_is_not_an_image_is_refused(monkeypatch):
    monkeypatch.setattr(
        image_service.url_guard,
        "safe_get",
        lambda url, timeout, cap, allowed_hosts=(): image_service.url_guard.Fetched(  # noqa: ARG005
            b"<html>", "text/html"
        ),
    )
    with pytest.raises(ValueError, match="not an image"):
        image_service.fetch_remote("https://example.test/a.png")


def test_fetch_remote_forwards_whatever_allowlist_its_caller_gives_it(monkeypatch):
    """fetch_remote itself carries no allowlist opinion — it forwards exactly what it is given.
    `_load` (catalog/worker path, tested below) is the one caller allowed to pass the operator's
    LOOKUP_EMBED_ALLOWED_HOSTS; `lookup_service._query_image` (request-supplied image_url) must not."""
    calls = []
    monkeypatch.setattr(
        image_service.url_guard,
        "safe_get",
        lambda url, timeout, cap, allowed_hosts=(): (
            calls.append(tuple(allowed_hosts)) or image_service.url_guard.Fetched(_png(), "image/png")
        ),
    )
    image_service.fetch_remote("http://fixtures/a.png", allowed_hosts=["fixtures"])
    assert calls == [("fixtures",)]
    image_service.fetch_remote("http://fixtures/a.png")
    assert calls[-1] == ()


class _FakeGetResponse:
    """Minimal requests.Response stand-in for a successful fetch (mirrors test_url_guard.py's own)."""

    def __init__(self, content: bytes, content_type: str):
        self.status_code, self.headers, self._content = 200, {"Content-Type": content_type}, content

    def iter_content(self, chunk_size):  # noqa: ARG002 — mirrors requests.Response
        yield self._content

    def raise_for_status(self):
        pass

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def test_the_catalog_path_reaches_an_allowlisted_private_host(monkeypatch, pim_provider):
    """A private IP allowlisted for the embedding client is also reachable for a catalog image
    fetched through embed_refs (the worker path) — this URL is provider-owned, never
    request-supplied. Contrast with the next test."""
    monkeypatch.setattr(
        image_service.url_guard.requests,
        "get",
        lambda url, timeout, stream, allow_redirects: _FakeGetResponse(_png(), "image/png"),  # noqa: ARG005
    )
    pim_provider.add(ProviderItem(ref="SKU-URL", name_by_lang={"pl": "x"}, image_path_or_url="http://10.0.0.5/a.png"))
    Fingerprint.objects.create(kind=KIND, ref="SKU-URL")
    with override_settings(LOOKUP_EMBED_ALLOWED_HOSTS=["10.0.0.5"], LOOKUP_BLOCK_PRIVATE_HOSTS=True):
        assert embed_refs(KIND, ["SKU-URL"]) == 1


def test_a_request_supplied_image_url_cannot_reach_an_allowlisted_private_host():
    """The query path (lookup_service._query_image) must never inherit the operator's embed
    allowlist — punching that hole here would let an admin-JWT caller use image_url as a blind
    SSRF oracle against internal hosts. fetch_remote defaults to no allowlist, so the same host
    the worker path reaches above is blocked when the caller supplies none."""
    with (
        override_settings(LOOKUP_EMBED_ALLOWED_HOSTS=["10.0.0.5"], LOOKUP_BLOCK_PRIVATE_HOSTS=True),
        pytest.raises(ValueError, match="internal host blocked"),
    ):
        image_service.fetch_remote("http://10.0.0.5/a.png")


def test_prepare_query_hashes_and_embeds_without_touching_the_database():
    prepared = prepare_query(_png())
    assert prepared.phash is not None and prepared.vector is not None
    assert prepared.degraded is False
    assert Fingerprint.objects.count() == 0


def test_prepare_query_degrades_to_hashes_when_the_backend_is_down():
    fake_embedding.fail_with(EmbeddingError)
    prepared = prepare_query(_png())
    assert prepared.degraded is True
    assert prepared.vector is None and prepared.phash is not None


def test_prepare_query_rejects_bytes_that_are_not_an_image():
    with pytest.raises(InvalidImage):
        prepare_query(b"nope")


def test_the_same_picture_always_prepares_the_same_evidence():
    assert prepare_query(_png()).phash == prepare_query(_png()).phash


def test_with_the_provider_off_the_hashes_are_still_written(catalog):
    with override_settings(LOOKUP_EMBEDDING={"provider": "none"}):
        assert embed_refs(KIND, ["SKU-1"]) == 1
    row = _row("SKU-1")
    assert row.phash is not None and row.image_vec is None


def _png() -> bytes:
    return images.encode(images.product_image(42))
