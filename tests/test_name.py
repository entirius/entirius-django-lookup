# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import pytest

from django_lookup.normalize.name import NameResult, normalize_name


def _r(name_norm, *, brand="", pack_qty=None, color="", size="", tokens=None):
    return NameResult(
        name_norm=name_norm,
        tokens=sorted(set(name_norm.split())) if tokens is None else tokens,
        brand=brand,
        pack_qty=pack_qty,
        color=color,
        size=size,
    )


@pytest.mark.parametrize(
    ("raw", "brand", "expected"),
    [
        # units: decimal comma/dot, spacing, case
        ("Woda mineralna 1,5 l", "", _r("woda mineralna 1.5l")),
        ("Woda mineralna 1.5L", "", _r("woda mineralna 1.5l")),
        ("Shampoo 500 ml", "", _r("shampoo 500ml")),
        ("Shampoo 500ml", "", _r("shampoo 500ml")),
        ("Kabel 3 m", "", _r("kabel 3m")),
        ("Akku 2,0 Ah 18 V", "", _r("akku 2.0ah 18v")),
        ("SSD 1 TB", "", _r("ssd 1tb")),
        ("Mąka 1 kg", "", _r("maka 1kg")),
        ("Powerbank 20000 mAh", "", _r("powerbank 20000mah")),
        # stopwords pl/en/de
        ("New original case for iPhone", "", _r("case iphone")),
        ("Nowy oryginalny pokrowiec dla laptopa", "", _r("pokrowiec laptopa")),
        ("Neu Hülle für Handy mit Band", "", _r("hulle handy band")),
        ("The Best Pan with Lid", "", _r("best pan lid")),
        # brand strip: given brand
        ("Bosch GSR 12V", "bosch", _r("gsr 12v", brand="bosch")),
        ("GSR 12V Bosch", "bosch", _r("gsr 12v", brand="bosch")),
        ("Black Decker drill", "black decker", _r("drill", brand="black decker")),
        # brand strip: extracted from leading tokens via dictionary
        ("Bosch GSR 12V", "", _r("gsr 12v", brand="bosch")),
        ("Hewlett Packard LaserJet", "", _r("laserjet", brand="hewlett packard")),
        ("Black Decker drill", "", _r("drill", brand="black decker")),
        ("Drill Bosch GSR", "", _r("drill bosch gsr")),  # not leading -> kept
        ("Acme widget", "", _r("acme widget")),  # unknown brand kept
        # pack qty
        ("Baterie AA 2x", "", _r("baterie aa", pack_qty=2)),
        ("Baterie AA 4 x", "", _r("baterie aa", pack_qty=4)),
        ("Socks 3-pack", "", _r("socks", pack_qty=3)),
        ("Socks 3pack", "", _r("socks", pack_qty=3)),
        ("Socken 6er Pack", "", _r("socken", pack_qty=6)),
        ("Zestaw 2 kubki", "", _r("kubki", pack_qty=2)),
        ("Kubki 4 szt", "", _r("kubki", pack_qty=4)),
        ("Cups 12 pcs", "", _r("cups", pack_qty=12)),
        ("Tassen 4 Stk", "", _r("tassen", pack_qty=4)),
        ("Kabel 2 m", "", _r("kabel 2m")),  # unit, not pack
        ("Ręcznik 50 x 100 cm", "", _r("recznik 50 x 100cm")),  # dimension, not pack
        ("Plakat 30 x 40", "", _r("plakat 30 x 40")),
        ("Baterie 99999 x", "", _r("baterie 99999 x")),  # absurd qty: not a pack
        # colour pl/en/de -> canonical english
        ("Koszulka czarna", "", _r("koszulka", color="black")),
        ("T-shirt black", "", _r("t shirt", color="black")),
        ("Hemd schwarz", "", _r("hemd", color="black")),
        ("Kubek biały", "", _r("kubek", color="white")),
        ("Becher weiß", "", _r("becher", color="white")),
        ("Etui granatowe", "", _r("etui granatowe")),  # declension not in dictionary -> kept
        ("Torba czerwona", "", _r("torba", color="red")),
        # size
        ("Koszulka XL", "", _r("koszulka", size="xl")),
        ("Buty EU 42", "", _r("buty", size="42")),
        ("Buty rozmiar 42", "", _r("buty", size="42")),
        ("Schuhe Gr. 40", "", _r("schuhe", size="40")),
        ("Shoes size 10", "", _r("shoes", size="10")),
        ("Buty 42", "", _r("buty 42")),  # bare number is not a size
        ("Tesla Model S 2024", "", _r("tesla model s 2024")),  # bare s mid-name is not a size
        ("Galaxy S", "", _r("galaxy", size="s")),  # trailing single letter is
        ("Kurtka biała", "", _r("kurtka", color="white")),
        ("Sukienka żółta", "", _r("sukienka", color="yellow")),
        ("Koszulka M czarna", "", _r("koszulka", size="m", color="black")),
        # combined
        (
            "Bosch GSR 12V-35 Akku 2,0 Ah niebieski 2x",
            "bosch",
            _r("gsr 12v 35 akku 2.0ah", brand="bosch", color="blue", pack_qty=2),
        ),
        ("NOWY Kärcher WD 3 odkurzacz żółty", "karcher", _r("wd 3 odkurzacz", brand="karcher", color="yellow")),
        # punctuation, accents, whitespace
        ("  Żółć   / gęś  ", "", _r("zolc ges")),
        ("Kabel USB-C (2 m)", "", _r("kabel usb c 2m")),
        # empty
        ("", "", NameResult(name_norm="")),
        (None, "", NameResult(name_norm="")),
        ("   ", "", NameResult(name_norm="")),
    ],
)
def test_normalize_name(raw, brand, expected):
    assert normalize_name(raw, brand=brand) == expected


def test_tokens_are_sorted_unique():
    result = normalize_name("b x b c")
    assert result.name_norm == "b x b c"
    assert result.tokens == ["b", "c", "x"]
