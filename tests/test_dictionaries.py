# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import pytest

from django_lookup import dictionaries
from django_lookup.normalize.text import fold, unaccent


@pytest.mark.parametrize("name", dictionaries.NAMES)
def test_keys_are_already_folded(name):
    data = dictionaries._load(name)
    keys = list(data) if isinstance(data, dict) else data
    assert [k for k in keys if k != fold(k)] == []
    assert len(set(keys)) == len(keys)


def test_unknown_dictionary_raises():
    with pytest.raises(ValueError, match="unknown dictionary"):
        dictionaries._load("nope")


def test_views_are_immutable():
    with pytest.raises(TypeError):
        dictionaries.colors()["x"] = "y"  # type: ignore[index]
    assert isinstance(dictionaries.stopwords(), frozenset)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Żółć", "zolc"),
        ("Straße", "strasse"),
        ("Kärcher", "karcher"),
        ("1.5 l", "1.5 l"),
        ("v2.", "v2"),
        (".5", "5"),
        ("1.5.2", "1.5.2"),
        ("Black & Decker", "black & decker"),
        ("A+B", "a+b"),
        ("  a ,  b  ", "a b"),
    ],
)
def test_fold(raw, expected):
    assert fold(raw) == expected


def test_unaccent_keeps_case():
    assert unaccent("Łódź") == "Lodz"
