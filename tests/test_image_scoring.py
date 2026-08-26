# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Level L8: what a picture is worth, and what it can never buy on its own."""

import pytest
from django.test import override_settings

from django_lookup.enums import DecisionAuto
from django_lookup.models import Fingerprint
from django_lookup.schemas.requests.lookup import LookupQuery
from django_lookup.services.image_service import QueryImage
from django_lookup.services.query_parser import parse
from django_lookup.services.scoring import (
    COSINE_SIMILAR,
    FLAG_IMAGE_ONLY,
    WEIGHTS,
    score_pair,
)
from tests.fake_embedding import MODEL_ID as MODEL

QUERY_HASH = 0x0F0F0F0F0F0F0F0F
GTIN = "5901234123457"


def _image(vector: list[float] | None = None, phash: int = QUERY_HASH) -> QueryImage:
    return QueryImage(phash=phash, model_id=MODEL, vector=vector, degraded=vector is None)


def _candidate(**fields) -> Fingerprint:
    row = Fingerprint(kind="pim_product", ref="SKU-1", **fields)
    row.name_similarity = 0.0
    return row


def _distant(bits: int) -> int:
    return (QUERY_HASH & ~((1 << bits) - 1)) | (~QUERY_HASH & ((1 << bits) - 1))


def _codes(pair) -> list[str]:
    return [reason.code for reason in pair.reasons]


@pytest.mark.parametrize(
    ("bits", "code"),
    [(0, "image_near_exact"), (5, "image_near_exact"), (6, "image_near"), (10, "image_near")],
)
def test_the_hash_distance_picks_the_tier(bits, code):
    pair = score_pair(parse(LookupQuery(q="anything")), _candidate(phash=_distant(bits)), _image())
    assert _codes(pair) == [code]
    assert pair.score == WEIGHTS[code]


def test_a_distant_hash_says_nothing():
    pair = score_pair(parse(LookupQuery(q="anything")), _candidate(phash=_distant(20)), _image())
    assert _codes(pair) == []


@pytest.mark.parametrize(
    ("cosine", "code"), [(0.99, "image_similar_strong"), (0.90, "image_similar_strong"), (0.85, "image_similar")]
)
def test_the_cosine_picks_the_tier(cosine, code):
    candidate = _candidate(vec_model=MODEL)
    candidate.image_distance = 1 - cosine
    assert _codes(score_pair(parse(LookupQuery(q="x")), candidate, _image([0.0]))) == [code]


def test_a_weak_cosine_says_nothing():
    candidate = _candidate(vec_model=MODEL)
    candidate.image_distance = 1 - (COSINE_SIMILAR - 0.01)
    assert _codes(score_pair(parse(LookupQuery(q="x")), candidate, _image([0.0]))) == []


def test_a_vector_from_another_model_says_nothing():
    """Two models' vectors live in different spaces; their cosine is a number, not a similarity."""
    candidate = _candidate(vec_model="another-model")
    candidate.image_distance = 0.01
    assert _codes(score_pair(parse(LookupQuery(q="x")), candidate, _image([0.0]))) == []


def test_both_legs_can_fire_for_one_candidate():
    candidate = _candidate(phash=_distant(2), vec_model=MODEL)
    candidate.image_distance = 0.02
    assert set(_codes(score_pair(parse(LookupQuery(q="x")), candidate, _image([0.0])))) == {
        "image_near_exact",
        "image_similar_strong",
    }


def test_image_only_evidence_can_never_reach_match():
    """research r01 §2: image-only F1 tops out around 0.7 — the picture proposes, text decides.

    Thresholds are lowered here on purpose: even a configuration where the image score alone clears
    the match bar must still stop at review.
    """
    candidate = _candidate(phash=QUERY_HASH, vec_model=MODEL)
    candidate.image_distance = 0.0
    with override_settings(LOOKUP_THRESHOLDS={"match": 10, "review": 5}):
        pair = score_pair(parse(LookupQuery(q="completely unrelated words")), candidate, _image([0.0]))
    assert FLAG_IMAGE_ONLY in pair.flags
    assert pair.score >= 10
    assert pair.decision == DecisionAuto.REVIEW


def test_a_gtin_hit_with_a_matching_picture_is_not_image_only():
    candidate = _candidate(gtin14="05901234123457", gtin_trusted=True, phash=QUERY_HASH)
    pair = score_pair(parse(LookupQuery(ean=GTIN)), candidate, _image())
    assert FLAG_IMAGE_ONLY not in pair.flags
    assert pair.decision == DecisionAuto.MATCH
    assert "image_near_exact" in _codes(pair)


def test_a_variant_conflict_still_outranks_a_perfect_picture():
    candidate = _candidate(gtin14="05901234123457", gtin_trusted=True, color="red", phash=QUERY_HASH)
    pair = score_pair(parse(LookupQuery(ean=GTIN, attrs={"color": "blue"})), candidate, _image())
    assert pair.decision == DecisionAuto.REVIEW


def test_without_a_query_image_nothing_changes():
    candidate = _candidate(phash=QUERY_HASH, vec_model=MODEL)
    assert _codes(score_pair(parse(LookupQuery(q="x")), candidate)) == []


def test_a_candidate_without_a_picture_is_simply_silent():
    assert _codes(score_pair(parse(LookupQuery(q="x")), _candidate(), _image([0.0]))) == []
