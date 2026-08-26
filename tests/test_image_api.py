# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The multipart half of the Admin API: image upload, image_url, validation and the degrade path."""

import pytest
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from django_lookup.api.admin.throttling import IMAGE_SCOPE, SCOPE, LookupImageThrottle
from django_lookup.constants import MAX_UPLOAD_IMAGE_BYTES
from django_lookup.embedding.base import EmbeddingError
from django_lookup.enums import DecisionAuto, FingerprintKind
from django_lookup.models import Fingerprint
from django_lookup.providers.base import ProviderItem
from django_lookup.services.fingerprint_service import build_fingerprint, upsert_fingerprints
from django_lookup.services.image_prep import load_and_crop, perceptual_hash
from django_lookup.services.lookup_service import WARNING_IMAGE_UNAVAILABLE
from tests import fake_embedding, images

pytestmark = pytest.mark.django_db

PHOTO = images.encode(images.product_image(31))
# Only the PIM provider is registered here, so the scope is explicit — an unregistered kind would
# add a `kind_unavailable:` warning and drown the ones these tests are about.
SCOPE_PIM = [FingerprintKind.PIM_PRODUCT.value]


@pytest.fixture(autouse=True)
def clear_throttle_history():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def catalog(pim_provider):
    """One PIM product whose fingerprint carries the hash and vector of PHOTO."""
    item = ProviderItem(ref="SKU-1", name_by_lang={"pl": "Wiertarka udarowa Bosch"}, brand="Bosch")
    pim_provider.add(item)
    upsert_fingerprints([build_fingerprint(item, FingerprintKind.PIM_PRODUCT)])
    prepared = load_and_crop(PHOTO)
    phash = perceptual_hash(prepared)
    from django_lookup.services.image_prep import encode

    Fingerprint.objects.filter(ref="SKU-1").update(
        phash=phash,
        image_vec=fake_embedding.vector_for(encode(prepared)),
        vec_model=fake_embedding.MODEL_ID,
    )
    return pim_provider


def _upload(name: str = "product.jpg", data: bytes = PHOTO, content_type: str = "image/jpeg") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, data, content_type=content_type)


def search_url() -> str:
    return reverse("admin-lookup-search")


def test_a_photo_of_a_catalogued_product_finds_it(admin_client, catalog):
    response = admin_client.post(search_url(), {"q": "", "scope": SCOPE_PIM, "image": _upload()}, format="multipart")
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["warnings"] == []
    assert body["hits"][0]["ref"] == "SKU-1"
    assert {reason["code"] for reason in body["hits"][0]["reasons"]} >= {"image_near_exact"}


def test_a_photo_alone_is_shown_but_never_decided(admin_client, catalog):
    """The picture proposes: the product comes back as a candidate, the verdict stays away from match."""
    payload = {"scope": SCOPE_PIM, "image": _upload()}
    response = admin_client.post(reverse("admin-lookup-check"), payload, format="multipart")
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["candidates"][0]["ref"] == "SKU-1"
    assert body["decision"] != DecisionAuto.MATCH


def test_multipart_carries_the_same_fields_as_json(admin_client, catalog):
    payload = {"q": "wiertarka", "limit": "3", "scope": SCOPE_PIM, "image": _upload()}
    response = admin_client.post(search_url(), payload, format="multipart")
    assert response.status_code == 200, response.content
    assert response.json()["query_parsed"]["name_norm"] == "wiertarka"


def test_an_empty_query_without_an_image_is_still_refused(admin_client, catalog):
    assert admin_client.post(search_url(), {"q": ""}, format="multipart").status_code == 400


def test_bytes_that_are_not_an_image_are_a_client_error(admin_client, catalog):
    response = admin_client.post(search_url(), {"image": _upload(data=b"not a picture")}, format="multipart")
    assert response.status_code == 400


def test_an_unsupported_image_format_is_a_client_error(admin_client, catalog):
    gif = images.encode(images.product_image(4), "GIF")
    assert admin_client.post(search_url(), {"image": _upload(data=gif)}, format="multipart").status_code == 400


def test_an_oversized_upload_is_refused_before_it_is_decoded(admin_client, catalog):
    huge = _upload(data=b"\x00" * (MAX_UPLOAD_IMAGE_BYTES + 1))
    assert admin_client.post(search_url(), {"image": huge}, format="multipart").status_code == 400


