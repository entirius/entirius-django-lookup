# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Bulk paths over the providers: fill the table once (backfill), repair drift (reconcile).

Both stream the provider generators in batches — a catalog is never materialised. Reconcile keeps
one set of refs per kind in memory (the price of a set difference); it is the repair path for lost
signals, not a hot loop.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from itertools import islice
from types import ModuleType

from django_lookup.constants import IMAGE_TASK_BATCH
from django_lookup.models import Fingerprint
from django_lookup.providers.registry import get_provider, get_providers
from django_lookup.services.fingerprint_service import build_fingerprint, upsert_fingerprints
from django_lookup.tasks import embed_fingerprint_images

DEFAULT_BATCH = 500


@dataclass(frozen=True)
class ReconcileResult:
    created: int
    deleted: int


def _batches(items, size: int) -> Iterator[list]:
    stream = iter(items)  # iter(): a provider returning a list must not restart islice forever
    while batch := list(islice(stream, size)):
        yield batch


def _resolve(kind: str | None) -> dict[str, ModuleType]:
    return {kind: get_provider(kind)} if kind else get_providers()


def backfill(kind: str | None = None, since: datetime | None = None, batch: int = DEFAULT_BATCH) -> dict[str, int]:
    """Upsert every item of every (or one) kind. Returns kind -> rows written."""
    written = {}
    for name, provider in _resolve(kind).items():
        written[name] = sum(
            upsert_fingerprints([build_fingerprint(item, name) for item in chunk])
            for chunk in _batches(provider.iter_items(since), batch)
        )
    return written


def reconcile(kind: str | None = None, batch: int = DEFAULT_BATCH) -> dict[str, ReconcileResult]:
    """Create rows for items that have none, delete rows whose item is gone. Idempotent."""
    return {name: _reconcile_kind(name, provider, batch) for name, provider in _resolve(kind).items()}


def _reconcile_kind(kind: str, provider: ModuleType, batch: int) -> ReconcileResult:
    known = set(Fingerprint.objects.filter(kind=kind).values_list("ref", flat=True))
    created, seen = 0, set()
    for chunk in _batches(provider.iter_items(), batch):
        seen.update(item.ref for item in chunk)
        created += upsert_fingerprints([build_fingerprint(i, kind) for i in chunk if i.ref not in known])
    return ReconcileResult(created=created, deleted=_delete_stale(kind, known - seen, batch))


def _delete_stale(kind: str, refs: set[str], batch: int) -> int:
    stale = iter(sorted(refs))
    return sum(Fingerprint.objects.filter(kind=kind, ref__in=chunk).delete()[0] for chunk in _batches(stale, batch))


def enqueue_images(kind: str | None = None, batch: int = IMAGE_TASK_BATCH) -> dict[str, int]:
    """Hand every fingerprint's picture to the `lookup` queue. Returns kind -> refs enqueued.

    Rows, not provider items: the fingerprints already exist and the task skips whatever it has
    already hashed and embedded, so a repeated run costs one image fetch per row and no vectors.
    """
    enqueued = {}
    for name in _resolve(kind):
        refs = Fingerprint.objects.filter(kind=name).values_list("ref", flat=True).iterator()
        enqueued[name] = _enqueue_images(name, refs, batch)
    return enqueued


def _enqueue_images(kind: str, refs: Iterator[str], batch: int) -> int:
    total = 0
    for chunk in _batches(refs, batch):
        embed_fingerprint_images.delay(kind, chunk)
        total += len(chunk)
    return total
