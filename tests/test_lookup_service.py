# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""search / check over real fingerprints, with the catalog behind the fake provider."""

import pytest

from django_lookup.enums import DecisionAuto, DecisionSource, FingerprintKind, MatchKind
from django_lookup.models import DedupDecision, Fingerprint
from django_lookup.providers.base import ProviderItem
from django_lookup.schemas.requests.lookup import LookupQuery
from django_lookup.services.fingerprint_service import build_fingerprint, upsert_fingerprints
from django_lookup.services.lookup_service import (
    WARNING_IMAGE_UNAVAILABLE,
    WARNING_KIND_UNAVAILABLE,
    check,
    search,
)

pytestmark = pytest.mark.django_db

GTIN = "5901234123457"
GTIN14 = "05901234123457"


def add_product(provider, ref: str, name: str, gtin: str | None = None, **fields) -> Fingerprint:
    """One catalog item: known to the provider and fingerprinted, exactly like the backfill does."""
    item = ProviderItem(ref=ref, name_by_lang={"pl": name}, gtin=gtin, **fields)
    provider.add(item)
    row = build_fingerprint(item, FingerprintKind.PIM_PRODUCT)
    upsert_fingerprints([row])
    return row


def test_search_returns_the_hit_with_its_display_data(pim_provider):
    add_product(pim_provider, "SKU-1", "Wiertarka udarowa Bosch", GTIN, brand="Bosch")
    hits = search(LookupQuery(q=GTIN)).hits
    assert [hit.ref for hit in hits] == ["SKU-1"]
    assert hits[0].basic["name"] == "Wiertarka udarowa Bosch"
    assert hits[0].basic["detail_url"] == "/fake/SKU-1"
    assert hits[0].similarity == 100  # relevance: a trusted GTIN is the whole answer
    assert hits[0].match == MatchKind.EXACT
    assert hits[0].reasons[0].code == "gtin_exact"


def test_check_on_a_seeded_ean_decides_match(pim_provider):
    add_product(pim_provider, "SKU-1", "Wiertarka udarowa Bosch", GTIN, brand="Bosch")
    result = check(LookupQuery(q=GTIN))
    assert result.decision == DecisionAuto.MATCH
    assert result.candidates[0].reasons[0].code == "gtin_exact"
    assert result.parsed.gtin14 == GTIN14


def test_check_logs_one_decision_row_per_candidate(pim_provider, django_user_model):
    add_product(pim_provider, "SKU-1", "Wiertarka udarowa Bosch", GTIN, brand="Bosch")
    user = django_user_model.objects.create_user(username="operator")
    check(LookupQuery(q=GTIN), user=user)
    row = DedupDecision.objects.get()
    assert row.candidate_kind == FingerprintKind.PIM_PRODUCT
    assert row.candidate_ref == "SKU-1"
    assert row.source == DecisionSource.API_CHECK
    assert row.decision_auto == DecisionAuto.MATCH
    assert row.query["gtin14"] == GTIN14
    assert [feature["code"] for feature in row.features] == ["gtin_exact"]
    assert row.user_id == user.id


def test_check_tags_the_decision_rows_with_the_callers_source(pim_provider):
    add_product(pim_provider, "SKU-1", "Wiertarka udarowa Bosch", GTIN, brand="Bosch")
    check(LookupQuery(q=GTIN), source=DecisionSource.CREATE_HOOK)
    assert DedupDecision.objects.get().source == DecisionSource.CREATE_HOOK


def test_search_writes_no_decision_log(pim_provider):
    add_product(pim_provider, "SKU-1", "Wiertarka udarowa Bosch", GTIN, brand="Bosch")
    search(LookupQuery(q=GTIN))
    assert DedupDecision.objects.count() == 0


def test_check_log_false_writes_no_decision_log(pim_provider):
    """The calibration harness runs this thousands of times per pairs file — no row per candidate."""
    add_product(pim_provider, "SKU-1", "Wiertarka udarowa Bosch", GTIN, brand="Bosch")
    result = check(LookupQuery(q=GTIN), log=False)
    assert result.decision == DecisionAuto.MATCH  # the verdict is unaffected by not logging it
    assert DedupDecision.objects.count() == 0


def test_colour_variant_is_review_not_match(pim_provider):
    add_product(pim_provider, "SKU-BLACK", "Koszulka bawelniana czarna", brand="Bosch", mpn="TS-100")
    result = check(LookupQuery(name="Koszulka bawelniana biala", brand="Bosch", mpn="TS-100"))
    assert result.decision == DecisionAuto.REVIEW
    codes = [reason.code for reason in result.candidates[0].reasons]
    assert "color_conflict" in codes


def test_candidates_are_ranked_best_first(pim_provider):
    add_product(pim_provider, "SKU-EAN", "Zupelnie inny produkt", GTIN)
    add_product(pim_provider, "SKU-NAME", "Wiertarka udarowa niebieska")
    result = check(LookupQuery(q=f"Wiertarka udarowa {GTIN}"))
    assert [candidate.ref for candidate in result.candidates] == ["SKU-EAN", "SKU-NAME"]
    assert result.candidates[0].score > result.candidates[1].score


