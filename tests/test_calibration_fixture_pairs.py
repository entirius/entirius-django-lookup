# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Golden-pair tests (test-strategy §2) for the calibration fixture's adversarial pair classes.

These mirror index-0 of each pair class in `entirius-test-package-emporium`'s
`scripts/generate-lookup-fixtures.py` byte-for-byte (name/brand/EAN/weight literals copied from a
fresh run of that generator) — the BDD suite proves the `/check/` flow runs end to end on the real
seeded fixture, but does not assert a specific decision per pair class (test-strategy §5: "BDD
proves the flow runs, it does not tune thresholds"); that assertion belongs here, next to
`scoring.py`, so a weight/threshold change that flips one of these is caught by `pytest`, not by a
BDD run against a live seed. `exact_dup` and `variant` already have equivalents above
(`test_check_on_a_seeded_ean_decides_match`, `test_colour_variant_is_review_not_match`) — this file
adds the remaining four classes: multipack, dirty_ean, name_only, photo_lookalike.

Keep the literals here and the generator's in sync by hand; a divergence is a silent drift, not a
failure — nothing wires the two together automatically.
"""

from decimal import Decimal

import pytest

from django_lookup.enums import DecisionAuto
from django_lookup.schemas.requests.lookup import Attrs, LookupQuery
from django_lookup.services.lookup_service import check
from tests.test_lookup_service import add_product

pytestmark = pytest.mark.django_db


def test_multipack_pair_is_no_match(pim_provider):
    """generate-lookup-fixtures.py `_make_multipack(class_index, 0)`: same name text, the atlas side
    is a 3-pack (weight x3) -> `weight_conflict` keeps this out of `match`/`review`."""
    add_product(
        pim_provider, "ATL-MULTI-00", "Boreal Desk Lamp 3-pack", brand="Boreal", attrs={"weight": Decimal("1.2")}
    )
    query = LookupQuery(name="Boreal Desk Lamp", brand="Boreal", attrs=Attrs(weight=Decimal("0.4")))
    assert check(query).decision == DecisionAuto.NO_MATCH


def test_dirty_ean_pair_matches_when_the_name_corroborates(pim_provider):
    """`_make_dirty_ean(class_index, 0)`: shared GTIN and the SAME product name, only the feed's brand
    field is corrupted. A recycled barcode would change the name too, so with the identifier, the name
    and the physicals all agreeing the brand conflict is dirty data and must not cap the verdict."""
    gtin = "5900010000500"
    add_product(pim_provider, "ATL-DIRTY-00", "Boreal Kettle", gtin, brand="Cindra")
    query = LookupQuery(ean=gtin, brand="Boreal", name="Boreal Kettle")
    result = check(query)
    assert result.decision == DecisionAuto.MATCH
    assert "brand_conflict" in {reason.code for reason in result.candidates[0].reasons}  # still shown


def test_a_recycled_barcode_still_caps_at_review(pim_provider):
    """The case research r02 §4 wrote the cap for: the barcode is the ONLY thing that agrees."""
    gtin = "5900010000517"
    add_product(pim_provider, "ATL-RECYCLED-00", "Cindra Blender", gtin, brand="Cindra")
    query = LookupQuery(ean=gtin, brand="Boreal", name="Boreal Kettle")
    assert check(query).decision == DecisionAuto.REVIEW


def test_name_only_pair_is_review_without_an_identifier(pim_provider):
    """`_make_name_only(class_index, 0)`: no EAN on either side, weight within the 5 % tolerance —
    name + brand + weight agree but nothing is exact, so the verdict tops out at `review`."""
    add_product(
        pim_provider,
        "ATL-NAMEONLY-00",
        "Sundrift Office Chair edition",
        brand="Sundrift",
        attrs={"weight": Decimal("0.309")},
    )
    query = LookupQuery(name="Sundrift Office Chair Edition", brand="Sundrift", attrs=Attrs(weight=Decimal("0.3")))
    assert check(query).decision == DecisionAuto.REVIEW


def test_photo_lookalike_pair_is_no_match_on_text_alone(pim_provider):
    """`_make_photo_lookalike(class_index, 0)`: different brand/name/GTIN, only the photo template is
    shared — text-only evidence must not find it at all (the image-only guard is a separate,
    image-layer concern; see `test_image_scoring.py`'s `image_only` tests)."""
    add_product(pim_provider, "ATL-PHOTO-00", "Boreal Office Chair", "5900010000616", brand="Boreal")
    query = LookupQuery(ean="5900010000609")
    assert check(query).decision == DecisionAuto.NO_MATCH
