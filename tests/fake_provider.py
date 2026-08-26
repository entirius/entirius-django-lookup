# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""In-memory provider implementing the protocol from django_lookup.providers.base."""

from collections.abc import Iterator
from datetime import datetime

from django_lookup.providers.base import BasicData, ProviderItem

_ITEMS: dict[str, ProviderItem] = {}


def reset() -> None:
    _ITEMS.clear()


def add(item: ProviderItem) -> None:
    _ITEMS[item.ref] = item


def iter_items(since: datetime | None = None) -> Iterator[ProviderItem]:
    for item in _ITEMS.values():
        if since is None or (item.updated_at and item.updated_at >= since):
            yield item


def get_item(ref: str) -> ProviderItem:
    try:
        return _ITEMS[ref]
    except KeyError as exc:
        raise LookupError(f"unknown ref {ref!r}") from exc


def basic(ref: str) -> BasicData:
    item = get_item(ref)
    return BasicData(
        ref=ref,
        name=next(iter(item.name_by_lang.values()), ""),
        brand=item.brand or "",
        gtin=item.gtin or "",
        mpn=item.mpn or "",
        image_url=item.image_path_or_url or "",
    )


def detail_url(ref: str) -> str:
    return f"/fake/{ref}"


def basics(refs: list[str]) -> dict[str, BasicData]:
    """Batch form of `basic` — exercises the `lookup_service._display` fast path in tests."""
    return {ref: basic(ref) for ref in refs if ref in _ITEMS}


def detail_urls(refs: list[str]) -> dict[str, str]:
    """Batch form of `detail_url` — a ref the provider no longer serves is simply absent."""
    return {ref: detail_url(ref) for ref in refs if ref in _ITEMS}