def test_search_reports_relevance_to_the_query(pim_provider):
    """A name-only query is judged by the name: the same name is 100, a partial overlap less — and a
    name alone is never an `exact` match."""
    add_product(pim_provider, "SKU-NAME", "Wiertarka udarowa niebieska")
    add_product(pim_provider, "SKU-PARTIAL", "Wiertarka stolowa")
    hits = search(LookupQuery(name="Wiertarka udarowa niebieska")).hits
    assert hits[0].ref == "SKU-NAME"
    assert (hits[0].similarity, hits[0].match) == (100, MatchKind.SIMILAR)
    assert all(hit.similarity < 100 for hit in hits[1:])


def test_limit_caps_the_answer(pim_provider):
    for index in range(5):
        add_product(pim_provider, f"SKU-{index}", "Wiertarka udarowa niebieska")
    assert len(search(LookupQuery(q="Wiertarka udarowa niebieska", limit=2)).hits) == 2


def test_a_fingerprint_the_provider_no_longer_serves_is_dropped(pim_provider):
    add_product(pim_provider, "SKU-1", "Wiertarka udarowa Bosch", GTIN)
    pim_provider.reset()  # the item is gone; the refresh task has not caught up yet
    assert search(LookupQuery(q=GTIN)).hits == []


def test_a_kind_without_a_provider_warns_instead_of_failing(pim_provider):
    add_product(pim_provider, "SKU-1", "Wiertarka udarowa Bosch", GTIN)
    result = search(LookupQuery(q=GTIN))
    expected = WARNING_KIND_UNAVAILABLE.format(kind=FingerprintKind.ATLAS_SOURCE_PRODUCT)
    assert expected in result.warnings
    assert [hit.ref for hit in result.hits] == ["SKU-1"]


def test_an_image_query_warns_that_the_image_layer_is_not_wired_yet(pim_provider):
    add_product(pim_provider, "SKU-1", "Wiertarka udarowa Bosch", GTIN)
    result = search(LookupQuery(q=GTIN, image_url="https://example.com/a.jpg"))
    assert WARNING_IMAGE_UNAVAILABLE in result.warnings


def test_nothing_similar_gives_an_empty_answer(pim_provider):
    add_product(pim_provider, "SKU-1", "Wiertarka udarowa Bosch", GTIN)
    result = check(LookupQuery(q="parasol plazowy skladany"))
    assert result.candidates == []
    assert result.decision == DecisionAuto.NO_MATCH


def test_search_hits_carry_no_score_or_decision(pim_provider):
    """The boundary is enforced on the dataclass, not just hidden by the response schema."""
    add_product(pim_provider, "SKU-1", "Wiertarka udarowa Bosch", GTIN, brand="Bosch")
    hit = search(LookupQuery(q=GTIN)).hits[0]
    assert not hasattr(hit, "score")
    assert not hasattr(hit, "decision")


def test_display_uses_the_providers_batch_entry_points_when_available(pim_provider, monkeypatch):
    """`tests.fake_provider` defines `basics`/`detail_urls` — `lookup_service` must dispatch to them,
    one round trip for the whole hit list, instead of one `basic`/`detail_url` pair per ref."""
    add_product(pim_provider, "SKU-1", "Wiertarka udarowa Bosch", GTIN, brand="Bosch")
    add_product(pim_provider, "SKU-2", "Wiertarka udarowa niebieska")
    basics_calls: list[tuple[str, ...]] = []
    urls_calls: list[tuple[str, ...]] = []
    original_basics, original_detail_urls = pim_provider.basics, pim_provider.detail_urls

    def spy_basics(refs: list[str]):
        basics_calls.append(tuple(refs))
        return original_basics(refs)

    def spy_detail_urls(refs: list[str]):
        urls_calls.append(tuple(refs))
        return original_detail_urls(refs)

    monkeypatch.setattr(pim_provider, "basics", spy_basics)
    monkeypatch.setattr(pim_provider, "detail_urls", spy_detail_urls)

    hits = search(LookupQuery(q="Wiertarka udarowa")).hits
    assert {hit.ref for hit in hits} == {"SKU-1", "SKU-2"}
    assert basics_calls == [("SKU-1", "SKU-2")]  # one round trip for the whole hit list
    assert urls_calls == [("SKU-1", "SKU-2")]


def test_display_falls_back_to_singular_calls_without_batch_entry_points(pim_provider, monkeypatch):
    add_product(pim_provider, "SKU-1", "Wiertarka udarowa Bosch", GTIN, brand="Bosch")
    monkeypatch.delattr(pim_provider, "basics")
    monkeypatch.delattr(pim_provider, "detail_urls")
    hits = search(LookupQuery(q=GTIN)).hits
    assert hits[0].basic["name"] == "Wiertarka udarowa Bosch"


def test_display_falls_back_to_singular_calls_when_the_batch_form_raises(pim_provider, monkeypatch):
    """`basics`/`detail_urls` MUST omit unknown refs, not raise (providers/base.py) — a provider
    that gets this wrong still answers, through the singular `basic`/`detail_url` pair, instead of
    failing the whole search."""
    add_product(pim_provider, "SKU-1", "Wiertarka udarowa Bosch", GTIN, brand="Bosch")

    def broken_basics(refs: list[str]):
        raise LookupError("this provider forgot to omit unknown refs")

    monkeypatch.setattr(pim_provider, "basics", broken_basics)
    hits = search(LookupQuery(q=GTIN)).hits
    assert hits[0].basic["name"] == "Wiertarka udarowa Bosch"
