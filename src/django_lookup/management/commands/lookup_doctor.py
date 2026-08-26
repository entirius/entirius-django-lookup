# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Fail-closed health check of the image layer (notes §Remote GPU, obligation 1).

Someone swapping the model on the shared embedding box must break *here*, loudly, and not quietly
in the search results — mixed vectors do not raise, they just stop matching. Exit code 1 on any
failed check; the boot handshake in `apps.py` reports the same dimension mismatch as a warning only.
"""

import math
from dataclasses import dataclass

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from PIL import Image

from django_lookup.constants import EMBEDDING_DIM
from django_lookup.embedding.base import PROVIDER_NONE, EmbeddingError
from django_lookup.embedding.factory import current_model_id, dimension_mismatch, get_embedding_provider
from django_lookup.models import Fingerprint
from django_lookup.services import image_prep
from django_lookup.settings import image_enabled
from django_lookup.tasks import probe_embedding

HNSW_INDEX = "lookup_fp_image_vec_hnsw_idx"
# Black vs white through a vision tower lands far below this; a text route fed two data URLs
# that differ only in their payload returns ~1.0. The gap is wide, so the gate is deliberately
# loose — it exists to catch "not an image endpoint", not to grade embedding quality.
_MAX_PROBE_COSINE = 0.99
DEFAULT_WORKER_TIMEOUT_S = 60


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


class Command(BaseCommand):
    help = "Verify the lookup image layer: provider handshake, vector dimensions, HNSW index, worker reach."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--skip-worker", action="store_true", help="Do not probe the Celery worker.")
        parser.add_argument("--worker-timeout", type=int, default=DEFAULT_WORKER_TIMEOUT_S)

    def handle(self, *args, **options) -> None:
        checks = [_settings_check(), _column_check(), _index_check(), _provider_check(), _discrimination_check()]
        if not options["skip_worker"]:
            checks.append(_worker_check(options["worker_timeout"]))
        for check in checks:
            self.stdout.write(f"[{'ok' if check.ok else 'FAIL'}] {check.name}: {check.detail}")
        self.stdout.write(_coverage())
        if failed := [check.name for check in checks if not check.ok]:
            raise CommandError(f"image layer unhealthy: {', '.join(failed)}")


def _settings_check() -> Check:
    if not image_enabled():
        return Check("settings", True, "LOOKUP_IMAGE_ENABLED is False — the image layer is off on purpose")
    if (model := current_model_id()) == PROVIDER_NONE:
        return Check("settings", False, 'LOOKUP_IMAGE_ENABLED is True but the provider is "none"')
    return Check("settings", True, f"image layer on, vec_model={model}")


def _column_check() -> Check:
    """`LOOKUP_EMBEDDING["dim"]` against the halfvec width the migration actually created."""
    if message := dimension_mismatch():
        return Check("dimension", False, message)
    actual = _column_dimension()
    return Check(
        "dimension", actual == EMBEDDING_DIM, f"halfvec({actual}) in the database, settings say {EMBEDDING_DIM}"
    )


def _column_dimension() -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
            "WHERE attrelid = %s::regclass AND attname = 'image_vec'",
            [Fingerprint._meta.db_table],
        )
        row = cursor.fetchone()
    return int("".join(character for character in row[0] if character.isdigit())) if row else 0


def _index_check() -> Check:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_indexes WHERE indexname = %s", [HNSW_INDEX])
        present = cursor.fetchone() is not None
    return Check("hnsw_index", present, f"{HNSW_INDEX} {'present' if present else 'MISSING'}")


def _provider_check() -> Check:
    """A real embedding call from this process — what the web tier can actually do right now."""
    if not image_enabled():
        return Check("provider", True, "skipped, the image layer is off")
    try:
        info = get_embedding_provider().info()
    except EmbeddingError as exc:
        return Check("provider", False, f"unreachable from this process: {exc}")
    expected = current_model_id()
    ok = info.dim == EMBEDDING_DIM and info.model_id == expected
    return Check("provider", ok, f"answers model={info.model_id} dim={info.dim}, expected {expected}/{EMBEDDING_DIM}")


def _discrimination_check() -> Check:
    """Two unlike pictures must not come back as the same vector.

    A handshake that only reads back `model_id` and `dim` cannot tell an image endpoint from a
    text one. Point `LOOKUP_EMBEDDING["url"]` at an OpenAI-compatible *text* route and it answers
    200 with the right model and the right width — it just embedded the `data:image/jpeg;base64,`
    string instead of decoding it, and every catalog photo shares that prefix, so the whole
    catalog collapses onto one vector. Nothing raises; recall simply goes to noise.
    """
    if not image_enabled():
        return Check("discrimination", True, "skipped, the image layer is off")
    try:
        vectors = get_embedding_provider().embed_images([_black(), _white()])
    except EmbeddingError as exc:
        return Check("discrimination", False, f"could not embed the probe pair: {exc}")
    left, right = vectors[0].vector, vectors[1].vector
    similarity = _cosine(left, right)
    if similarity > _MAX_PROBE_COSINE:
        return Check(
            "discrimination",
            False,
            f"a black and a white image embed at cosine {similarity:.4f} — the backend is not "
            "reading the pictures (is the URL a text route rather than an image one?)",
        )
    return Check("discrimination", True, f"unlike images embed apart (cosine {similarity:.4f})")


def _black() -> bytes:
    return image_prep.encode(Image.new("RGB", (64, 64), (0, 0, 0)))


def _white() -> bytes:
    return image_prep.encode(Image.new("RGB", (64, 64), (255, 255, 255)))


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norms = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / norms if norms else 1.0


def _worker_check(timeout: int) -> Check:
    """The worker does the backfill; its network is not the web tier's (notes §Remote GPU, obligation 3)."""
    if not image_enabled():
        return Check("worker", True, "skipped, the image layer is off")
    try:
        answer = probe_embedding.apply_async().get(timeout=timeout)
    except Exception as exc:  # a dead broker, an idle queue and a dead backend all land here
        return Check("worker", False, f"no answer within {timeout}s ({type(exc).__name__}: {exc})")
    ok = answer["dim"] == EMBEDDING_DIM and answer["model_id"] == current_model_id()
    return Check("worker", ok, f"answers model={answer['model_id']} dim={answer['dim']}")


def _coverage() -> str:
    """How much of the catalog the image layer actually covers — the number that explains bad recall."""
    rows = Fingerprint.objects.all()
    total = rows.count()
    return (
        f"fingerprints: {total} total, {rows.filter(phash__isnull=False).count()} hashed, "
        f"{rows.filter(image_vec__isnull=False).count()} embedded, "
        f"{rows.filter(image_vec__isnull=False).exclude(vec_model=current_model_id()).count()} on another model"
    )
