# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""What `/search/` reports: relevance normalised to the query's modality, and the kind of match.

No database — `score_pair` on a ParsedQuery and an unsaved Fingerprint, with the blocking annotations
(`name_similarity`, `image_distance`) injected by hand, exactly like `test_image_scoring`.
"""

import pytest

from django_lookup.enums import DecisionAuto, MatchKind
from django_lookup.models import Fingerprint
from django_lookup.services.image_service import QueryImage
from django_lookup.services.query_parser import ParsedQuery
from django_lookup.services.scoring import FIND_IMAGE_WEIGHT, WEIGHTS, score_pair
from tests.fake_embedding import MODEL_ID as MODEL

QUERY_HASH = 0x0F0F0F0F0F0F0F0F
GTIN = "05901234123457"
UNTRUSTED_GTIN = "04006381333931"  # prefix 04 -> restricted circulation
NAME = "wiertarka udarowa niebieska"


def _image(phash: int = QUERY_HASH) -> QueryImage:
    return QueryImage(phash=phash, model_id=MODEL, vector=[0.0], degraded=False)


def _distant(bits: int) -> int:
    return (QUERY_HASH & ~((1 << bits) - 1)) | (~QUERY_HASH & ((1 << bits) - 1))


def _candidate(name_similarity: float = 0.0, cosine: float | None = None, **fields) -> Fingerprint:
    row = Fingerprint(kind="pim_product", ref="SKU-1", **fields)
    row.name_similarity = name_similarity
    if cosine is not None:
        row.image_distance = 1.0 - cosine
        row.vec_model = MODEL
    return row


# --- a photo alone is judged by the photo ----------------------------------------------------------


def test_the_same_file_is_the_whole_answer():
    pair = score_pair(ParsedQuery(), _candidate(phash=_distant(2)), _image())
    assert (pair.relevance, pair.match) == (100, MatchKind.EXACT)
    # The dedup side is untouched: still a weak, image-only piece of evidence.
    assert pair.score == WEIGHTS["image_near_exact"]
    assert pair.decision == DecisionAuto.NO_MATCH


@pytest.mark.parametrize(("cosine", "expected"), [(0.99, 95), (0.95, 75), (0.90, 50), (0.85, 25), (0.80, 0)])
def test_the_cosine_maps_linearly_over_the_similar_band(cosine, expected):
    pair = score_pair(ParsedQuery(), _candidate(phash=_distant(20), cosine=cosine), _image())
    assert pair.relevance == expected
    assert pair.match == (MatchKind.SIMILAR if expected else MatchKind.NONE)


def test_a_reworked_shot_outranks_a_weak_cosine():
    pair = score_pair(ParsedQuery(), _candidate(phash=_distant(8), cosine=0.85), _image())
    assert (pair.relevance, pair.match) == (85, MatchKind.SIMILAR)


def test_a_neighbour_nothing_agreed_on_is_none():
    pair = score_pair(ParsedQuery(), _candidate(phash=_distant(20), cosine=0.5), _image())
    assert (pair.relevance, pair.match) == (0, MatchKind.NONE)


def test_a_candidate_embedded_by_another_model_has_no_cosine():
    row = _candidate(phash=_distant(20), cosine=0.99)
    row.vec_model = "someone-else/model"
    assert score_pair(ParsedQuery(), row, _image()).relevance == 0


# --- text alone is judged by identifier and name ---------------------------------------------------


def test_a_trusted_gtin_is_exact():
    query = ParsedQuery(gtin14=GTIN, gtin_trusted=True)
    pair = score_pair(query, _candidate(gtin14=GTIN, gtin_trusted=True))
    assert (pair.relevance, pair.match) == (100, MatchKind.EXACT)


def test_an_untrusted_gtin_says_probably():
    query = ParsedQuery(gtin14=UNTRUSTED_GTIN, gtin_trusted=False)
    pair = score_pair(query, _candidate(gtin14=UNTRUSTED_GTIN, gtin_trusted=False))
    assert (pair.relevance, pair.match) == (60, MatchKind.SIMILAR)


def test_an_mpn_identifies_fully_only_with_its_brand():
    without_brand = score_pair(ParsedQuery(mpn_norm="TS100"), _candidate(mpn_norm="TS100"))
    with_brand = score_pair(
        ParsedQuery(mpn_norm="TS100", brand_norm="bosch"), _candidate(mpn_norm="TS100", brand_norm="bosch")
    )
    assert (without_brand.relevance, without_brand.match) == (80, MatchKind.SIMILAR)
    assert (with_brand.relevance, with_brand.match) == (100, MatchKind.EXACT)


@pytest.mark.parametrize(("trigram", "expected"), [(0.9, 100), (0.6, 45), (0.35, 0)])
def test_the_trigram_maps_over_the_floor_to_ceiling_band(trigram, expected):
    query = ParsedQuery(name_norm=NAME)
    pair = score_pair(query, _candidate(name_similarity=trigram, name_norm="zupelnie inna nazwa"))
    assert pair.relevance == expected
    assert pair.match != MatchKind.EXACT  # a name is never an exact match


def test_reordered_tokens_count_through_the_token_leg():
    query = ParsedQuery(name_norm="wiertarka udarowa bosch")
    pair = score_pair(query, _candidate(name_similarity=0.0, name_norm="bosch wiertarka udarowa"))
    assert pair.relevance == 100


# --- both: a fixed blend, unless an identifier settles it -----------------------------------------


def test_an_exact_identifier_settles_a_mixed_query():
    query = ParsedQuery(gtin14=GTIN, gtin_trusted=True, name_norm=NAME)
    pair = score_pair(query, _candidate(gtin14=GTIN, gtin_trusted=True, phash=_distant(20)), _image())
    assert (pair.relevance, pair.match) == (100, MatchKind.EXACT)


def test_text_and_picture_blend_by_the_image_weight():
    query = ParsedQuery(name_norm=NAME)
    candidate = _candidate(name_similarity=0.9, name_norm="inna nazwa", phash=_distant(20), cosine=0.99)
    pair = score_pair(query, candidate, _image())
    assert pair.relevance == round(FIND_IMAGE_WEIGHT * 95 + (1 - FIND_IMAGE_WEIGHT) * 100)


def test_a_picture_the_text_disagrees_with_is_only_half_relevant():
    query = ParsedQuery(name_norm=NAME)
    candidate = _candidate(name_similarity=0.0, name_norm="inna nazwa", phash=_distant(20), cosine=0.99)
    pair = score_pair(query, candidate, _image())
    assert (pair.relevance, pair.match) == (round(FIND_IMAGE_WEIGHT * 95), MatchKind.SIMILAR)


def test_the_same_file_is_exact_even_when_the_text_disagrees():
    query = ParsedQuery(name_norm=NAME)
    pair = score_pair(query, _candidate(name_norm="inna nazwa", phash=_distant(2)), _image())
    assert (pair.relevance, pair.match) == (50, MatchKind.EXACT)


def test_a_brand_alone_makes_the_query_mixed():
    pair = score_pair(ParsedQuery(brand_norm="bosch"), _candidate(brand_norm="bosch", phash=_distant(2)), _image())
    assert pair.relevance == 50


# --- conflicts are dedup facts, not relevance ------------------------------------------------------


def test_a_variant_conflict_leaves_relevance_alone():
    query = ParsedQuery(name_norm=NAME, color="black")
    pair = score_pair(query, _candidate(name_similarity=0.9, name_norm=NAME, color="white"))
    assert pair.relevance == 100
    assert "color_conflict" in [reason.code for reason in pair.reasons]
    assert pair.decision != DecisionAuto.MATCH
