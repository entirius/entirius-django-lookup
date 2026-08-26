# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from django_lookup.enums import FingerprintKind
from django_lookup.models import Fingerprint
from django_lookup.providers.base import ProviderItem
from django_lookup.services.fingerprint_service import build_fingerprint

FULL = ProviderItem(
    ref="42",
    gtin="5901234123457",
    brand="Robert Bosch GmbH",
    mpn="GSR 12V-35",
    name_by_lang={"en": "Bosch GSR 12V-35 drill blue 2x", "pl": "Bosch GSR 12V-35 wiertarka niebieska 2x"},
    attrs={"weight": "1.2", "width": 10, "height": Decimal("20.5"), "deep": "bad"},
    image_path_or_url="/media/p.jpg",
    updated_at=datetime(2026, 8, 22, tzinfo=UTC),
)


def test_full_item_fills_every_normalised_column():
    fp = build_fingerprint(FULL, FingerprintKind.PIM_PRODUCT)
    assert isinstance(fp, Fingerprint) and fp.pk is None
    assert (fp.kind, fp.ref) == ("pim_product", "42")
    assert (fp.gtin14, fp.gtin_trusted) == ("05901234123457", True)
    assert fp.brand_norm == "bosch"
    assert fp.mpn_norm == "GSR12V35"
    assert fp.name_norm == "gsr 12v 35 wiertarka"  # pl preferred over en
    assert fp.name_tokens == ["12v", "35", "gsr", "wiertarka"]
    assert (fp.pack_qty, fp.color, fp.size) == (2, "blue", "")
    assert (fp.weight, fp.width, fp.height, fp.deep) == (Decimal("1.2"), Decimal("10"), Decimal("20.5"), None)
    assert fp.source_updated_at == FULL.updated_at
    assert fp.image_vec is None and fp.phash is None


def test_missing_fields_become_null_or_blank():
    fp = build_fingerprint(ProviderItem(ref="x"), FingerprintKind.ATLAS_SOURCE_PRODUCT)
    assert (fp.gtin14, fp.gtin_trusted, fp.brand_norm, fp.mpn_norm, fp.name_norm) == ("", False, "", "", "")
    assert fp.name_tokens == []
    assert (fp.pack_qty, fp.weight, fp.width, fp.height, fp.deep, fp.source_updated_at) == (None,) * 6


def test_brand_extracted_from_name_when_source_has_none():
    fp = build_fingerprint(ProviderItem(ref="y", name_by_lang={"de": "Bosch Akkuschrauber"}), "pim_product")
    assert (fp.brand_norm, fp.name_norm) == ("bosch", "akkuschrauber")


def test_invalid_gtin_is_not_stored():
    fp = build_fingerprint(ProviderItem(ref="z", gtin="5901234123450"), "pim_product")
    assert (fp.gtin14, fp.gtin_trusted) == ("", False)


@pytest.mark.django_db
def test_fingerprint_saves_and_enforces_unique_kind_ref():
    build_fingerprint(FULL, "pim_product").save()
    assert Fingerprint.objects.count() == 1
    from django.db import IntegrityError

    with pytest.raises(IntegrityError):
        build_fingerprint(FULL, "pim_product").save()
