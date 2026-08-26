# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""`services.eval_service`: CSV parsing, leg-isolated blocking recall, the score/threshold sweeps,
confusion, skip-reason accounting and attrs pass-through."""

from decimal import Decimal

import pytest
from django.test import override_settings

from django_lookup.enums import DecisionAuto, DecisionSource, FingerprintKind
from django_lookup.models import DedupDecision, Fingerprint
from django_lookup.providers import registry
from django_lookup.providers.base import ProviderItem
from django_lookup.services import eval_service
from django_lookup.services.fingerprint_service import build_fingerprint, upsert_fingerprints
from tests.vectors import similar_to, unit_vector

pytestmark = pytest.mark.django_db

GTIN = "5901234123457"


@pytest.fixture
def two_kind_provider():
    """`tests.fake_provider` registered under both kinds — the eval CSV crosses catalogs."""
    from tests import fake_provider as module

    registry.clear_cache()
    module.reset()
    with override_settings(
        LOOKUP_PROVIDERS={"pim_product": "tests.fake_provider", "atlas_source_product": "tests.fake_provider"}
    ):
        yield module
    module.reset()
    registry.clear_cache()


def _add(provider, kind: str, ref: str, name: str, **fields) -> Fingerprint:
    item = ProviderItem(ref=ref, name_by_lang={"en": name}, **fields)
    provider.add(item)
    row = build_fingerprint(item, kind)
    upsert_fingerprints([row])
    return Fingerprint.objects.get(kind=kind, ref=ref)


def _row(**overrides) -> eval_service.PairRow:
    base = {
        "query_kind": FingerprintKind.PIM_PRODUCT,
        "query_ref": "PIM-1",
        "candidate_kind": FingerprintKind.ATLAS_SOURCE_PRODUCT,
        "candidate_ref": "ATL-1",
        "label": "match",
        "why": "test",
    }
    return eval_service.PairRow(**{**base, **overrides})


def test_load_pairs_parses_the_csv(tmp_path):
    path = tmp_path / "pairs.csv"
    path.write_text(
        "query_kind,query_ref,candidate_kind,candidate_ref,label,why\n"
        "pim_product,PIM-1,atlas_source_product,ATL-1,match,exact dup\n"
    )
    rows = eval_service.load_pairs(str(path))
    assert rows == [
        eval_service.PairRow(
            query_kind="pim_product",
            query_ref="PIM-1",
            candidate_kind="atlas_source_product",
            candidate_ref="ATL-1",
            label="match",
            why="exact dup",
        )
    ]


def test_load_pairs_drops_blank_rows(tmp_path):
    path = tmp_path / "pairs.csv"
    path.write_text("query_kind,query_ref,candidate_kind,candidate_ref,label,why\n,,,,,\n")
    assert eval_service.load_pairs(str(path)) == []


def test_load_pairs_tolerates_a_byte_order_mark(tmp_path):
    path = tmp_path / "pairs.csv"
    path.write_bytes(
        "﻿query_kind,query_ref,candidate_kind,candidate_ref,label,why\n"
        "pim_product,PIM-1,atlas_source_product,ATL-1,match,bom\n".encode()
    )
    rows = eval_service.load_pairs(str(path))
    assert rows[0].query_kind == "pim_product"  # a BOM-prefixed first header must not corrupt it


def test_evaluate_skips_a_row_whose_query_ref_is_unknown(two_kind_provider):
    report = eval_service.evaluate([_row(query_ref="NOT-THERE")], thresholds=[75])
    assert report.total == 1
    assert report.skipped == 1
    assert report.skip_reasons == {eval_service.STALE_REF: 1}


def test_a_bad_label_is_skipped_and_counted(two_kind_provider):
    report = eval_service.evaluate([_row(label="maybe")], thresholds=[75])
    assert report.skip_reasons == {eval_service.BAD_LABEL: 1}


def test_an_unregistered_kind_is_skipped_as_unknown_kind(two_kind_provider):
    with override_settings(LOOKUP_PROVIDERS={"pim_product": "tests.fake_provider"}):
        report = eval_service.evaluate([_row(query_kind="atlas_source_product")], thresholds=[75])
    assert report.skip_reasons == {eval_service.UNKNOWN_KIND: 1}


def test_gtin_exact_pair_is_a_true_positive_at_a_low_threshold(two_kind_provider):
    _add(two_kind_provider, FingerprintKind.PIM_PRODUCT, "PIM-1", "Kestrel Kettle", gtin=GTIN, brand="Kestrel")
    _add(two_kind_provider, FingerprintKind.ATLAS_SOURCE_PRODUCT, "ATL-1", "Kestrel Kettle", gtin=GTIN, brand="Kestrel")
    report = eval_service.evaluate([_row(label="match")], thresholds=[45, 75])
    by_threshold = {t.threshold: t for t in report.thresholds}
    assert by_threshold[45].tp == 1
    assert by_threshold[75].tp == 1  # gtin_exact + brand_equal alone clears 75
    assert report.not_retrieved == 0


def test_a_negative_pair_below_threshold_is_a_true_negative_and_not_retrieved(two_kind_provider):
    _add(two_kind_provider, FingerprintKind.PIM_PRODUCT, "PIM-1", "Kestrel Kettle", gtin=GTIN, brand="Kestrel")
    _add(two_kind_provider, FingerprintKind.ATLAS_SOURCE_PRODUCT, "ATL-1", "Zupelnie inny produkt bez zwiazku")
    report = eval_service.evaluate([_row(label="no")], thresholds=[75])
    assert report.thresholds[0].tn == 1
    assert report.thresholds[0].tp == 0
    assert report.not_retrieved == 1  # never blocked at all -> check() cannot have returned it


def test_variant_label_is_positive_only_in_the_variant_sweep(two_kind_provider):
    _add(two_kind_provider, FingerprintKind.PIM_PRODUCT, "PIM-1", "Kestrel Kettle", gtin=GTIN, brand="Kestrel")
    _add(two_kind_provider, FingerprintKind.ATLAS_SOURCE_PRODUCT, "ATL-1", "Kestrel Kettle", gtin=GTIN, brand="Kestrel")
    report = eval_service.evaluate([_row(label="variant")], thresholds=[45])
    assert report.thresholds[0].positives == "match"
    assert report.thresholds[0].tp == 0
    assert report.thresholds[0].fp == 1
    assert report.thresholds_with_variant[0].positives == "match+variant"
    assert report.thresholds_with_variant[0].tp == 1


def test_evaluate_leaves_no_decision_log_by_default(two_kind_provider):
    """A full pairs file must not flood `DedupDecision` — `log_decisions` defaults to False."""
    _add(two_kind_provider, FingerprintKind.PIM_PRODUCT, "PIM-1", "Kestrel Kettle", gtin=GTIN, brand="Kestrel")
    _add(two_kind_provider, FingerprintKind.ATLAS_SOURCE_PRODUCT, "ATL-1", "Kestrel Kettle", gtin=GTIN, brand="Kestrel")
    eval_service.evaluate([_row(label="match")], thresholds=[75])
    assert DedupDecision.objects.count() == 0


def test_evaluate_logs_decisions_when_asked(two_kind_provider):
    _add(two_kind_provider, FingerprintKind.PIM_PRODUCT, "PIM-1", "Kestrel Kettle", gtin=GTIN, brand="Kestrel")
    _add(two_kind_provider, FingerprintKind.ATLAS_SOURCE_PRODUCT, "ATL-1", "Kestrel Kettle", gtin=GTIN, brand="Kestrel")
    eval_service.evaluate([_row(label="match")], thresholds=[75], log_decisions=True)
    row = DedupDecision.objects.get()
    assert row.source == DecisionSource.LOOKUP_EVAL
    assert row.candidate_ref == "ATL-1"


def test_confusion_buckets_by_ground_truth_label_and_engine_decision(two_kind_provider):
    _add(two_kind_provider, FingerprintKind.PIM_PRODUCT, "PIM-1", "Kestrel Kettle", gtin=GTIN, brand="Kestrel")
    _add(two_kind_provider, FingerprintKind.ATLAS_SOURCE_PRODUCT, "ATL-1", "Kestrel Kettle", gtin=GTIN, brand="Kestrel")
    report = eval_service.evaluate([_row(label="match")], thresholds=[75])
    assert report.confusion[("match", DecisionAuto.MATCH)] == 1


def test_attrs_flow_through_so_weight_evidence_scores(two_kind_provider):
    """Regression: `_build_query` used to drop `item.attrs`, so L6 (weight) never fired."""
    weight = {"weight": Decimal("1.500")}
    _add(two_kind_provider, FingerprintKind.PIM_PRODUCT, "PIM-1", "Kestrel Kettle", brand="Kestrel", attrs=weight)
    _add(
        two_kind_provider,
        FingerprintKind.ATLAS_SOURCE_PRODUCT,
        "ATL-1",
        "Kestrel Kettle",
        brand="Kestrel",
        attrs=weight,
    )
    report = eval_service.evaluate([_row(label="match")], thresholds=[56])
    assert report.thresholds[0].tp == 1  # name+brand alone (~55) clears 56 only with weight_match (+5)


def test_recall_at_50_finds_a_trigram_similar_candidate(two_kind_provider):
    _add(two_kind_provider, FingerprintKind.PIM_PRODUCT, "PIM-1", "Kestrel Office Chair Black")
    _add(two_kind_provider, FingerprintKind.ATLAS_SOURCE_PRODUCT, "ATL-1", "Kestrel Office Chair Black")
    report = eval_service.evaluate([_row(label="match")], thresholds=[75])
    assert report.recall_at_50_name == 1.0


def test_recall_at_50_is_zero_for_a_name_that_never_blocks(two_kind_provider):
    _add(two_kind_provider, FingerprintKind.PIM_PRODUCT, "PIM-1", "Zzz completely unrelated text one")
    _add(two_kind_provider, FingerprintKind.ATLAS_SOURCE_PRODUCT, "ATL-1", "Qqq nothing whatsoever in common two")
    report = eval_service.evaluate([_row(label="match")], thresholds=[75])
    assert report.recall_at_50_name == 0.0


def test_recall_at_50_excludes_the_exact_key_leg(two_kind_provider):
    """Regression: the mixed union used to credit a gtin_exact hit to 'name blocking'."""
    _add(two_kind_provider, FingerprintKind.PIM_PRODUCT, "PIM-1", "Zzz completely unrelated text one", gtin=GTIN)
    _add(
        two_kind_provider,
        FingerprintKind.ATLAS_SOURCE_PRODUCT,
        "ATL-1",
        "Qqq nothing whatsoever in common two",
        gtin=GTIN,
    )
    report = eval_service.evaluate([_row(label="match")], thresholds=[75])
    assert report.recall_at_50_name == 0.0  # gtin_exact would have found it; the isolated name leg must not


def test_recall_at_20_image_is_none_without_any_embeddings(two_kind_provider):
    _add(two_kind_provider, FingerprintKind.PIM_PRODUCT, "PIM-1", "Kestrel Kettle", gtin=GTIN, brand="Kestrel")
    _add(two_kind_provider, FingerprintKind.ATLAS_SOURCE_PRODUCT, "ATL-1", "Kestrel Kettle", gtin=GTIN, brand="Kestrel")
    report = eval_service.evaluate([_row(label="match")], thresholds=[75])
    assert report.recall_at_20_image is None


def test_recall_at_20_image_is_none_when_the_query_has_no_phash(two_kind_provider):
    """Regression: a NULL pHash used to be fabricated as `0` instead of skipping the leg."""
    query_vector = unit_vector(2)
    query_row = _add(two_kind_provider, FingerprintKind.PIM_PRODUCT, "PIM-1", "Kestrel Kettle")
    Fingerprint.objects.filter(pk=query_row.pk).update(phash=None, image_vec=query_vector, vec_model="v1")
    candidate_row = _add(two_kind_provider, FingerprintKind.ATLAS_SOURCE_PRODUCT, "ATL-1", "Nightshade Rover Unit")
    Fingerprint.objects.filter(pk=candidate_row.pk).update(
        phash=1, image_vec=similar_to(query_vector, 0.99), vec_model="v1"
    )
    report = eval_service.evaluate([_row(label="match")], thresholds=[75], image_only=True)
    assert report.recall_at_20_image is None


def test_image_only_reports_recall_at_20_from_stored_vectors(two_kind_provider):
    """Query and candidate names share no trigram — the number can only come from the image legs."""
    query_vector = unit_vector(1)
    query_row = _add(two_kind_provider, FingerprintKind.PIM_PRODUCT, "PIM-1", "Kestrel Kettle")
    Fingerprint.objects.filter(pk=query_row.pk).update(phash=1, image_vec=query_vector, vec_model="fake-v1")
    candidate_row = _add(two_kind_provider, FingerprintKind.ATLAS_SOURCE_PRODUCT, "ATL-1", "Zephyrion Anvil Extractor")
    Fingerprint.objects.filter(pk=candidate_row.pk).update(
        phash=1, image_vec=similar_to(query_vector, 0.99), vec_model="fake-v1"
    )
    report = eval_service.evaluate([_row(label="match")], thresholds=[75], image_only=True)
    assert report.thresholds == []
    assert report.recall_at_20_image == 1.0
