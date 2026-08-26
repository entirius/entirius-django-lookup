# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Provider over `auth.User` rows — the only model the standalone test settings offer as a sender.

It stands in for a real catalog: `first_name` is the product name, a deleted user is an item the
provider no longer serves (exactly what linking a SourceProduct does in atlas).
"""

from collections.abc import Iterator
from datetime import datetime

from django.contrib.auth.models import User

from django_lookup.providers.base import BasicData, ProviderItem


def _to_item(user: User) -> ProviderItem:
    return ProviderItem(ref=user.username, name_by_lang={"en": user.first_name}, updated_at=user.date_joined)


def iter_items(since: datetime | None = None) -> Iterator[ProviderItem]:
    queryset = User.objects.all() if since is None else User.objects.filter(date_joined__gte=since)
    for user in queryset.order_by("id").iterator(chunk_size=500):
        yield _to_item(user)


def get_item(ref: str) -> ProviderItem:
    user = User.objects.filter(username=ref).first()
    if user is None:
        raise LookupError(f"unknown ref {ref!r}")
    return _to_item(user)


def basic(ref: str) -> BasicData:
    return BasicData(ref=ref, name=get_item(ref).name_by_lang["en"])


def detail_url(ref: str) -> str:
    return f"/users/{ref}/"


def signal_specs() -> list[dict]:
    return [
        {"model": "auth.User", "signal": "post_save", "ref": lambda user: user.username, "watch": ["first_name"]},
        {"model": "auth.User", "signal": "post_delete", "ref": lambda user: user.username},
    ]
