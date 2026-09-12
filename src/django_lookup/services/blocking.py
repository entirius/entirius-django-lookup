# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Blocking — cheap candidate generation in SQL (research r02 §1, r01 §3).

UNION of five legs, exact hits first: the exact keys (GTIN14, brand+MPN, catalog reference — B-tree),
the pHash neighbourhood (`bit_count(phash # query) <= LOOKUP_PHASH_MAX_DISTANCE`, a seq scan over
8-byte columns), the trigram top-50 (GIN `gin_trgm_ops`), the image-embedding top-k (pgvector HNSW
cosine, `LOOKUP_IMAGE_TOP_K`) and the word-similarity top-50 (pg_trgm `word_similarity`, the same GIN
index: the query against any run of consecutive words of the stored name — what the whole-string
similarity cannot see when a short query meets a long marketing title). Recall is judged by
`tests/test_blocking.py`; scoring never sees a row this function did not return.

Every returned row carries `name_similarity` (the pg_trgm value) and, when the query has a vector,
`image_distance` (the cosine distance) — the annotations levels L3 and L8 score on. Query and
annotation must stay the same text and the same vector, so both live here.

`name_similarity` is measured against the query name as given *and* with the candidate's own
`brand_norm` taken out of it, and the better one counts. The indexer removes the provider's brand from
`name_norm`; a query rarely carries a `brand` field, so its name keeps the word and the two strings
would reach pg_trgm one token apart — writing the brand into a product name, the ordinary thing to do,
would lower its own score. Removing per candidate exactly what the index removed from that candidate is
the symmetric comparison; no vocabulary has to guess which word the index dropped.
"""

from functools import reduce
from operator import or_

from django.contrib.postgres.search import TrigramSimilarity, TrigramWordSimilarity
from django.db import connection, transaction
from django.db.models import BigIntegerField, F, FloatField, Func, IntegerField, Q, QuerySet, Value
from django.db.models.functions import Cast, Concat, Greatest, Replace
from pgvector import HalfVector
from pgvector.django import CosineDistance

from django_lookup.models import Fingerprint
from django_lookup.services.image_service import QueryImage
from django_lookup.services.query_parser import ParsedQuery
from django_lookup.services.scoring import TRIGRAM_FLOOR
from django_lookup.settings import get_hnsw_ef_search, get_image_top_k, get_phash_max_distance

TRIGRAM_LIMIT = 50  # top-N of the fuzzy leg (research r02 §1)
WORD_LIMIT = 50  # top-N of the word-similarity leg
CANDIDATE_LIMIT = 100  # hard cap on what scoring is asked to look at
# Shortest text worth a trigram search: below 3 characters there is not one full trigram.
MIN_TRIGRAM_LENGTH = 3
# Word-similarity floor: pg_trgm's own default for `<%`, so the GIN pre-filter and this floor agree.
WORD_SIMILARITY_FLOOR = 0.6
# Fewest query tokens worth a word search: one generic word ("range") sits inside half the titles at 1.0.
MIN_WORD_TOKENS = 2


def candidates(
    parsed: ParsedQuery, scope: list[str], image: QueryImage | None = None, limit: int = CANDIDATE_LIMIT
) -> list[Fingerprint]:
    """Candidate pool for one query: exact hits first, then the fuzzy legs, deduplicated by row."""
    base = _annotated(scope, parsed.name_norm, image)
    legs = (
        _exact(base, parsed, limit)
        + _near_hash(base, image)
        + _trigram(base, parsed.name_norm)
        + _near_vector(base, image)
        + _words(base, parsed)
    )
    pool: dict[int, Fingerprint] = {}
    for row in legs:
        pool.setdefault(row.pk, row)
    return list(pool.values())[:limit]


def _without_candidate_brand(text: str):
    """`text` with the row's own `brand_norm` removed as a whole word — what the indexer did to that
    row's name. Padding with spaces keeps `pro` out of `product`; an empty brand matches nothing."""
    brand = Concat(Value(" "), F("brand_norm"), Value(" "))
    return Replace(Value(f" {text} "), brand, Value(" "))


def _annotated(scope: list[str], text: str, image: QueryImage | None) -> QuerySet:
    """One base queryset for every leg, so all candidates carry the same similarity and distance."""
    if text:
        similarity = Greatest(
            TrigramSimilarity("name_norm", text),
            TrigramSimilarity("name_norm", _without_candidate_brand(text)),
        )
    else:
        similarity = Value(0.0, output_field=FloatField())
    queryset = Fingerprint.objects.filter(kind__in=list(scope)).annotate(name_similarity=similarity)
    if image is None or image.vector is None:
        return queryset
    return queryset.annotate(image_distance=CosineDistance("image_vec", HalfVector(image.vector)))


