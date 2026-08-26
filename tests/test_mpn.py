# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import pytest

from django_lookup.normalize.mpn import MpnResult, normalize_mpn


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("GSR 12V-35", MpnResult("GSR12V35", "GSR12V")),
        ("gsr12v-35", MpnResult("GSR12V35", "GSR12V")),
        ("GSR-12V-35-BLK", MpnResult("GSR12V35BLK", "GSR12V35")),
        ("ABC/123.45", MpnResult("ABC12345", "ABC12345")),
        ("  abc 123  ", MpnResult("ABC123", "ABC123")),
        # leading zeros stripped only when >= 4 chars remain
        ("0001234", MpnResult("1234", "1234")),
        ("000123", MpnResult("000123", "000123")),
        ("0012", MpnResult("0012", "0012")),
        ("00AB-CD12", MpnResult("ABCD12", "00AB")),
        ("0001234-XYZ", MpnResult("1234XYZ", "1234")),
        # no suffix -> loose == strict
        ("X1", MpnResult("X1", "X1")),
        ("12345", MpnResult("12345", "12345")),
        # suffix only / dangling separators
        ("-BLK", MpnResult("BLK", "BLK")),
        ("ABC-", MpnResult("ABC", "ABC")),
        ("a-b-c", MpnResult("ABC", "AB")),
        ("", MpnResult("", "")),
        (None, MpnResult("", "")),
        ("   ", MpnResult("", "")),
    ],
)
def test_normalize_mpn(raw, expected):
    assert normalize_mpn(raw) == expected
