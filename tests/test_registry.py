# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import pytest
from django.test import override_settings

from django_lookup.providers import registry
from django_lookup.providers.base import ProviderItem


def test_get_provider_by_dotted_path(fake_provider):
    fake_provider.add(ProviderItem(ref="1", name_by_lang={"en": "Thing"}))
    provider = registry.get_provider("fake")
    assert provider is fake_provider
    assert provider.get_item("1").ref == "1"
    assert provider.basic("1").name == "Thing"
    assert provider.detail_url("1") == "/fake/1"
    assert [i.ref for i in provider.iter_items()] == ["1"]


def test_unknown_kind_raises(fake_provider):
    with pytest.raises(ValueError, match="no lookup provider for kind='nope'"):
        registry.get_provider("nope")


def test_unknown_ref_raises(fake_provider):
    with pytest.raises(LookupError):
        fake_provider.get_item("missing")


def test_get_providers_lists_registry(fake_provider):
    assert list(registry.get_providers()) == ["fake"]


def test_cache_is_keyed_by_path(fake_provider):
    with override_settings(LOOKUP_PROVIDERS={"a": "tests.fake_provider"}):
        first = registry.get_provider("a")
    with override_settings(LOOKUP_PROVIDERS={"a": "django_lookup.providers.base"}):
        second = registry.get_provider("a")
    assert first is not second
