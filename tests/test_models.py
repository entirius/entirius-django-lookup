# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import pytest
from django.db import connection

from django_lookup.enums import DecisionAuto, DecisionSource, FingerprintKind
from django_lookup.models import DedupDecision, Fingerprint


@pytest.mark.django_db
def test_extensions_and_indexes_exist():
    with connection.cursor() as cur:
        cur.execute("SELECT extname FROM pg_extension WHERE extname IN ('vector', 'pg_trgm', 'unaccent')")
        assert {row[0] for row in cur.fetchall()} == {"vector", "pg_trgm", "unaccent"}
        cur.execute("SELECT indexdef FROM pg_indexes WHERE tablename = 'django_lookup_fingerprint'")
        defs = "\n".join(row[0] for row in cur.fetchall())
    assert "USING gin (name_norm gin_trgm_ops)" in defs
    assert "USING hnsw (image_vec halfvec_cosine_ops)" in defs
    assert "(brand_norm, mpn_norm)" in defs


@pytest.mark.django_db
def test_fingerprint_str_and_decision_roundtrip():
    fp = Fingerprint.objects.create(kind=FingerprintKind.PIM_PRODUCT, ref="1")
    assert str(fp) == "Fingerprint(pim_product:1)"
    decision = DedupDecision.objects.create(
        query={"gtin": "x"},
        candidate_kind=fp.kind,
        candidate_ref=fp.ref,
        score=80,
        features=[{"code": "gtin_exact", "label": "GTIN", "score": 60, "observed": True}],
        decision_auto=DecisionAuto.MATCH,
        source=DecisionSource.API_CHECK,
    )
    decision.refresh_from_db()
    assert decision.decision_human == "" and decision.user is None
    assert decision.features[0]["code"] == "gtin_exact"
    assert str(decision) == "DedupDecision(pim_product:1 match 80)"
