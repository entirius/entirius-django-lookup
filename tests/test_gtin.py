# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import pytest

from django_lookup.normalize.gtin import INVALID, GtinResult, normalize_gtin

VALID_EAN13 = "5901234123457"
VALID_EAN8 = "96385074"
VALID_UPC = "036000291452"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # canonical forms: EAN-13 and its zero-padded GTIN-14 are the same key
        (VALID_EAN13, GtinResult("05901234123457", True, True, False)),
        ("05901234123457", GtinResult("05901234123457", True, True, False)),
        # EAN-8 and UPC-A pad to 14
        (VALID_EAN8, GtinResult("00000096385074", True, True, False)),
        (VALID_UPC, GtinResult("00036000291452", True, True, False)),
        # cleanup: whitespace, separators, letters
        (" 590-1234 123457 ", GtinResult("05901234123457", True, True, False)),
        ("EAN: 5901234123457", GtinResult("05901234123457", True, True, False)),
        ("5901 2341 2345 7", GtinResult("05901234123457", True, True, False)),
        ("\t5901234123457\n", GtinResult("05901234123457", True, True, False)),
        # GTIN-14 with indicator 1-8 = case / multipack -> related, not equal, never trusted
        ("15901234123454", GtinResult("15901234123454", True, False, True)),
        ("85901234123453", GtinResult("85901234123453", True, False, True)),
        # untrusted prefixes (restricted circulation): valid but not trusted
        ("2001234567893", GtinResult("02001234567893", True, False, False)),
        ("2901234567896", GtinResult("02901234567896", True, False, False)),
        ("223456789019", GtinResult("00223456789019", True, False, False)),
        ("423456789013", GtinResult("00423456789013", True, False, False)),
        # bad check digit
        ("5901234123450", INVALID),
        ("5901234123458", INVALID),
        ("96385075", INVALID),
        # wrong length
        ("", INVALID),
        (None, INVALID),
        ("123", INVALID),
        ("59012341234", INVALID),
        ("590123412345678", INVALID),
        ("abc", INVALID),
        ("5901234 12345", INVALID),
        ("0000000000000", INVALID),
    ],
)
def test_normalize_gtin(raw, expected):
    assert normalize_gtin(raw) == expected


def test_result_is_immutable():
    with pytest.raises(AttributeError):
        normalize_gtin(VALID_EAN13).gtin14 = "x"
