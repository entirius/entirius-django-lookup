# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Product name normalisation (pl/en/de): units, stopwords, brand strip, pack/colour/size extraction."""

import re
from dataclasses import dataclass, field

from django_lookup.dictionaries import colors, known_brands, stopwords, units
from django_lookup.normalize.text import fold, unaccent

_UNITS = "|".join(sorted(units(), key=len, reverse=True))
# "1,5 l" / "1.5 L" / "500 ml" -> "1.5l" / "500ml"
_NUMBER_UNIT = re.compile(rf"(?<![\w.])(\d+)(?:[,.](\d+))?\s*({_UNITS})(?![\w])", re.IGNORECASE)
_PACK_PATTERNS = (
    re.compile(r"(?<!\w)(\d{1,4})\s*x(?!\w)(?!\s*\d)"),  # 2x, 2 x — but not "50 x 100" (a dimension)
    re.compile(r"(?<!\w)(\d{1,4})\s*-?\s*(?:pack|pak|er pack|er-pack)(?!\w)"),  # 3-pack, 3pack, 6er pack
    re.compile(r"(?<!\w)(?:zestaw|set|komplet)\s+(\d{1,4})(?!\w)"),  # zestaw 2
    re.compile(r"(?<!\w)(\d{1,4})\s*(?:szt|pcs|stk|stuck)(?!\w)"),  # 4 szt, 4 pcs, 4 stk
)
_SIZE_PATTERNS = (
    re.compile(r"(?<!\w)(?:eu|rozmiar|rozm|size|gr|gr\.|grosse)\s*(\d{2}(?:[.,]5)?)(?!\w)"),  # eu 40, rozmiar 42
    re.compile(r"(?<!\w)(xxs|xs|xl|xxl|xxxl|3xl|4xl)(?!\w)"),  # unambiguous apparel sizes anywhere
)
_MAX_BRAND_TOKENS = 3
# Bare s/m/l count as a size only as the last token once colour is gone ("Koszulka M czarna"); "Model S 2024" keeps its s.
_BARE_SIZES = frozenset({"s", "m", "l"})


@dataclass(frozen=True)
class NameResult:
    name_norm: str  # folded, units normalised, stopwords/brand/pack/colour/size removed, token order kept
    tokens: list[str] = field(default_factory=list)  # sorted unique tokens for Jaccard
    brand: str = ""  # brand stripped from the name (given or extracted)
    pack_qty: int | None = None
    color: str = ""
    size: str = ""


def _normalise_units(text: str) -> str:
    def repl(m: re.Match) -> str:
        integer, fraction, unit = m.group(1), m.group(2), m.group(3).lower()
        number = f"{integer}.{fraction}" if fraction else integer
        return f"{number}{unit}"

    return _NUMBER_UNIT.sub(repl, text)


def _extract(text: str, patterns: tuple[re.Pattern, ...]) -> tuple[str, str | None]:
    for pattern in patterns:
        if match := pattern.search(text):
            return pattern.sub(" ", text, count=1), match.group(1)
    return text, None


def _strip_brand(tokens: list[str], brand: str) -> tuple[list[str], str]:
    if brand:
        brand_tokens = brand.split()
        n = len(brand_tokens)
        for i in range(len(tokens) - n + 1):
            if tokens[i : i + n] == brand_tokens:
                return tokens[:i] + tokens[i + n :], brand
        return tokens, brand
    brands = known_brands()
    for n in range(min(_MAX_BRAND_TOKENS, len(tokens)), 0, -1):
        candidate = " ".join(tokens[:n])
        if candidate in brands:
            return tokens[n:], candidate
    return tokens, ""


def _extract_color(tokens: list[str]) -> tuple[list[str], str]:
    palette = colors()
    for i, token in enumerate(tokens):
        if token in palette:
            return tokens[:i] + tokens[i + 1 :], palette[token]
    return tokens, ""


def normalize_name(raw: str | None, brand: str = "") -> NameResult:
    """`brand` is the already-normalised brand (normalize_brand) when the source knows it."""
    if not raw or not raw.strip():
        return NameResult(name_norm="")
    text = _normalise_units(unaccent(raw).lower())
    text, pack = _extract(text, _PACK_PATTERNS)
    text, size = _extract(text, _SIZE_PATTERNS)
    tokens = [t for t in fold(text).split() if t not in stopwords()]
    tokens, brand_found = _strip_brand(tokens, brand)
    tokens, color = _extract_color(tokens)
    if not size and tokens and tokens[-1] in _BARE_SIZES:
        tokens, size = tokens[:-1], tokens[-1]
    return NameResult(
        name_norm=" ".join(tokens),
        tokens=sorted(set(tokens)),
        brand=brand_found,
        pack_qty=int(pack) if pack else None,
        color=color,
        size=size.replace(",", ".") if size else "",
    )
