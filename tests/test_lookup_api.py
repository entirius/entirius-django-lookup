# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Admin API v2: contract, permissions, validation and throttling of /search/ and /check/."""

import pytest
from django.core.cache import cache
from django.urls import reverse

from django_lookup.api.admin.throttling import SCOPE, LookupThrottle
from django_lookup.enums import DecisionAuto, FingerprintKind
from django_lookup.providers.base import ProviderItem
from django_lookup.services.fingerprint_service import build_fingerprint, upsert_fingerprints

pytestmark = pytest.mark.django_db

GTIN = "5901234123457"
GTIN14 = "05901234123457"


@pytest.fixture
def catalog(pim_provider):
    item = ProviderItem(ref="SKU-1", name_by_lang={"pl": "Wiertarka udarowa Bosch"}, gtin=GTIN, brand="Bosch")
    pim_provider.add(item)
    upsert_fingerprints([build_fingerprint(item, FingerprintKind.PIM_PRODUCT)])
    return pim_provider


@pytest.fixture(autouse=True)
def clear_throttle_history():
    cache.clear()
    yield
    cache.clear()


def check_url() -> str:
    return reverse("admin-lookup-check")


def search_url() -> str:
    return reverse("admin-lookup-search")


def test_check_on_a_seeded_ean_answers_match_with_reasons(admin_client, catalog):
    response = admin_client.post(check_url(), {"q": GTIN}, format="json")
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["decision"] == DecisionAuto.MATCH
    assert body["query_parsed"]["gtin14"] == GTIN14
    candidate = body["candidates"][0]
    assert candidate["kind"] == FingerprintKind.PIM_PRODUCT
    assert candidate["ref"] == "SKU-1"
    assert candidate["decision"] == DecisionAuto.MATCH
    assert candidate["reasons"][0]["code"] == "gtin_exact"
    assert candidate["reasons"][0]["observed"] == {"query": GTIN14, "candidate": GTIN14}


def test_check_response_keeps_its_shape(admin_client, catalog):
    """The response is a contract — `reasons` and `basic` included (test-strategy, cross-cutting)."""
    body = admin_client.post(check_url(), {"q": GTIN}, format="json").json()
    assert set(body) == {"decision", "query_parsed", "candidates", "warnings"}
    candidate = body["candidates"][0]
    assert set(candidate) == {"kind", "ref", "similarity", "score", "decision", "reasons", "basic"}
    assert set(candidate["basic"]) == {"sku", "name", "brand", "ean", "main_image_url", "detail_url"}
    assert set(candidate["reasons"][0]) == {"code", "label", "score", "observed"}


def test_search_answers_hits_without_a_verdict(admin_client, catalog):
    body = admin_client.post(search_url(), {"q": GTIN}, format="json").json()
    assert set(body) == {"query_parsed", "hits", "warnings"}
    assert body["hits"][0]["similarity"] == 60
    assert "decision" not in body["hits"][0]
    assert "score" not in body["hits"][0]


def test_scope_narrows_the_catalogs(admin_client, catalog):
    body = admin_client.post(search_url(), {"q": GTIN, "scope": ["atlas_source_product"]}, format="json").json()
    assert body["hits"] == []
    assert body["warnings"] == ["kind_unavailable:atlas_source_product"]


def test_empty_query_is_rejected(admin_client, catalog):
    response = admin_client.post(check_url(), {}, format="json")
    assert response.status_code == 400, response.content
    assert response.json()["error"] == "VALIDATION_ERROR"


def test_out_of_range_limit_is_rejected(admin_client, catalog):
    assert admin_client.post(check_url(), {"q": GTIN, "limit": 500}, format="json").status_code == 400


def test_unknown_scope_value_is_rejected(admin_client, catalog):
    assert admin_client.post(check_url(), {"q": GTIN, "scope": ["everything"]}, format="json").status_code == 400


def test_a_body_that_is_not_an_object_is_rejected(admin_client, catalog):
    assert admin_client.post(check_url(), [{"q": GTIN}], format="json").status_code == 400


def test_anonymous_callers_get_401(api_client, catalog):
    assert api_client.post(check_url(), {"q": GTIN}, format="json").status_code == 401
    assert api_client.post(search_url(), {"q": GTIN}, format="json").status_code == 401


def test_anonymous_caller_is_rejected_before_the_body_is_ever_parsed(api_client, catalog):
    """APIView.initial() runs check_permissions() before check_throttles() — an unauthenticated
    caller is already 401 by the time get_throttles()/_carries_image() would touch request.data,
    so a malformed body surfaces as the 401 it deserves, never a parse-error-shaped 400."""
    response = api_client.post(search_url(), data=b"{not valid json", content_type="application/json")
    assert response.status_code == 401


def test_an_admin_caller_with_a_malformed_body_gets_the_views_own_400(admin_client, catalog):
    """get_throttles()/_carries_image() reads request.data for an authenticated caller now — a
    malformed body must not blow up there; it falls back to the text bucket and the view's own
    parsing raises the real 400 (not a 500 from an unhandled ParseError in get_throttles())."""
    response = admin_client.post(search_url(), data=b"{not valid json", content_type="application/json")
    assert response.status_code == 400


def test_authenticated_customers_get_403(customer_client, catalog):
    assert customer_client.post(check_url(), {"q": GTIN}, format="json").status_code == 403
    assert customer_client.post(search_url(), {"q": GTIN}, format="json").status_code == 403


def test_get_is_not_allowed(admin_client, catalog):
    assert admin_client.get(check_url()).status_code == 405


@pytest.fixture
def throttle_rates(monkeypatch):
    """DRF snapshots DEFAULT_THROTTLE_RATES onto the class at import — patch it, not the setting."""

    def apply(rates: dict[str, str]) -> None:
        monkeypatch.setattr(LookupThrottle, "THROTTLE_RATES", rates)

    return apply


def test_throttle_answers_429_over_the_rate(admin_client, catalog, throttle_rates):
    throttle_rates({SCOPE: "1/min"})
    assert admin_client.post(check_url(), {"q": GTIN}, format="json").status_code == 200
    response = admin_client.post(check_url(), {"q": GTIN}, format="json")
    assert response.status_code == 429
    assert response.json()["error"] == "RATE_LIMITED"


def test_throttle_is_on_by_default_without_any_service_configuration(admin_client, catalog, throttle_rates):
    """`rate = None` would disable throttling — the fallback is the safety net."""
    throttle_rates({})
    assert LookupThrottle().get_rate() == LookupThrottle.FALLBACK_RATE
    assert admin_client.post(check_url(), {"q": GTIN}, format="json").status_code == 200


def test_a_malformed_configured_rate_falls_back(throttle_rates):
    throttle_rates({SCOPE: "nonsense"})
    assert LookupThrottle().get_rate() == LookupThrottle.FALLBACK_RATE
