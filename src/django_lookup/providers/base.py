# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Provider protocol — how the lookup module sees a catalog without importing it.

A provider is a *module* registered by dotted path in `settings.LOOKUP_PROVIDERS` (kind -> path),
duck-typed like enrichment adapters. It must expose:

    iter_items(since: datetime | None = None) -> Iterator[ProviderItem]
    get_item(ref: str) -> ProviderItem            # raises LookupError when unknown
    basic(ref: str) -> BasicData                  # display data for API responses
    detail_url(ref: str) -> str                   # admin/CMS deep link

It may optionally also expose the batch form of the display calls (measured N+1: 20 hits cost 246
queries through the singular pair — see AGENTS.md "Display data" gotcha). `lookup_service._display`
uses them when both are present and falls back to the singular calls otherwise:

    basics(refs: list[str]) -> dict[str, BasicData]  # only the refs the provider still serves
    detail_urls(refs: list[str]) -> dict[str, str]   # only the refs the provider still serves

`basics`/`detail_urls` MUST omit unknown refs from the returned dict, MUST NOT raise `LookupError`
(or anything else) for one — that is how the singular pair signals "gone", but the batch pair has no
per-ref return slot to carry it. `lookup_service._display_batch` treats a `LookupError` raised by
either call as the whole batch implementation being broken and falls back to the singular
`basic`/`detail_url` pair (which does tolerate it), so a provider that gets this wrong degrades to
N+1 rather than 500s the request — implement it correctly and the batch form is used as intended.

PIM and atlas never import each other; each ships its own provider module (plan 03).
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class ProviderItem:
    ref: str
    gtin: str | None = None
    brand: str | None = None
    mpn: str | None = None
    name_by_lang: dict[str, str] = field(default_factory=dict)
    attrs: dict = field(default_factory=dict)
    image_path_or_url: str | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class BasicData:
    ref: str
    name: str
    brand: str = ""
    gtin: str = ""
    mpn: str = ""
    image_url: str = ""