def test_a_dead_embedding_backend_degrades_instead_of_failing(admin_client, catalog):
    """The DoD case: `make embed` stopped, the answer still arrives with a warning and no 5xx."""
    fake_embedding.fail_with(EmbeddingError)
    payload = {"q": "wiertarka", "scope": SCOPE_PIM, "image": _upload()}
    response = admin_client.post(search_url(), payload, format="multipart")
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["warnings"] == [WARNING_IMAGE_UNAVAILABLE]
    assert body["hits"][0]["ref"] == "SKU-1"


def test_a_degraded_query_keeps_its_hash_evidence(admin_client, catalog):
    fake_embedding.fail_with(EmbeddingError)
    response = admin_client.post(search_url(), {"scope": SCOPE_PIM, "image": _upload()}, format="multipart")
    codes = {reason["code"] for reason in response.json()["hits"][0]["reasons"]}
    assert codes == {"image_near_exact"}


def test_the_image_layer_switched_off_answers_with_a_warning(admin_client, catalog):
    payload = {"q": "wiertarka", "scope": SCOPE_PIM, "image": _upload()}
    with override_settings(LOOKUP_IMAGE_ENABLED=False):
        response = admin_client.post(search_url(), payload, format="multipart")
    assert response.status_code == 200
    assert response.json()["warnings"] == [WARNING_IMAGE_UNAVAILABLE]


def test_an_image_url_is_fetched_through_the_guard(admin_client, catalog, monkeypatch):
    from django_lookup.services import image_service

    monkeypatch.setattr(image_service, "fetch_remote", lambda url: PHOTO)  # noqa: ARG005
    payload = {"image_url": "https://example.test/a.jpg", "scope": SCOPE_PIM}
    response = admin_client.post(search_url(), payload, format="json")
    assert response.status_code == 200, response.content
    assert response.json()["hits"][0]["ref"] == "SKU-1"


def test_an_unreachable_image_url_degrades(admin_client, catalog, monkeypatch):
    from django_lookup.services import image_service

    monkeypatch.setattr(image_service, "fetch_remote", _refuse)
    payload = {"q": "wiertarka", "image_url": "http://10.0.0.1/a.jpg", "scope": SCOPE_PIM}
    response = admin_client.post(search_url(), payload, format="json")
    assert response.status_code == 200
    assert response.json()["warnings"] == [WARNING_IMAGE_UNAVAILABLE]


def test_an_image_request_draws_on_its_own_throttle_bucket(admin_client, catalog, monkeypatch):
    """DRF snapshots DEFAULT_THROTTLE_RATES onto the class at import — patch it, not the setting."""
    monkeypatch.setattr(LookupImageThrottle, "THROTTLE_RATES", {IMAGE_SCOPE: "1/min", SCOPE: "60/min"})
    assert admin_client.post(search_url(), {"image": _upload()}, format="multipart").status_code == 200
    assert admin_client.post(search_url(), {"image": _upload()}, format="multipart").status_code == 429
    # The text bucket is untouched: a picture must not cost anyone their typing budget.
    assert admin_client.post(search_url(), {"q": "wiertarka"}, format="json").status_code == 200


def test_a_json_image_url_request_draws_on_the_image_throttle_bucket(admin_client, catalog, monkeypatch):
    """A JSON body carrying image_url pays for the same guarded fetch plus synchronous embedding
    call a multipart upload does, so it must draw on the tighter image bucket too — not the 60/min
    text bucket (restores the coverage the earlier "decide the bucket without parsing the body" fix
    dropped)."""
    from django_lookup.services import image_service

    monkeypatch.setattr(image_service, "fetch_remote", lambda url: PHOTO)  # noqa: ARG005
    monkeypatch.setattr(LookupImageThrottle, "THROTTLE_RATES", {IMAGE_SCOPE: "1/min", SCOPE: "60/min"})
    payload = {"image_url": "https://example.test/a.jpg", "scope": SCOPE_PIM}
    assert admin_client.post(search_url(), payload, format="json").status_code == 200
    assert admin_client.post(search_url(), payload, format="json").status_code == 429
    # The text bucket is untouched: an image_url request must not cost anyone their typing budget.
    assert admin_client.post(search_url(), {"q": "wiertarka"}, format="json").status_code == 200


def test_the_image_throttle_falls_back_when_the_scope_is_unconfigured():
    assert LookupImageThrottle().get_rate() == LookupImageThrottle.FALLBACK_RATE


def test_a_client_cannot_claim_an_image_it_did_not_send(admin_client, catalog):
    assert admin_client.post(search_url(), {"has_image": True}, format="json").status_code == 400


def _refuse(url: str) -> bytes:
    raise ValueError(f"internal host blocked: {url}")
