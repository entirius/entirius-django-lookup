# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Blocking recall — the only test that judges the fuzzy engine (test-strategy §3).

1000 synthetic fingerprints, 20 probes with a known true candidate: every probe must find its
target inside the top 50. Reuse this file to compare Postgres against another engine.
"""

import pytest

from django_lookup.enums import FingerprintKind
from django_lookup.models import Fingerprint
from django_lookup.schemas.requests.lookup import LookupQuery
from django_lookup.services.blocking import TRIGRAM_LIMIT, candidates
from django_lookup.services.query_parser import parse

pytestmark = pytest.mark.django_db

CATALOG_SIZE = 1000
PROBE_COUNT = 20
PROBE_STEP = 47  # coprime with the category cycle, so the probes spread over the whole catalog
SCOPE = [FingerprintKind.PIM_PRODUCT]
_CATEGORIES = ("wiertarka", "szlifierka", "lampa", "kabel", "torba", "fotel", "monitor", "klawiatura")


def _name(index: int) -> str:
    return f"{_CATEGORIES[index % len(_CATEGORIES)]} zx{index:04d} seria qr{index:03d}"


def _probe(index: int) -> str:
    """What an operator types: the model code first, one token forgotten, no series number."""
    return f"zx{index:04d} {_CATEGORIES[index % len(_CATEGORIES)]} seria"


@pytest.fixture
def catalog() -> list[Fingerprint]:
    rows = [
        Fingerprint(
            kind=FingerprintKind.PIM_PRODUCT,
            ref=f"SKU-{index:04d}",
            name_norm=_name(index),
            brand_norm="acme" if index % 2 else "globex",
            mpn_norm=f"ZX{index:04d}",
        )
        for index in range(CATALOG_SIZE)
    ]
    Fingerprint.objects.bulk_create(rows)
    return rows


def test_recall_at_50_is_complete_on_the_synthetic_catalog(catalog):
    targets = [index * PROBE_STEP for index in range(PROBE_COUNT)]
    missed = []
    for index in targets:
        parsed = parse(LookupQuery(q=_probe(index)))
        refs = {row.ref for row in candidates(parsed, SCOPE, limit=TRIGRAM_LIMIT)}
        if f"SKU-{index:04d}" not in refs:
            missed.append(index)
    assert missed == [], f"probes without their target in the top {TRIGRAM_LIMIT}: {missed}"


def test_every_candidate_carries_the_similarity_scoring_needs(catalog):
    parsed = parse(LookupQuery(q=_probe(7)))
    rows = candidates(parsed, SCOPE)
    assert rows
    assert all(row.name_similarity is not None for row in rows)
    assert rows[0].name_similarity >= 0.35


def test_unrelated_text_blocks_nothing(catalog):
    parsed = parse(LookupQuery(q="parasol plazowy skladany"))
    assert candidates(parsed, SCOPE) == []


def test_exact_gtin_wins_the_first_place_over_a_better_name_match(catalog):
    gtin = "05901234123457"
    Fingerprint.objects.create(kind=FingerprintKind.PIM_PRODUCT, ref="SKU-EAN", gtin14=gtin, name_norm="cos innego")
    parsed = parse(LookupQuery(q=f"wiertarka zx0000 seria {gtin}"))
    rows = candidates(parsed, SCOPE)
    assert rows[0].ref == "SKU-EAN"
    assert "SKU-0000" in {row.ref for row in rows}


def test_brand_and_mpn_block_without_any_name(catalog):
    parsed = parse(LookupQuery(brand="acme", mpn="zx-0001"))
    assert [row.ref for row in candidates(parsed, SCOPE)] == ["SKU-0001"]


def test_catalog_reference_blocks_on_its_own(catalog):
    parsed = parse(LookupQuery(sku="SKU-0002"))
    assert [row.ref for row in candidates(parsed, SCOPE)] == ["SKU-0002"]


def test_scope_keeps_the_other_catalog_out(catalog):
    Fingerprint.objects.create(
        kind=FingerprintKind.ATLAS_SOURCE_PRODUCT, ref="src:1", name_norm=_name(3), brand_norm="acme"
    )
    parsed = parse(LookupQuery(q=_probe(3)))
    assert all(row.kind == FingerprintKind.PIM_PRODUCT for row in candidates(parsed, SCOPE))
    both = candidates(parsed, [FingerprintKind.PIM_PRODUCT, FingerprintKind.ATLAS_SOURCE_PRODUCT])
    assert {row.kind for row in both} == {FingerprintKind.PIM_PRODUCT, FingerprintKind.ATLAS_SOURCE_PRODUCT}


def test_pool_is_capped(catalog):
    parsed = parse(LookupQuery(q="seria"))
    assert len(candidates(parsed, SCOPE, limit=5)) <= 5
