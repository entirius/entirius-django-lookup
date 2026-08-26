# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Lazy provider registry — the module's only indirection to PIM / atlas.

Cache keyed by dotted path (not by kind): re-pointing a kind in tests must miss the cache,
re-importing the same path must not.
"""

import importlib
from types import ModuleType

from django_lookup.settings import get_providers as configured_providers

_PROVIDER_CACHE: dict[str, ModuleType] = {}


def get_provider(kind: str) -> ModuleType:
    registry = configured_providers()
    dotted_path = registry.get(kind)
    if not dotted_path:
        raise ValueError(
            f"no lookup provider for kind={kind!r} (known: {sorted(registry)}); register it in settings.LOOKUP_PROVIDERS"
        )
    if dotted_path not in _PROVIDER_CACHE:
        _PROVIDER_CACHE[dotted_path] = importlib.import_module(dotted_path)
    return _PROVIDER_CACHE[dotted_path]


def get_providers() -> dict[str, ModuleType]:
    return {kind: get_provider(kind) for kind in configured_providers()}


def clear_cache() -> None:
    """Drop the import cache. Test hook — production never re-points a live path."""
    _PROVIDER_CACHE.clear()
