# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Freshness signals end-to-end: a catalog write reaches the fingerprint row through the task."""

import pytest
from django.contrib.auth.models import User

from django_lookup.models import Fingerprint
from django_lookup.services import fingerprint_service

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("wired_provider", "eager_celery")]


def _save(callbacks, username: str, name: str) -> User:
    with callbacks(execute=True):
        user, _ = User.objects.update_or_create(username=username, defaults={"first_name": name})
    return user


def test_save_creates_the_fingerprint(django_capture_on_commit_callbacks):
    _save(django_capture_on_commit_callbacks, "sku-1", "Bosch Akkuschrauber")

    fingerprint = Fingerprint.objects.get(kind="pim_product", ref="sku-1")
    assert (fingerprint.name_norm, fingerprint.brand_norm) == ("akkuschrauber", "bosch")


def test_rename_refreshes_the_existing_row(django_capture_on_commit_callbacks):
    _save(django_capture_on_commit_callbacks, "sku-2", "Bosch Akkuschrauber")
    _save(django_capture_on_commit_callbacks, "sku-2", "Bosch Winkelschleifer")

    assert Fingerprint.objects.filter(kind="pim_product", ref="sku-2").count() == 1
    assert Fingerprint.objects.get(kind="pim_product", ref="sku-2").name_norm == "winkelschleifer"


def test_item_the_provider_stops_serving_loses_its_row(django_capture_on_commit_callbacks):
    user = _save(django_capture_on_commit_callbacks, "sku-3", "Bosch Akkuschrauber")

    with django_capture_on_commit_callbacks(execute=True):
        user.delete()

    assert not Fingerprint.objects.filter(kind="pim_product", ref="sku-3").exists()


def test_unchanged_watched_fields_do_not_enqueue(django_capture_on_commit_callbacks, monkeypatch):
    from django_lookup import signals

    user = _save(django_capture_on_commit_callbacks, "sku-5", "Bosch Akkuschrauber")
    calls = []
    monkeypatch.setattr(signals.refresh_fingerprint, "delay", lambda *args: calls.append(args))

    with django_capture_on_commit_callbacks(execute=True):
        user.last_name = "touched, but not watched"
        user.save()
    assert calls == []

    with django_capture_on_commit_callbacks(execute=True):
        user.first_name = "Bosch Winkelschleifer"
        user.save()
    assert calls == [("pim_product", "sku-5")]


def test_a_broker_failure_never_breaks_the_catalog_write(django_capture_on_commit_callbacks, monkeypatch):
    from django_lookup import signals

    def boom(*args):
        raise RuntimeError("broker down")

    monkeypatch.setattr(signals.refresh_fingerprint, "delay", boom)
    with django_capture_on_commit_callbacks(execute=True):
        User.objects.create(username="sku-broker", first_name="Bosch Akkuschrauber")

    assert User.objects.filter(username="sku-broker").exists()  # the write survived


def test_disconnect_stops_the_wiring():
    from django_lookup import signals

    signals.disconnect()
    User.objects.create(username="sku-4", first_name="Bosch Akkuschrauber")
    assert not Fingerprint.objects.filter(ref="sku-4").exists()
    signals.connect()


def test_a_refreshed_row_gets_its_picture_re_checked(monkeypatch, wired_provider):
    """The image task follows the text refresh — a changed photo is the only thing that costs work."""
    from django_lookup import tasks

    enqueued = []
    monkeypatch.setattr(tasks.embed_fingerprint_images, "delay", lambda kind, refs: enqueued.append((kind, refs)))
    User.objects.create(username="SKU-IMG", first_name="Wiertarka")
    assert tasks.refresh_fingerprint("pim_product", "SKU-IMG") == fingerprint_service.REFRESHED
    assert enqueued == [("pim_product", ["SKU-IMG"])]


def test_a_vanished_row_costs_no_image_work(monkeypatch, wired_provider):
    from django_lookup import tasks

    enqueued = []
    monkeypatch.setattr(tasks.embed_fingerprint_images, "delay", lambda kind, refs: enqueued.append((kind, refs)))
    assert tasks.refresh_fingerprint("pim_product", "SKU-GONE") == fingerprint_service.ABSENT
    assert enqueued == []


def test_refresh_fingerprints_refreshes_every_ref(wired_provider):
    """Batched twin of refresh_fingerprint — a thin loop over the same single-ref service call
    (checkpoint: atlas full sync enqueues in chunks instead of one publish per row)."""
    from django_lookup import tasks

    User.objects.create(username="SKU-A", first_name="Wiertarka")
    User.objects.create(username="SKU-B", first_name="Szlifierka")

    assert tasks.refresh_fingerprints("pim_product", ["SKU-A", "SKU-B"]) == 2

    assert Fingerprint.objects.get(kind="pim_product", ref="SKU-A").name_norm == "wiertarka"
    assert Fingerprint.objects.get(kind="pim_product", ref="SKU-B").name_norm == "szlifierka"


def test_refresh_fingerprints_tolerates_a_stale_ref(wired_provider):
    """A ref the provider no longer serves is dropped (mirrors refresh_fingerprint's DELETED/ABSENT
    outcomes) instead of the whole batch failing for one stale row."""
    from django_lookup import tasks

    User.objects.create(username="SKU-LIVE", first_name="Wiertarka")
    Fingerprint.objects.create(kind="pim_product", ref="SKU-DEAD")  # stale — no matching User

    assert tasks.refresh_fingerprints("pim_product", ["SKU-LIVE", "SKU-DEAD"]) == 1

    assert Fingerprint.objects.filter(kind="pim_product", ref="SKU-LIVE").exists()
    assert not Fingerprint.objects.filter(kind="pim_product", ref="SKU-DEAD").exists()


def test_refresh_fingerprints_batches_the_image_task_once(monkeypatch, wired_provider):
    """One embed_fingerprint_images publish for the whole refreshed set, not one per ref — the
    same fan-out this task exists to avoid must not reappear one layer down."""
    from django_lookup import tasks

    enqueued = []
    monkeypatch.setattr(tasks.embed_fingerprint_images, "delay", lambda kind, refs: enqueued.append((kind, refs)))
    User.objects.create(username="SKU-A", first_name="Wiertarka")
    User.objects.create(username="SKU-B", first_name="Szlifierka")

    tasks.refresh_fingerprints("pim_product", ["SKU-A", "SKU-B"])

    assert enqueued == [("pim_product", ["SKU-A", "SKU-B"])]


def test_a_compensating_post_save_still_refreshes(django_capture_on_commit_callbacks):
    """Bulk writers (PIM's `update_product`) re-send `post_save` by hand — no `pre_save` runs.

    The watched-column filter must not swallow that send: its snapshot belongs to the row's own
    save, so comparing against it would drop exactly the refresh the compensation exists to make.
    """
    from django.db.models.signals import post_save

    _save(django_capture_on_commit_callbacks, "sku-6", "Bosch Akkuschrauber")
    User.objects.filter(username="sku-6").update(first_name="Bosch Winkelschleifer")
    fresh = User.objects.get(username="sku-6")  # never went through pre_save — like PIM's product

    with django_capture_on_commit_callbacks(execute=True):
        post_save.send(sender=User, instance=fresh, created=False, raw=False, update_fields=None)

    assert Fingerprint.objects.get(kind="pim_product", ref="sku-6").name_norm == "winkelschleifer"
