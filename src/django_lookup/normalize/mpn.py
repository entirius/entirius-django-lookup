# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""MPN normalisation: strict key + loose key without the trailing variant suffix."""

import re
from dataclasses import dataclass

_SEPARATORS = re.compile(r"[\s\-/.]+")
_MIN_LEN_FOR_ZERO_STRIP = 4


@dataclass(frozen=True)
class MpnResult:
    strict: str  # uppercase, separators dropped, leading zeros stripped
    loose: str  # same, computed on the part before the last '-' (colour/region suffix dropped)


def _strict(value: str) -> str:
    key = _SEPARATORS.sub("", value.upper())
    stripped = key.lstrip("0")
    return stripped if len(stripped) >= _MIN_LEN_FOR_ZERO_STRIP else key


def normalize_mpn(raw: str | None) -> MpnResult:
    if not raw or not raw.strip():
        return MpnResult(strict="", loose="")
    value = raw.strip()
    strict = _strict(value)
    head, sep, _tail = value.rpartition("-")
    loose = _strict(head) if sep and head.strip() else strict
    return MpnResult(strict=strict, loose=loose)
