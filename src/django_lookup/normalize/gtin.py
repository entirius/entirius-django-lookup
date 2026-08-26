# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""GTIN normalisation (research r02 §2): gtin14 key, GS1 check digit, trust + multipack flags."""

import re
from dataclasses import dataclass

from stdnum import ean

_DIGITS = re.compile(r"\D+")
_VALID_LENGTHS = frozenset({8, 12, 13, 14})
# Restricted-circulation prefixes on the GTIN-13 form (GS1 2 / 20-29; UPC number systems 2 and 4 = 02 / 04).
_UNTRUSTED_PREFIXES = ("2", "02", "04")


@dataclass(frozen=True)
class GtinResult:
    gtin14: str  # "" when invalid
    valid: bool
    trusted: bool  # valid AND not a restricted-circulation prefix
    related_unit: bool  # GTIN-14 with indicator 1-8: case/multipack of the base item, related not equal


INVALID = GtinResult(gtin14="", valid=False, trusted=False, related_unit=False)


def normalize_gtin(raw: str | None) -> GtinResult:
    if not raw:
        return INVALID
    digits = _DIGITS.sub("", raw)
    if len(digits) not in _VALID_LENGTHS or not digits.strip("0") or not ean.is_valid(digits):
        return INVALID
    gtin14 = digits.zfill(14)
    related_unit = gtin14[0] in "12345678"
    gtin13 = gtin14[1:]
    trusted = not related_unit and not gtin13.startswith(_UNTRUSTED_PREFIXES)
    return GtinResult(gtin14=gtin14, valid=True, trusted=trusted, related_unit=related_unit)
