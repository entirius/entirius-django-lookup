# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The image layer's only stateful side: fetch a picture, hash it, embed it, write the columns.

Two callers. The catalog side (`embed_refs`, from the Celery task) walks fingerprints and fills
`phash / image_sha1 / image_vec / vec_model`. The query side (`prepare_query`) does the same
work in-process for one uploaded picture and never stores anything — the bytes are gone when the
response is written (cms-search §API shape).

Degradation is the rule, not the exception (notes §Remote GPU): a dead embedding backend leaves the
hashes in place and the vector NULL, so the next run retries and the search keeps its pHash evidence.
"""

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from django_lookup.constants import MAX_REMOTE_IMAGE_BYTES, REMOTE_IMAGE_TIMEOUT_S
from django_lookup.embedding.base import EmbeddingError
from django_lookup.embedding.factory import current_model_id, get_embedding_provider
from django_lookup.models import Fingerprint
from django_lookup.providers.registry import get_provider
from django_lookup.security import url_guard
from django_lookup.services import image_prep
from django_lookup.settings import embed_allowed_hosts

logger = logging.getLogger("process")

# Columns this module owns; a provider refresh never touches them (fingerprint_service._UPSERT_FIELDS).
IMAGE_FIELDS = ["phash", "image_sha1", "image_vec", "vec_model", "modified_at"]
_REMOTE_SCHEMES = ("http://", "https://")


@dataclass(frozen=True)
class QueryImage:
    """The query picture reduced to evidence. `degraded` means the vector leg is missing right now."""

    phash: int
    model_id: str
    vector: list[float] | None = None
    degraded: bool = False


@dataclass(frozen=True)
class _Prepared:
    row: Fingerprint
    sha1: str
    phash: int
    data: bytes


def prepare_query(data: bytes) -> QueryImage:
    """Hash locally, embed over HTTP. Raises `ValueError` when the bytes are not a usable image."""
    image = image_prep.load_and_crop(data)
    phash = image_prep.perceptual_hash(image)
    vector = _embed([image_prep.encode(image)])[0]
    return QueryImage(phash=phash, model_id=current_model_id(), vector=vector, degraded=vector is None)


def fetch_remote(url: str, *, allowed_hosts: Iterable[str] = ()) -> bytes:
    """Download an image referenced by URL through the SSRF guard. Raises `ValueError`.

    `allowed_hosts` defaults to empty — the caller opts in. The two callers carry different trust:
    `_load` (catalog/worker path) reads a provider-owned URL and passes `LOOKUP_EMBED_ALLOWED_HOSTS`,
    the same operator-configured hole the embedding client (`embedding/transport.py`) uses for its
    in-network host. `_query_image` (`lookup_service.py`) hands this a REQUEST-supplied `image_url`
    from an admin-JWT caller — punching that same hole there would let any admin token use this
    endpoint as a blind GET oracle against the allowlisted internal hosts, past the private-IP check.
    One caller's allowlist must never leak into the other's.
    """
    fetched = url_guard.safe_get(
        url, timeout=REMOTE_IMAGE_TIMEOUT_S, cap=MAX_REMOTE_IMAGE_BYTES, allowed_hosts=allowed_hosts
    )
    if not fetched.content_type.lower().startswith("image/"):
        raise ValueError(f"not an image: Content-Type {fetched.content_type!r}")
    return fetched.content


def embed_refs(kind: str, refs: list[str]) -> int:
    """Hash and embed the main image of every ref. Returns how many fingerprint rows were written.

    A ref whose picture is unchanged *and* already embedded by the current model is skipped. With
    the image layer disabled there is no current model, so every run re-hashes — the price of
    keeping pHash evidence available without a backend.
    """
    model_id = current_model_id()
    prepared = [
        item for row in Fingerprint.objects.filter(kind=kind, ref__in=list(refs)) if (item := _prepare(row, model_id))
    ]
    if not prepared:
        return 0
    vectors = _embed([item.data for item in prepared])
    for item, vector in zip(prepared, vectors, strict=True):
        _apply(item, vector, model_id)
    Fingerprint.objects.bulk_update([item.row for item in prepared], IMAGE_FIELDS)
    return len(prepared)


def _prepare(row: Fingerprint, model_id: str) -> _Prepared | None:
    """Everything before the embedding call; None when there is nothing (new) to do for this row."""
    if not (source := _image_source(row)):
        return None
    if (data := _safe(row, _load, source)) is None:
        return None
    if (sha1 := image_prep.digest(data)) == row.image_sha1 and row.vec_model == model_id:
        return None
    if (image := _safe(row, image_prep.load_and_crop, data)) is None:
        return None
    phash = image_prep.perceptual_hash(image)
    return _Prepared(row=row, sha1=sha1, phash=phash, data=image_prep.encode(image))


def _apply(item: _Prepared, vector: list[float] | None, model_id: str) -> None:
    """Hashes always; the vector only when the backend answered — a NULL vector is retried next run."""
    item.row.phash, item.row.image_sha1 = item.phash, item.sha1
    if vector is not None:
        item.row.image_vec, item.row.vec_model = vector, model_id


def _embed(images: list[bytes]) -> list[list[float] | None]:
    """One vector per image, or a list of None — the caller degrades, it never propagates the failure."""
    try:
        return [result.vector for result in get_embedding_provider().embed_images(images)]
    except EmbeddingError as exc:
        logger.warning("lookup: image embedding unavailable (%s) — hashes only", exc)
        return [None] * len(images)


def _image_source(row: Fingerprint) -> str:
    try:
        return get_provider(row.kind).get_item(row.ref).image_path_or_url or ""
    except LookupError:
        return ""


def _load(source: str) -> bytes:
    """A remote URL goes through the SSRF guard; anything else is a local path the catalog owns.

    `source` is provider-owned (catalog data), never request-supplied, so the operator-configured
    `LOOKUP_EMBED_ALLOWED_HOSTS` hole is safe to use here — see `fetch_remote`'s docstring.
    """
    if source.startswith(_REMOTE_SCHEMES):
        return fetch_remote(source, allowed_hosts=embed_allowed_hosts())
    return _read_local(source)


def _read_local(path: str) -> bytes:
    local = Path(path)
    if local.stat().st_size > MAX_REMOTE_IMAGE_BYTES:
        raise ValueError(f"local image exceeds the cap of {MAX_REMOTE_IMAGE_BYTES} bytes: {path}")
    return local.read_bytes()


def _safe(row: Fingerprint, step: Callable, argument):
    """Image work is best-effort: a broken or unreachable picture skips the row, never the batch."""
    try:
        return step(argument)
    except (OSError, ValueError) as exc:
        logger.warning("lookup: %s failed for %s:%s — %s", step.__name__, row.kind, row.ref, exc)
        return None
