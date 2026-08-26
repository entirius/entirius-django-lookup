# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Celery tasks — thin wrappers; the work lives in the services.

The host worker must consume `constants.CELERY_QUEUE`, and it has no autoreload: restart it after
any change in this file or in what it calls.
"""

from celery import shared_task

from django_lookup.constants import CELERY_QUEUE
from django_lookup.embedding.factory import get_embedding_provider
from django_lookup.services import fingerprint_service, image_service
from django_lookup.settings import image_enabled


@shared_task(queue=CELERY_QUEUE, ignore_result=True)
def refresh_fingerprint(kind: str, ref: str) -> str:
    """Rebuild one row from its provider. Enqueued by the freshness signals (signals.py).

    A rebuilt row keeps its image columns, so the picture is re-checked in its own task — a changed
    photo is the only thing that makes it do any work (`image_sha1`).
    """
    outcome = fingerprint_service.refresh(kind, ref)
    if outcome == fingerprint_service.REFRESHED and image_enabled():
        embed_fingerprint_images.delay(kind, [ref])
    return outcome


@shared_task(queue=CELERY_QUEUE, ignore_result=True)
def refresh_fingerprints(kind: str, refs: list[str]) -> int:
    """Batched twin of `refresh_fingerprint` — a thin loop over the same single-ref service call,
    same idempotent semantics (a ref the provider no longer serves loses its row). One publish per
    up-to-`REFRESH_TASK_BATCH` refs instead of one per row, for callers importing many rows at once
    (atlas full sync — see `import_service._enqueue_lookup_refresh`). Tolerates a stale ref (already
    refreshed/deleted by a previous run) exactly like `refresh_fingerprint` does.

    Returns how many refs the provider still served (and were rebuilt) — a stale/deleted ref is not
    counted, mirroring how `refresh_fingerprint` reports `DELETED`/`ABSENT` for its own single ref.
    """
    refreshed = [ref for ref in refs if fingerprint_service.refresh(kind, ref) == fingerprint_service.REFRESHED]
    if refreshed and image_enabled():
        embed_fingerprint_images.delay(kind, refreshed)
    return len(refreshed)


@shared_task(queue=CELERY_QUEUE, ignore_result=True)
def embed_fingerprint_images(kind: str, refs: list[str]) -> int:
    """Hash and embed the main image of each ref; returns how many rows were written."""
    return image_service.embed_refs(kind, refs)


@shared_task(queue=CELERY_QUEUE)
def probe_embedding() -> dict:
    """`lookup_doctor` handshake run *inside the worker* — web reachability proves nothing about it."""
    info = get_embedding_provider().info()
    return {"model_id": info.model_id, "dim": info.dim}
