# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Query parsing: free text -> the same keys the fingerprints were built with."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from django_lookup.schemas.requests.lookup import Attrs, LookupQuery
from django_lookup.services.query_parser import parse

VALID_EAN = "5901234123457"
VALID_EAN14 = "05901234123457"


def test_bare_gtin_query_yields_only_the_gtin_key():
    parsed = parse(LookupQuery(q=VALID_EAN))
    assert parsed.gtin14 == VALID_EAN14
    assert parsed.gtin_trusted is True
    assert parsed.name_norm == ""


def test_gtin_inside_free_text_is_taken_out_of_the_name():
    parsed = parse(LookupQuery(q=f"Bosch wiertarka {VALID_EAN} czarna"))
    assert parsed.gtin14 == VALID_EAN14
    assert VALID_EAN not in parsed.name_norm
    assert parsed.brand_norm == "bosch"
    assert parsed.color == "black"


def test_digit_run_that_is_not_a_valid_gtin_stays_in_the_name():
    parsed = parse(LookupQuery(q="akumulator 12345678901 2.0ah"))
    assert parsed.gtin14 == ""
    assert "12345678901" in parsed.name_norm


def test_explicit_ean_wins_over_a_gtin_found_in_the_text():
    other = "4006381333931"
    parsed = parse(LookupQuery(q=f"lampa {VALID_EAN}", ean=other))
    assert parsed.gtin14 == other.zfill(14)


def test_explicit_brand_field_is_alias_resolved():
    assert parse(LookupQuery(brand="Hewlett-Packard GmbH", q="laptop")).brand_norm == "hp"


def test_brand_only_query_leaves_no_name_to_search_on():
    """A brand found inside free text keeps the dictionary spelling — exactly what
    `build_fingerprint` stores for a catalog item without a brand field, so both sides agree."""
    parsed = parse(LookupQuery(q="Hewlett-Packard"))
    assert parsed.brand_norm == "hewlett packard"
    assert parsed.name_norm == ""


def test_name_field_replaces_the_text_part_of_q():
    parsed = parse(LookupQuery(q=VALID_EAN, name="Kabel USB czarny 2x"))
    assert parsed.gtin14 == VALID_EAN14
    assert parsed.color == "black"
    assert parsed.pack_qty == 2
    assert "kabel" in parsed.name_norm


def test_attrs_win_over_values_extracted_from_the_name():
    query = LookupQuery(q="Koszulka czarna 2x", attrs=Attrs(color="bialy", pack_qty=6, weight=Decimal("0.2")))
    parsed = parse(query)
    assert parsed.color == "white"
    assert parsed.pack_qty == 6
    assert parsed.weight == Decimal("0.2")


def test_mpn_and_sku_are_normalised_the_same_way_as_the_fingerprint():
    parsed = parse(LookupQuery(mpn=" gsr-12v-15 ", sku=" SKU-1 "))
    assert parsed.mpn_norm == "GSR12V15"
    assert parsed.sku == "SKU-1"


def test_empty_query_is_rejected_by_the_schema():
    with pytest.raises(ValidationError):
        LookupQuery()


def test_scope_and_limit_are_bounded_by_the_schema():
    with pytest.raises(ValidationError):
        LookupQuery(q="x", limit=21)
    with pytest.raises(ValidationError):
        LookupQuery(q="x", scope=["everything"])
