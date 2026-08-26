# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Query -> ParsedQuery: the same normalisation the fingerprints went through.

Blocking and scoring only ever see a ParsedQuery, so a query and a catalog row are always
compared on identical keys (research r02 §2). Free text is mined for a GTIN first; what is
left goes through the name normaliser (brand / colour / size / pack fall out of it).
"""

import re
from dataclasses import dataclass, field
from decimal import Decimal

from django_lookup.dictionaries import colors
from django_lookup.normalize import GtinResult, normalize_brand, normalize_gtin, normalize_mpn, normalize_name
from django_lookup.normalize.text import fold
from django_lookup.schemas.requests.lookup import Attrs, LookupQuery

# A run of 8-14 digits anywhere in the text; validated as a GTIN before being taken out.
_DIGIT_RUN = re.compile(r"(?<!\d)\d{8,14}(?!\d)")
_PHYSICAL_ATTRS = ("weight", "width", "height", "deep")


@dataclass(frozen=True)
class ParsedQuery:
    """Normalised query keys. Mirrors the Fingerprint columns — same names, same normalisers."""

    gtin14: str = ""
    gtin_trusted: bool = False
    brand_norm: str = ""
    mpn_norm: str = ""
    sku: str = ""
    name_norm: str = ""
    name_tokens: list[str] = field(default_factory=list)
    pack_qty: int | None = None
    color: str = ""
    size: str = ""
    weight: Decimal | None = None
    width: Decimal | None = None
    height: Decimal | None = None
    deep: Decimal | None = None


def _extract_gtin(text: str) -> tuple[str, GtinResult]:
    """Return (text without the GTIN, GtinResult). Only a digit run that validates is taken out."""
    for match in _DIGIT_RUN.finditer(text):
        gtin = normalize_gtin(match.group())
        if gtin.valid:
            return f"{text[: match.start()]} {text[match.end() :]}".strip(), gtin
    return text, normalize_gtin(None)


def _color(raw: str | None) -> str:
    """Same mapping the name normaliser applies: folded token -> english colour."""
    folded = fold(raw or "")
    return colors().get(folded, folded)


def parse(query: LookupQuery) -> ParsedQuery:
    """Explicit fields always win over what `q` yields — the caller knows more than the parser."""
    text, text_gtin = _extract_gtin(query.q or "")
    gtin = normalize_gtin(query.ean) if query.ean else text_gtin
    brand = normalize_brand(query.brand)
    name = normalize_name(query.name or text, brand=brand)
    attrs = query.attrs or Attrs()
    return ParsedQuery(
        gtin14=gtin.gtin14,
        gtin_trusted=gtin.trusted,
        brand_norm=brand or name.brand,
        mpn_norm=normalize_mpn(query.mpn).strict,
        sku=(query.sku or "").strip(),
        name_norm=name.name_norm,
        name_tokens=name.tokens,
        pack_qty=attrs.pack_qty or name.pack_qty,
        color=_color(attrs.color) or name.color,
        size=(attrs.size or "").strip().lower() or name.size,
        **{attr: getattr(attrs, attr) for attr in _PHYSICAL_ATTRS},
    )
