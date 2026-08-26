# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Normaliser dictionaries (JSON, pl/en/de) — loaded once per process, exposed as immutable, pre-shaped views.

Keys are stored already folded (lowercase, ASCII, no punctuation) because every lookup happens after
normalize.text.fold(); tests/test_dictionaries.py guards that invariant.
"""

import json
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from types import MappingProxyType

_DIR = Path(__file__).parent
NAMES = ("brand_aliases", "colors", "legal_forms", "stopwords", "units")


@cache
def _load(name: str) -> dict | list:
    if name not in NAMES:
        raise ValueError(f"unknown dictionary {name!r} (known: {NAMES})")
    with (_DIR / f"{name}.json").open(encoding="utf-8") as fh:
        return json.load(fh)


@cache
def brand_aliases() -> Mapping[str, str]:
    return MappingProxyType(_load("brand_aliases"))


@cache
def known_brands() -> frozenset[str]:
    aliases = brand_aliases()
    return frozenset(aliases) | frozenset(aliases.values())


@cache
def colors() -> Mapping[str, str]:
    return MappingProxyType(_load("colors"))


@cache
def legal_forms() -> tuple[str, ...]:
    """Folded legal-form phrases, longest first ("sp z o o" before "sa")."""
    return tuple(sorted(_load("legal_forms"), key=len, reverse=True))


@cache
def stopwords() -> frozenset[str]:
    return frozenset(_load("stopwords"))


@cache
def units() -> tuple[str, ...]:
    return tuple(_load("units"))
