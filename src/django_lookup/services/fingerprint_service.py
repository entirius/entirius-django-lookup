# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Fingerprint writes: build one from a ProviderItem, upsert rows, refresh a single ref.

The only place that maps a ProviderItem -> columns, and the only place that writes the table.
"""

from decimal import Decimal, InvalidOperation

from django_lookup.enums import FingerprintKind
from django_lookup.models import Fingerprint
from django_lookup.normalize import normalize_brand, normalize_gtin, normalize_mpn, normalize_name
from django_lookup.providers.base import ProviderItem
from django_lookup.providers.registry import get_provider

# `refresh` outcomes — the task branches on them, so they are constants, not literals.
REFRESHED = "refreshed"
DELETED = "deleted"
ABSENT = "absent"

# Preferred name language order; falls back to any non-empty translation.
_NAME_LANGS = ("pl", "en", "de")
_DIMENSION_ATTRS = ("weight", "width", "height", "deep")
# Columns an upsert overwrites. The image layer (plan 05) owns phash/image_vec/vec_model/
# image_sha1 and computes them from the fetched picture — a provider refresh must not blank them.
_UPSERT_FIELDS = [
    "gtin14",
    "gtin_trusted",
    "brand_norm",
    "mpn_norm",
    "name_norm",
    "name_tokens",
    "pack_qty",
    "color",
    "size",
    "weight",
    "width",
    "height",
    "deep",
    "source_updated_at",
    "modified_at",
]


def pick_name(name_by_lang: dict[str, str]) -> str:
    """Preferred-language name, falling back to any non-empty translation (eval_service reuses this
    so the calibration query picks the same name build_fingerprint would have)."""
    for lang in _NAME_LANGS:
        if name := name_by_lang.get(lang):
            return name
    return next((name for name in name_by_lang.values() if name), "")


def _decimal_or_none(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def build_fingerprint(item: ProviderItem, kind: str) -> Fingerprint:
    if kind not in FingerprintKind.values:
        raise ValueError(f"unknown fingerprint kind {kind!r} (known: {FingerprintKind.values})")
    gtin = normalize_gtin(item.gtin)
    brand = normalize_brand(item.brand)
    name = normalize_name(pick_name(item.name_by_lang), brand=brand)
    dims = {attr: _decimal_or_none(item.attrs.get(attr)) for attr in _DIMENSION_ATTRS}
    return Fingerprint(
        kind=kind,
        ref=item.ref,
        gtin14=gtin.gtin14,
        gtin_trusted=gtin.trusted,
        brand_norm=brand or name.brand,
        mpn_norm=normalize_mpn(item.mpn).strict,
        name_norm=name.name_norm,
        name_tokens=name.tokens,
        pack_qty=name.pack_qty,
        color=name.color,
        size=name.size,
        source_updated_at=item.updated_at,
        **dims,
    )


def upsert_fingerprints(fingerprints: list[Fingerprint]) -> int:
    """Insert or refresh rows by (kind, ref). Returns how many were written."""
    if not fingerprints:
        return 0
    Fingerprint.objects.bulk_create(
        fingerprints, update_conflicts=True, unique_fields=["kind", "ref"], update_fields=_UPSERT_FIELDS
    )
    return len(fingerprints)


def refresh(kind: str, ref: str) -> str:
    """Rebuild one row from its provider; an item the provider no longer serves loses its row.

    Atlas relies on that: linking a SourceProduct to a RealProduct drops it from the candidate pool,
    so `get_item` raises LookupError and the stale row goes away.
    """
    try:
        item = get_provider(kind).get_item(ref)
    except LookupError:
        deleted, _ = Fingerprint.objects.filter(kind=kind, ref=ref).delete()
        return DELETED if deleted else ABSENT
    upsert_fingerprints([build_fingerprint(item, kind)])
    return REFRESHED
