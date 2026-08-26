# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""`lookup_doctor` — the fail-closed handshake, and `--images` on the backfill command."""

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from django_lookup.constants import EMBEDDING_DIM
from django_lookup.embedding.base import EmbeddingError
from django_lookup.enums import FingerprintKind
from django_lookup.models import Fingerprint
from django_lookup.services import backfill_service
from tests import fake_embedding

pytestmark = pytest.mark.django_db

HEALTHY = ["lookup_doctor", "--skip-worker"]


def _run(*args) -> str:
    from io import StringIO

    out = StringIO()
    call_command(*args, stdout=out)
    return out.getvalue()


def test_a_healthy_image_layer_exits_clean():
    output = _run(*HEALTHY)
    assert "[FAIL]" not in output
    assert f"dim={EMBEDDING_DIM}" in output


def test_the_coverage_line_counts_what_search_can_actually_use():
    Fingerprint.objects.create(kind=FingerprintKind.PIM_PRODUCT, ref="A", phash=1)
    Fingerprint.objects.create(kind=FingerprintKind.PIM_PRODUCT, ref="B")
    assert "fingerprints: 2 total, 1 hashed, 0 embedded" in _run(*HEALTHY)


def test_a_dead_backend_fails_the_doctor():
    """The point of the command: a broken image layer must break here, not in the results."""
    fake_embedding.fail_with(EmbeddingError)
    with pytest.raises(CommandError, match="provider"):
        _run(*HEALTHY)


def test_a_dimension_that_contradicts_the_column_fails_the_doctor():
    settings_value = {"provider": "tests.fake_embedding.FakeEmbeddingProvider", "dim": EMBEDDING_DIM + 1}
    with override_settings(LOOKUP_EMBEDDING=settings_value), pytest.raises(CommandError, match="dimension"):
        _run(*HEALTHY)


def test_the_image_layer_on_with_no_provider_is_a_misconfiguration():
    with override_settings(LOOKUP_EMBEDDING={"provider": "none"}), pytest.raises(CommandError, match="settings"):
        _run(*HEALTHY)


def test_the_layer_switched_off_is_healthy_by_definition():
    with override_settings(LOOKUP_IMAGE_ENABLED=False):
        assert "[FAIL]" not in _run(*HEALTHY)


def test_backfill_images_enqueues_every_fingerprint_in_batches(monkeypatch, pim_provider):
    """`--images` never rebuilds the text columns — it only hands refs to the `lookup` queue."""
    enqueued = []
    monkeypatch.setattr(
        backfill_service.embed_fingerprint_images, "delay", lambda kind, refs: enqueued.append((kind, refs))
    )
    for index in range(backfill_service.IMAGE_TASK_BATCH + 3):
        Fingerprint.objects.create(kind=FingerprintKind.PIM_PRODUCT, ref=f"SKU-{index}")
    assert _run("lookup_backfill", "--images").strip() == f"pim_product: {backfill_service.IMAGE_TASK_BATCH + 3}"
    assert [len(refs) for _kind, refs in enqueued] == [backfill_service.IMAGE_TASK_BATCH, 3]


def test_backfill_images_refuses_an_unknown_kind(pim_provider):
    with pytest.raises(CommandError):
        _run("lookup_backfill", "--images", "--kind", "nope")