def _exact_filters(parsed: ParsedQuery) -> list[Q]:
    filters = []
    if parsed.gtin14:
        filters.append(Q(gtin14=parsed.gtin14))
    if parsed.brand_norm and parsed.mpn_norm:
        filters.append(Q(brand_norm=parsed.brand_norm, mpn_norm=parsed.mpn_norm))
    if parsed.sku:
        filters.append(Q(ref=parsed.sku))
    return filters


def _exact_rank(parsed: ParsedQuery, row: Fingerprint) -> tuple[int, float]:
    """GTIN hit first, then brand+MPN, then the catalog reference; ties by name similarity."""
    if parsed.gtin14 and row.gtin14 == parsed.gtin14:
        rank = 0
    elif parsed.mpn_norm and row.mpn_norm == parsed.mpn_norm:
        rank = 1
    else:
        rank = 2
    return rank, -float(row.name_similarity or 0.0)


def _exact(base: QuerySet, parsed: ParsedQuery, limit: int) -> list[Fingerprint]:
    filters = _exact_filters(parsed)
    if not filters:
        return []
    rows = list(base.filter(reduce(or_, filters))[:limit])
    return sorted(rows, key=lambda row: _exact_rank(parsed, row))


def _trigram(base: QuerySet, text: str) -> list[Fingerprint]:
    """`__trigram_similar` uses the GIN index (its 0.3 operator threshold is below our floor),
    the annotation then enforces the 0.35 of research r02 §3."""
    if len(text) < MIN_TRIGRAM_LENGTH:
        return []
    queryset = base.filter(name_norm__trigram_similar=text, name_similarity__gte=TRIGRAM_FLOOR)
    return list(queryset.order_by("-name_similarity", "id")[:TRIGRAM_LIMIT])


def _words(base: QuerySet, parsed: ParsedQuery) -> list[Fingerprint]:
    """pg_trgm `word_similarity(query, name_norm)`: the query against any run of consecutive words of
    the stored name (`<%` uses the same GIN index as the trigram leg).

    `similarity` is over the whole string, so "make + model" typed against a 90-character
    marketing title never reaches `TRIGRAM_FLOOR`; word similarity does not care how long the rest of
    the title is and still tolerates a typo. It is the last leg on purpose: the pool is cut at
    `CANDIDATE_LIMIT`, and a text+image query must not lose its image neighbours to it.
    """
    if len(parsed.name_tokens) < MIN_WORD_TOKENS:
        return []
    queryset = base.filter(name_norm__trigram_word_similar=parsed.name_norm).annotate(
        word_similarity=TrigramWordSimilarity(parsed.name_norm, "name_norm")
    )
    queryset = queryset.filter(word_similarity__gte=WORD_SIMILARITY_FLOOR)
    return list(queryset.order_by("-word_similarity", "id")[:WORD_LIMIT])


def _near_hash(base: QuerySet, image: QueryImage | None) -> list[Fingerprint]:
    """pHash neighbourhood — the free near-exact gate; no index, `bit_count` over a bigint column."""
    if image is None:
        return []
    # The cast is load-bearing: psycopg adapts a Python int as `numeric`, and `bigint # numeric`
    # is not an operator Postgres knows.
    difference = F("phash").bitxor(Cast(Value(image.phash), BigIntegerField()))
    queryset = base.filter(phash__isnull=False).annotate(phash_distance=_bit_count(difference))
    return list(
        queryset.filter(phash_distance__lte=get_phash_max_distance()).order_by("phash_distance", "id")[
            : get_image_top_k()
        ]
    )


def _near_vector(base: QuerySet, image: QueryImage | None) -> list[Fingerprint]:
    """HNSW cosine, top-k from `LOOKUP_IMAGE_TOP_K` (default 20).

    Rows embedded by another model are filtered out, never deleted.
    """
    if image is None or image.vector is None:
        return []
    queryset = base.filter(vec_model=image.model_id, image_vec__isnull=False)
    with transaction.atomic():
        _apply_ef_search()
        return list(queryset.order_by("image_distance")[: get_image_top_k()])


def _bit_count(expression) -> Func:
    """`bit_count` takes bit/bytea, never bigint — the `::bit(64)` cast is what makes it callable."""
    return Func(
        expression, function="bit_count", template="bit_count(%(expressions)s::bit(64))", output_field=IntegerField()
    )


def _apply_ef_search() -> None:
    """`SET LOCAL` through `set_config` — the only parameterised way to set a GUC from the ORM."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('hnsw.ef_search', %s, true)", [str(get_hnsw_ef_search())])
