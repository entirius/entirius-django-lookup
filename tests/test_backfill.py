# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Backfill / reconcile over a provider, and the two management commands."""

from datetime import UTC, datetime
from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from django_lookup.models import Fingerprint
from django_lookup.providers import registry
from django_lookup.providers.base import ProviderItem
from django_lookup.services import backfill_service

pytestmark = pytest.mark.django_db


@pytest.fixture
def catalog(settings):
    """`tests.fake_provider` registered under a real kind, preloaded with five items."""
    from tests import fake_provider

    settings.LOOKUP_PROVIDERS = {"pim_product": "tests.fake_provider"}
    registry.clear_cache()
    fake_provider.reset()
    for index in range(5):
        fake_provider.add(
            ProviderItem(
                ref=f"sku-{index}",
                name_by_lang={"en": f"Bosch drill {index}"},
                updated_at=datetime(2026, 8, index + 1, tzinfo=UTC),
            )
        )
    yield fake_provider
    fake_provider.reset()
    registry.clear_cache()


def _run(command: str, *args) -> str:
    out = StringIO()
    call_command(command, *args, stdout=out)
    return out.getvalue()


def test_backfill_writes_every_item_and_is_idempotent(catalog):
    assert backfill_service.backfill(batch=2) == {"pim_product": 5}
    assert backfill_service.backfill(batch=2) == {"pim_product": 5}

    assert Fingerprint.objects.filter(kind="pim_product").count() == 5


def test_backfill_since_skips_older_items(catalog):
    written = backfill_service.backfill(since=datetime(2026, 8, 4, tzinfo=UTC))

    assert written == {"pim_product": 2}
    assert sorted(Fingerprint.objects.values_list("ref", flat=True)) == ["sku-3", "sku-4"]


def test_backfill_refreshes_a_stale_row_without_duplicating_it(catalog):
    backfill_service.backfill()
    catalog.add(ProviderItem(ref="sku-1", name_by_lang={"en": "Bosch hammer"}))

    backfill_service.backfill()

    assert Fingerprint.objects.filter(ref="sku-1").count() == 1
    assert Fingerprint.objects.get(ref="sku-1").name_norm == "hammer"


def test_reconcile_creates_missing_rows_and_drops_vanished_ones(catalog):
    Fingerprint.objects.create(kind="pim_product", ref="gone", name_norm="ghost")

    result = backfill_service.reconcile()["pim_product"]

    assert (result.created, result.deleted) == (5, 1)
    assert sorted(Fingerprint.objects.values_list("ref", flat=True)) == [f"sku-{i}" for i in range(5)]


def test_reconcile_is_a_no_op_the_second_time(catalog):
    backfill_service.backfill()

    result = backfill_service.reconcile()["pim_product"]

    assert (result.created, result.deleted) == (0, 0)


def test_backfill_command_reports_counts_per_kind(catalog):
    assert _run("lookup_backfill", "--batch", "2") == "pim_product: 5\n"


def test_reconcile_command_reports_counts_per_kind(catalog):
    assert _run("lookup_reconcile") == "pim_product: created=5 deleted=0\n"


def test_backfill_command_rejects_an_unknown_kind(catalog):
    with pytest.raises(CommandError, match="no lookup provider"):
        _run("lookup_backfill", "--kind", "nope")


def test_backfill_command_rejects_a_malformed_since(catalog):
    with pytest.raises(CommandError, match="ISO timestamp"):
        _run("lookup_backfill", "--since", "yesterday")
