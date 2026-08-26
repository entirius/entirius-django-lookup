# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The two image blocking legs against a real Postgres: `bit_count` on pHash and HNSW cosine.

Vectors are built by hand rather than embedded — the point is what the SQL returns, and a fake
provider's output cannot be aimed at a chosen cosine.
"""

import pytest
from django.test import override_settings

from django_lookup.enums import FingerprintKind
from django_lookup.models import Fingerprint
from django_lookup.schemas.requests.lookup import LookupQuery
from django_lookup.services.blocking import candidates
from django_lookup.services.image_service import QueryImage
from django_lookup.services.query_parser import parse
from django_lookup.settings import DEFAULT_IMAGE_TOP_K
from tests.fake_embedding import MODEL_ID as MODEL
from tests.vectors import similar_to, unit_vector

pytestmark = pytest.mark.django_db

SCOPE = [FingerprintKind.PIM_PRODUCT]
QUERY_HASH = 0x0F0F0F0F0F0F0F0F  # an arbitrary 64-bit pattern that fits a signed bigint
EMPTY = parse(LookupQuery(q="zzz nothing matches here"))


def _row(ref: str, **fields) -> Fingerprint:
    fields.setdefault("name_norm", ref)
    return Fingerprint.objects.create(kind=FingerprintKind.PIM_PRODUCT, ref=ref, **fields)


def _flip(value: int, bits: int) -> int:
    """`value` with `bits` low bits inverted — a pHash exactly `bits` away."""
    flipped = (value & ~((1 << bits) - 1)) | (~value & ((1 << bits) - 1))
    return flipped - (1 << 64) if flipped >= (1 << 63) else flipped


def _image(vector: list[float] | None = None, phash: int = QUERY_HASH) -> QueryImage:
    return QueryImage(phash=phash, model_id=MODEL, vector=vector, degraded=vector is None)


def test_a_near_identical_hash_blocks_even_with_no_text():
    _row("SKU-NEAR", phash=_flip(QUERY_HASH, 3))
    _row("SKU-FAR", phash=_flip(QUERY_HASH, 40))
    assert [row.ref for row in candidates(EMPTY, SCOPE, image=_image())] == ["SKU-NEAR"]


def test_the_hash_leg_stops_at_the_research_threshold():
    _row("SKU-EDGE", phash=_flip(QUERY_HASH, 10))
    _row("SKU-OVER", phash=_flip(QUERY_HASH, 11))
    assert [row.ref for row in candidates(EMPTY, SCOPE, image=_image())] == ["SKU-EDGE"]


def test_rows_without_a_hash_are_invisible_to_the_hash_leg():
    _row("SKU-NOHASH")
    assert candidates(EMPTY, SCOPE, image=_image()) == []


def test_the_hash_leg_is_bounded():
    for index in range(DEFAULT_IMAGE_TOP_K + 5):
        _row(f"SKU-{index}", phash=_flip(QUERY_HASH, 1 + index % 3))
    assert len(candidates(EMPTY, SCOPE, image=_image())) == DEFAULT_IMAGE_TOP_K


def test_the_vector_leg_returns_the_nearest_rows_first():
    query = unit_vector(1)
    _row("SKU-CLOSE", image_vec=similar_to(query, 0.98), vec_model=MODEL)
    _row("SKU-MID", image_vec=similar_to(query, 0.85), vec_model=MODEL)
    _row("SKU-AWAY", image_vec=unit_vector(999), vec_model=MODEL)
    found = [row.ref for row in candidates(EMPTY, SCOPE, image=_image(query))]
    assert found[:2] == ["SKU-CLOSE", "SKU-MID"]


def test_the_vector_leg_filters_by_vec_model():
    """Mixed models are invisible, never deleted — the operator re-embeds when they choose to."""
    query = unit_vector(2)
    _row("SKU-OURS", image_vec=similar_to(query, 0.99), vec_model=MODEL)
    _row("SKU-THEIRS", image_vec=similar_to(query, 0.999), vec_model="another-model")
    assert [row.ref for row in candidates(EMPTY, SCOPE, image=_image(query))] == ["SKU-OURS"]


def test_every_candidate_carries_the_distance_scoring_needs():
    query = unit_vector(3)
    _row("SKU-A", image_vec=similar_to(query, 0.97), vec_model=MODEL)
    row = candidates(EMPTY, SCOPE, image=_image(query))[0]
    assert row.image_distance == pytest.approx(1 - 0.97, abs=0.02)


def test_a_degraded_query_still_uses_the_hash_leg():
    query = unit_vector(4)
    _row("SKU-VEC", image_vec=similar_to(query, 0.99), vec_model=MODEL)
    _row("SKU-HASH", phash=_flip(QUERY_HASH, 2))
    assert [row.ref for row in candidates(EMPTY, SCOPE, image=_image())] == ["SKU-HASH"]


def test_a_text_hit_and_an_image_hit_end_up_in_one_pool():
    query = unit_vector(5)
    _row("SKU-TEXT", name_norm="wiertarka udarowa bosch")
    _row("SKU-IMAGE", image_vec=similar_to(query, 0.99), vec_model=MODEL)
    parsed = parse(LookupQuery(q="wiertarka udarowa bosch"))
    assert {row.ref for row in candidates(parsed, SCOPE, image=_image(query))} == {"SKU-TEXT", "SKU-IMAGE"}


def test_no_image_means_no_image_legs():
    _row("SKU-NEAR", phash=_flip(QUERY_HASH, 1))
    assert candidates(EMPTY, SCOPE) == []


def test_the_ef_search_setting_reaches_the_session():
    """A wrong `hnsw.ef_search` silently costs recall, so the query must actually apply it."""
    query = unit_vector(6)
    _row("SKU-A", image_vec=similar_to(query, 0.99), vec_model=MODEL)
    with override_settings(LOOKUP_HNSW_EF_SEARCH=200):
        assert [row.ref for row in candidates(EMPTY, SCOPE, image=_image(query))] == ["SKU-A"]


def test_the_image_legs_honour_the_configured_top_k(settings):
    """`LOOKUP_IMAGE_TOP_K` is the depth knob: more neighbours per leg, more candidates to score."""
    for index in range(DEFAULT_IMAGE_TOP_K + 5):
        _row(f"SKU-{index}", phash=_flip(QUERY_HASH, 1 + index % 3))
    settings.LOOKUP_IMAGE_TOP_K = 5
    assert len(candidates(EMPTY, SCOPE, image=_image())) == 5


def test_the_phash_gate_width_is_configurable(settings):
    """`LOOKUP_PHASH_MAX_DISTANCE` widens or narrows the free near-exact gate."""
    _row("SKU-FAR", phash=_flip(QUERY_HASH, 12))
    assert candidates(EMPTY, SCOPE, image=_image()) == []
    settings.LOOKUP_PHASH_MAX_DISTANCE = 16
    assert [row.ref for row in candidates(EMPTY, SCOPE, image=_image())] == ["SKU-FAR"]
