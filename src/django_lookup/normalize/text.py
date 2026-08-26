# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Shared text primitives for the normalizers — pure, no Django."""

import re
import unicodedata

_NON_WORD = re.compile(r"[^\w&+.]+", re.UNICODE)
# Dots survive only inside numbers ("2.0ah"); elsewhere they are separators.
_DOT_NOT_DECIMAL = re.compile(r"(?<!\d)\.|\.(?!\d)")
_SPACES = re.compile(r"\s+")


def unaccent(value: str) -> str:
    """ASCII fold: 'żółć' -> 'zolc', 'ß' -> 'ss'."""
    value = value.replace("ß", "ss").replace("ł", "l").replace("Ł", "L")
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def fold(value: str) -> str:
    """Lowercase + unaccent + punctuation to spaces + collapsed whitespace."""
    text = _DOT_NOT_DECIMAL.sub(" ", _NON_WORD.sub(" ", unaccent(value).lower()))
    return _SPACES.sub(" ", text).strip()
