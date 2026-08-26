# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import pytest

from django_lookup.normalize.brand import normalize_brand


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("HP", "hp"),
        ("Hewlett-Packard GmbH", "hp"),
        ("Hewlett Packard", "hp"),
        ("hewlett   packard", "hp"),
        ("HP Inc.", "hp"),
        ("Samsung Electronics Co., Ltd.", "samsung"),
        ("LG Electronics", "lg"),
        ("Robert Bosch GmbH", "bosch"),
        ("BOSCH", "bosch"),
        ("Kärcher", "karcher"),
        ("Karcher AG", "karcher"),
        ("De'Longhi", "delonghi"),
        ("DeWALT", "dewalt"),
        ("Black & Decker", "black decker"),
        ("Black+Decker Inc", "black decker"),
        ("Procter and Gamble", "p&g"),
        ("Acme Sp. z o.o.", "acme"),
        ("Żywiec S.A.", "zywiec"),
        ("Unknown Brand Ltd", "unknown brand"),
        ("s.Oliver", "s oliver"),
        ("Zywiec SA", "zywiec"),
        ("Salon A", "salon a"),
        ("", ""),
        (None, ""),
        ("   ", ""),
    ],
)
def test_normalize_brand(raw, expected):
    assert normalize_brand(raw) == expected
