# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""`manage.py lookup_eval` end to end: never raises, always prints the report sections."""

import io

import pytest
from django.core.management import call_command
from django.test import override_settings

from django_lookup.enums import FingerprintKind
from django_lookup.models import DedupDecision
from django_lookup.providers import registry
from django_lookup.providers.base import ProviderItem
from django_lookup.services.fingerprint_service import build_fingerprint, upsert_fingerprints

pytestmark = pytest.mark.django_db

GTIN = "5901234123457"


@pytest.fixture
def two_kind_provider():
    from tests import fake_provider as module

    registry.clear_cache()
    module.reset()
    with override_settings(
        LOOKUP_PROVIDERS={"pim_product": "tests.fake_provider", "atlas_source_product": "tests.fake_provider"}
    ):
        yield module
    module.reset()
    registry.clear_cache()


def _seed(provider) -> None:
    for kind, ref in ((FingerprintKind.PIM_PRODUCT, "PIM-1"), (FingerprintKind.ATLAS_SOURCE_PRODUCT, "ATL-1")):
        item = ProviderItem(ref=ref, name_by_lang={"en": "Kestrel Kettle"}, gtin=GTIN, brand="Kestrel")
        provider.add(item)
        upsert_fingerprints([build_fingerprint(item, kind)])


def _write_pairs(path, *rows: str) -> None:
    header = "query_kind,query_ref,candidate_kind,candidate_ref,label,why\n"
    path.write_text(header + "\n".join(rows) + ("\n" if rows else ""))


def test_command_prints_the_report_and_exits_clean(two_kind_provider, tmp_path):
    _seed(two_kind_provider)
    path = tmp_path / "pairs.csv"
    _write_pairs(path, "pim_product,PIM-1,atlas_source_product,ATL-1,match,exact dup")
    out = io.StringIO()
    call_command("lookup_eval", f"--pairs={path}", "--thresholds=45,75", stdout=out)
    output = out.getvalue()
    assert "pairs: 1 (skipped 0, not retrieved 0)" in output
    assert "positives: match" in output
    assert "positives: match+variant" in output
    assert "recall@50 (name blocking):" in output
    assert "recall@20 (image blocking):" in output
    assert "confusion (label -> engine decision):" in output


def test_command_prints_skip_reasons(two_kind_provider, tmp_path):
    path = tmp_path / "pairs.csv"
    _write_pairs(
        path,
        "pim_product,NOPE,atlas_source_product,ATL-1,match,stale",
        "pim_product,PIM-1,atlas_source_product,ATL-1,maybe,bad label",
    )
    out = io.StringIO()
    call_command("lookup_eval", f"--pairs={path}", stdout=out)
    output = out.getvalue()
    assert "pairs: 2 (skipped 2, not retrieved 0)" in output
    assert "skipped by reason:" in output
    assert "bad label=1" in output
    assert "stale ref=1" in output


def test_command_never_raises_on_a_missing_csv(tmp_path):
    out = io.StringIO()
    call_command("lookup_eval", f"--pairs={tmp_path / 'missing.csv'}", stdout=out)
    assert "cannot read --pairs" in out.getvalue()


def test_command_guards_evaluate_and_never_raises(monkeypatch, tmp_path):
    from django_lookup.management.commands import lookup_eval as command_module

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(command_module.eval_service, "evaluate", _boom)
    path = tmp_path / "pairs.csv"
    _write_pairs(path)
    out = io.StringIO()
    call_command("lookup_eval", f"--pairs={path}", stdout=out)
    assert "evaluate() failed" in out.getvalue()


def test_command_leaves_no_decision_log_by_default(two_kind_provider, tmp_path):
    _seed(two_kind_provider)
    path = tmp_path / "pairs.csv"
    _write_pairs(path, "pim_product,PIM-1,atlas_source_product,ATL-1,match,exact dup")
    call_command("lookup_eval", f"--pairs={path}", stdout=io.StringIO())
    assert DedupDecision.objects.count() == 0


def test_command_log_decisions_flag_writes_the_rows(two_kind_provider, tmp_path):
    _seed(two_kind_provider)
    path = tmp_path / "pairs.csv"
    _write_pairs(path, "pim_product,PIM-1,atlas_source_product,ATL-1,match,exact dup")
    call_command("lookup_eval", f"--pairs={path}", "--log-decisions", stdout=io.StringIO())
    assert DedupDecision.objects.count() == 1


def test_command_image_only_skips_the_threshold_sweep(two_kind_provider, tmp_path):
    _seed(two_kind_provider)
    path = tmp_path / "pairs.csv"
    _write_pairs(path, "pim_product,PIM-1,atlas_source_product,ATL-1,match,exact dup")
    out = io.StringIO()
    call_command("lookup_eval", f"--pairs={path}", "--image-only", stdout=out)
    output = out.getvalue()
    assert "threshold" not in output
    assert "recall@20 (image blocking): n/a" in output
