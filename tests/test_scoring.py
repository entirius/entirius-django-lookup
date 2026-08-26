# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Golden pairs and properties of the pairwise scoring (test-strategy §2, research r02 §1/§3/§4).

No database: `score_pair` works on a ParsedQuery and an unsaved Fingerprint, with the trigram
similarity injected the way blocking annotates it. Every pair names the feed case it stands for.
"""

from decimal import Decimal

from django.test import override_settings

from django_lookup.enums import DecisionAuto, FingerprintKind
from django_lookup.models import Fingerprint
from django_lookup.services.query_parser import ParsedQuery
from django_lookup.services.scoring import (
    FLAG_BRAND_CONFLICT,
    FLAG_VARIANT_CONFLICT,
    SCORE_MAX,
    score_pair,
)

TRUSTED_GTIN = "05901234123457"
OTHER_GTIN = "04006381333931"  # prefix 04 -> restricted circulation, supplier-invented


def fingerprint(name_similarity: float = 0.0, **fields) -> Fingerprint:
    row = Fingerprint(kind=FingerprintKind.PIM_PRODUCT, ref=fields.pop("ref", "SKU-1"), **fields)
    row.name_similarity = name_similarity
    return row


def codes(pair) -> list[str]:
    return [reason.code for reason in pair.reasons]


# --- golden pairs -------------------------------------------------------------------------------


def test_same_gtin_and_brand_agrees_is_a_match():
    """Supplier feed and PIM carry the same manufacturer EAN — the everyday happy path."""
    query = ParsedQuery(gtin14=TRUSTED_GTIN, gtin_trusted=True, brand_norm="bosch")
    pair = score_pair(query, fingerprint(gtin14=TRUSTED_GTIN, gtin_trusted=True, brand_norm="bosch"))
    assert pair.decision == DecisionAuto.MATCH
    assert codes(pair)[0] == "gtin_exact"


def test_bare_gtin_query_still_decides_match():
    """CMS single box: the operator pastes only an EAN (research r02 §4, first row)."""
    query = ParsedQuery(gtin14=TRUSTED_GTIN, gtin_trusted=True)
    pair = score_pair(query, fingerprint(gtin14=TRUSTED_GTIN, gtin_trusted=True))
    assert pair.decision == DecisionAuto.MATCH
    assert pair.score == 60


def test_same_gtin_but_different_brand_is_review():
    """Dirty EAN: one supplier reuses a manufacturer code for its own product."""
    query = ParsedQuery(gtin14=TRUSTED_GTIN, gtin_trusted=True, brand_norm="bosch")
    pair = score_pair(query, fingerprint(gtin14=TRUSTED_GTIN, gtin_trusted=True, brand_norm="makita"))
    assert pair.decision == DecisionAuto.REVIEW
    assert "brand_conflict" in codes(pair)
    assert FLAG_BRAND_CONFLICT in pair.flags


def test_untrusted_gtin_alone_is_not_enough():
    """In-house EAN (prefix 04) — valid, but says nothing about the manufacturer."""
    query = ParsedQuery(gtin14=OTHER_GTIN, gtin_trusted=False)
    pair = score_pair(query, fingerprint(gtin14=OTHER_GTIN, gtin_trusted=False))
    assert codes(pair) == ["gtin_exact_untrusted"]
    assert pair.decision == DecisionAuto.NO_MATCH


def test_brand_and_mpn_equal_but_colour_differs_is_review_never_match():
    """Colour siblings of one model — the classic false merge."""
    query = ParsedQuery(brand_norm="bosch", mpn_norm="GSR12V15", color="black")
    row = fingerprint(brand_norm="bosch", mpn_norm="GSR12V15", color="white")
    pair = score_pair(query, row)
    assert pair.decision == DecisionAuto.REVIEW
    assert FLAG_VARIANT_CONFLICT in pair.flags
    assert "color_conflict" in codes(pair)


def test_pack_quantity_difference_keeps_a_similar_listing_out():
    """Multipack vs single unit from the same feed — never the same product."""
    query = ParsedQuery(brand_norm="pampers", name_norm="pieluchy 4", pack_qty=1)
    row = fingerprint(brand_norm="pampers", name_norm="pieluchy 4", pack_qty=3, name_similarity=0.95)
    pair = score_pair(query, row)
    assert "pack_conflict" in codes(pair)
    assert pair.decision == DecisionAuto.NO_MATCH


def test_strong_name_with_agreeing_physicals_reaches_match():
    """Same product in two feeds, no shared identifier: name + weight + variant attributes."""
    query = ParsedQuery(
        brand_norm="bosch",
        name_norm="wiertarka udarowa 18v",
        weight=Decimal("1.500"),
        width=Decimal("10"),
        height=Decimal("20"),
        deep=Decimal("30"),
        color="black",
        size="m",
        pack_qty=1,
    )
    row = fingerprint(
        brand_norm="bosch",
        name_norm="wiertarka udarowa 18v akumulatorowa",
        weight=Decimal("1.530"),
        width=Decimal("10"),
        height=Decimal("20"),
        deep=Decimal("30"),
        color="black",
        size="m",
        pack_qty=1,
        name_similarity=0.9,
    )
    pair = score_pair(query, row)
    assert pair.score >= 75
    assert pair.decision == DecisionAuto.MATCH


def test_strong_name_alone_is_review():
    """Two similar titles and nothing else — a human has to look."""
    query = ParsedQuery(name_norm="wiertarka udarowa 18v")
    row = fingerprint(name_norm="wiertarka udarowa 18v akumulatorowa", name_similarity=0.9)
    pair = score_pair(query, row)
    assert set(codes(pair)) == {"name_trigram", "name_tokens_strong"}
    assert pair.decision == DecisionAuto.REVIEW


def test_weak_name_similarity_scores_nothing_below_the_floor():
    """Blocking may hand over a row at 0.34 through another leg — L3 must stay silent."""
    query = ParsedQuery(name_norm="lampa biurkowa led")
    row = fingerprint(name_norm="lampa sufitowa halogen", name_similarity=0.30)
    assert "name_trigram" not in codes(score_pair(query, row))


def test_missing_weight_on_one_side_is_silence_not_a_penalty():
    """Feeds often ship no weight — absence must not push a good pair down."""
    query = ParsedQuery(gtin14=TRUSTED_GTIN, gtin_trusted=True, weight=Decimal("1.0"))
    pair = score_pair(query, fingerprint(gtin14=TRUSTED_GTIN, gtin_trusted=True))
    assert codes(pair) == ["gtin_exact"]


def test_weight_outside_tolerance_is_a_penalty():
    """500 g vs 1 kg under one EAN — usually a case/multipack row."""
    query = ParsedQuery(gtin14=TRUSTED_GTIN, gtin_trusted=True, weight=Decimal("0.500"))
    row = fingerprint(gtin14=TRUSTED_GTIN, gtin_trusted=True, weight=Decimal("1.000"))
    assert "weight_conflict" in codes(score_pair(query, row))


def test_dimensions_compare_axis_order_free():
    """The same box measured L/W/H by one feed and W/H/L by another."""
    query = ParsedQuery(name_norm="karton", width=Decimal("10"), height=Decimal("20"), deep=Decimal("30"))
    row = fingerprint(name_norm="karton", width=Decimal("30"), height=Decimal("10"), deep=Decimal("20"))
    assert "dimensions_match" in codes(score_pair(query, row))


def test_mpn_without_a_brand_on_one_side_is_weak_evidence():
    """Atlas rows frequently have an MPN but no brand column."""
    query = ParsedQuery(mpn_norm="GSR12V15", brand_norm="bosch")
    pair = score_pair(query, fingerprint(mpn_norm="GSR12V15"))
    assert codes(pair) == ["mpn_exact"]
    assert pair.decision == DecisionAuto.NO_MATCH


def test_catalog_reference_hit_alone_does_not_decide():
    """A sku is unique inside one catalog only — supplier codes collide across sources."""
    query = ParsedQuery(sku="SKU-1")
    pair = score_pair(query, fingerprint(ref="SKU-1"))
    assert codes(pair) == ["sku_exact"]
    assert pair.decision == DecisionAuto.NO_MATCH


def test_score_is_clamped_to_the_0_100_range():
    query = ParsedQuery(
        gtin14=TRUSTED_GTIN,
        gtin_trusted=True,
        brand_norm="bosch",
        mpn_norm="GSR12V15",
        sku="SKU-1",
        name_norm="wiertarka udarowa 18v",
        color="black",
        size="m",
        pack_qty=1,
    )
    row = fingerprint(
        ref="SKU-1",
        gtin14=TRUSTED_GTIN,
        gtin_trusted=True,
        brand_norm="bosch",
        mpn_norm="GSR12V15",
        name_norm="wiertarka udarowa 18v",
        color="black",
        size="m",
        pack_qty=1,
        name_similarity=1.0,
    )
    pair = score_pair(query, row)
    assert pair.score == SCORE_MAX
    assert sum(reason.score for reason in pair.reasons) > SCORE_MAX


# --- properties ---------------------------------------------------------------------------------


def as_query(row: Fingerprint) -> ParsedQuery:
    """The mirror image of a fingerprint — used to score a pair in both directions."""
    return ParsedQuery(
        gtin14=row.gtin14,
        gtin_trusted=row.gtin_trusted,
        brand_norm=row.brand_norm,
        mpn_norm=row.mpn_norm,
        name_norm=row.name_norm,
        pack_qty=row.pack_qty,
        color=row.color,
        size=row.size,
        weight=row.weight,
        width=row.width,
        height=row.height,
        deep=row.deep,
    )


def test_scoring_is_symmetric():
    left = fingerprint(
        gtin14=TRUSTED_GTIN,
        gtin_trusted=True,
        brand_norm="bosch",
        name_norm="wiertarka udarowa 18v",
        weight=Decimal("1.5"),
        color="black",
        name_similarity=0.8,
    )
    right = fingerprint(
        gtin14=TRUSTED_GTIN,
        gtin_trusted=True,
        brand_norm="makita",
        name_norm="wiertarka udarowa 18v akumulatorowa",
        weight=Decimal("1.9"),
        color="white",
        name_similarity=0.8,
    )
    forward = score_pair(as_query(left), right)
    backward = score_pair(as_query(right), left)
    assert forward.score == backward.score
    assert sorted(codes(forward)) == sorted(codes(backward))


def test_agreeing_evidence_never_lowers_the_score():
    row = fingerprint(brand_norm="bosch", name_norm="wiertarka udarowa 18v", weight=Decimal("1.5"), name_similarity=0.8)
    without_brand = score_pair(ParsedQuery(name_norm=row.name_norm), row)
    with_brand = score_pair(ParsedQuery(name_norm=row.name_norm, brand_norm="bosch"), row)
    with_weight = score_pair(ParsedQuery(name_norm=row.name_norm, brand_norm="bosch", weight=Decimal("1.5")), row)
    assert without_brand.score <= with_brand.score <= with_weight.score


def test_reasons_add_up_to_the_score():
    query = ParsedQuery(brand_norm="bosch", mpn_norm="GSR12V15", color="black")
    pair = score_pair(query, fingerprint(brand_norm="bosch", mpn_norm="GSR12V15", color="white"))
    assert pair.score == max(sum(reason.score for reason in pair.reasons), 0)


def test_reasons_are_sorted_by_absolute_weight():
    query = ParsedQuery(gtin14=TRUSTED_GTIN, gtin_trusted=True, brand_norm="bosch", color="black")
    pair = score_pair(query, fingerprint(gtin14=TRUSTED_GTIN, gtin_trusted=True, brand_norm="bosch", color="white"))
    weights = [abs(reason.score) for reason in pair.reasons]
    assert weights == sorted(weights, reverse=True)


def test_thresholds_change_the_decision_without_changing_the_score():
    query = ParsedQuery(name_norm="wiertarka udarowa 18v")
    row = fingerprint(name_norm="wiertarka udarowa 18v akumulatorowa", name_similarity=0.9)
    default = score_pair(query, row)
    with override_settings(LOOKUP_THRESHOLDS={"match": 40, "review": 20}):
        lowered = score_pair(query, row)
    assert lowered.score == default.score
    assert default.decision == DecisionAuto.REVIEW
    assert lowered.decision == DecisionAuto.MATCH
