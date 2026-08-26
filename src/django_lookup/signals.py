# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Freshness signals — wired from the senders each provider declares.

A provider module may expose `signal_specs() -> list[dict]`; each spec is plain data, so PIM and
atlas declare what matters without importing this module:

    {"model": "<app_label>.<Model>", "signal": "post_save" | "post_delete", "ref": <callable>,
     "watch": ["<field>", ...]}   # optional, post_save only

`ref(instance)` returns the fingerprint ref to refresh, or None when the row is irrelevant (a
`ProductAttribute` for a feature nobody fingerprints, a picture that is not MAIN, …). Handlers stay
cheap: resolve a ref, enqueue `refresh_fingerprint` after commit — the task does the provider call.
`watch` narrows a post_save spec to rows whose listed fields actually changed (one pre-save query of
those columns); a create always enqueues. Senders that are saved in bulk (atlas imports) must use it.
It is wrong, though, for a sender a provider re-sends by hand to compensate for `bulk_create`
(PIM's `product_service.update_product`): that send skips `pre_save`, so the snapshot it compares
against is the row's own earlier save and the refresh gets dropped.
"""

import logging
from collections.abc import Iterator

from django.apps import apps
from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_save

from django_lookup.providers import registry
from django_lookup.settings import get_providers as configured_providers
from django_lookup.tasks import refresh_fingerprint

logger = logging.getLogger("process")

_SIGNALS = {"post_save": post_save, "post_delete": post_delete}


_WATCH_ATTR = "_lookup_watched"


def connect() -> None:
    """Connect every provider-declared sender. Called from LookupConfig.ready()."""
    for kind, spec, model in _specs():
        if watch := spec.get("watch"):
            pre_save.connect(_snapshot(watch), sender=model, weak=False, dispatch_uid=_uid(kind, spec) + ":pre")
        _SIGNALS[spec["signal"]].connect(
            _handler(kind, spec["ref"], spec.get("watch")),
            sender=model,
            weak=False,  # the handler is a closure — a weak reference would be collected at once
            dispatch_uid=_uid(kind, spec),
        )


def disconnect() -> None:
    """Undo `connect()`. Only re-pointed providers (tests) ever need it."""
    for kind, spec, model in _specs():
        if spec.get("watch"):
            pre_save.disconnect(sender=model, dispatch_uid=_uid(kind, spec) + ":pre")
        _SIGNALS[spec["signal"]].disconnect(sender=model, dispatch_uid=_uid(kind, spec))


def _uid(kind: str, spec: dict) -> str:
    return f"lookup:{kind}:{spec['model']}:{spec['signal']}"


def _specs() -> Iterator[tuple[str, dict, type]]:
    """(kind, spec, sender model) for every declared sender that this host can actually resolve."""
    for kind in configured_providers():
        try:
            provider = registry.get_provider(kind)
            for spec in getattr(provider, "signal_specs", list)():
                yield kind, spec, apps.get_model(spec["model"])
        except (ImportError, LookupError):
            # A configured provider whose module or model is absent (a host that installs lookup but
            # not that catalog) must not take the boot down — that kind simply stays unwired.
            logger.warning("lookup: no freshness signals for kind=%s — provider unavailable", kind, exc_info=True)


def _snapshot(watch: list[str]):
    """pre_save: remember the stored values of the watched columns (None for a new row)."""

    def handler(sender, instance, **kwargs):  # noqa: ARG001 — Django signal signature
        stored = sender._default_manager.filter(pk=instance.pk).values(*watch).first() if instance.pk else None
        setattr(instance, _WATCH_ATTR, stored)

    return handler


def _changed(instance, watch: list[str] | None, created: bool) -> bool:
    if not watch or created:
        return True
    before = getattr(instance, _WATCH_ATTR, None)
    return before is None or any(before[field] != getattr(instance, field) for field in watch)


def _enqueue(kind: str, ref: str) -> None:
    """Enqueue the refresh, swallowing broker failures.

    This module is optional: a catalog write must never fail because the lookup queue is unreachable.
    (The platform's other on_commit enqueues are unguarded — a stale fingerprint is recoverable with
    `lookup_reconcile`, a failed product save is not.)
    """
    try:
        refresh_fingerprint.delay(kind, ref)
    except Exception:  # noqa: BLE001 — broker/transport errors are not this module's business to classify
        logger.warning("lookup: could not enqueue refresh for %s:%s — run lookup_reconcile", kind, ref, exc_info=True)


def _handler(kind: str, resolve_ref, watch: list[str] | None = None):
    def handler(sender, instance, **kwargs):  # noqa: ARG001 — Django signal signature
        if kwargs.get("raw") or not _changed(instance, watch, kwargs.get("created", False)):
            return
        if not (ref := resolve_ref(instance)):
            return
        transaction.on_commit(lambda: _enqueue(kind, ref))

    return handler
