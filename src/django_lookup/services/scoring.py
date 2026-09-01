# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Pairwise scoring: query vs one candidate fingerprint (research r02 §1, §3, §4, §5).

Additive evidence — every level that fires emits a `Reason` carrying its own weight, so the total
is always explainable and `sum(reason.score)` reconstructs the score (before the 0-100 clamp).
The same vector is logged with each decision and is the training set for a later classifier.

Levels: L0 GTIN · L1 brand+MPN · L2 sku · L3 name trigram · L4 name tokens · L5 brand ·
L6 weight/dimensions · L7 colour/size/pack · L8 image (pHash distance + embedding cosine).

L8 is evidence, never proof: two black t-shirts photograph alike, so a candidate whose only positive
evidence is its picture is flagged `image_only` and can never reach `match` (research r01 §2/§3).

The additive score answers "is this the same product?" — the dedup question `/check/` asks. `/search/`
asks a different one, "how well does this hit match what I was given?", and answers it with
`relevance`: each evidence group (identifier, text, image) is normalised to 0–100 on its own scale and
the *query's modality* decides which one counts — a photo-only query is scored by the photo, a text-only
query by identifier/name, a mixed query by a fixed blend. Adding the raw groups instead would let one
scale dominate whatever the other said, which is exactly why hybrid search engines weight by query
(Meilisearch `semanticRatio`, Weaviate/Typesense `alpha`) or fuse by rank (RRF) rather than summing.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from rapidfuzz.fuzz import token_set_ratio

from django_lookup.enums import DecisionAuto, MatchKind
from django_lookup.models import Fingerprint
from django_lookup.services.image_prep import hamming
from django_lookup.services.image_service import QueryImage
from django_lookup.services.query_parser import ParsedQuery
from django_lookup.settings import get_thresholds

# --- weights (research r02 §1 — binding) ---
WEIGHTS: dict[str, int] = {
    "gtin_exact": 60,
    "gtin_exact_untrusted": 15,
    "brand_mpn_exact": 45,
    "mpn_exact": 20,
    "mpn_exact_brand_conflict": -30,
    "sku_exact": 30,
    "name_trigram": 25,  # maximum of the linear map
    "name_tokens_strong": 20,
    "name_tokens_weak": 10,
    "brand_equal": 10,
    "brand_conflict": -25,
    "weight_match": 5,
    "weight_conflict": -15,
    "dimensions_match": 5,
    "dimensions_conflict": -15,
    "color_equal": 5,
    "color_conflict": -40,
    "size_equal": 5,
    "size_conflict": -40,
    "pack_equal": 5,
    "pack_conflict": -40,
    "image_near_exact": 10,
    "image_near": 6,
    "image_similar_strong": 10,
    "image_similar": 5,
}

# --- thresholds (research r02 §3) ---
TRIGRAM_FLOOR = 0.35  # below it the trigram signal scores nothing
TRIGRAM_CEILING = 0.9  # at and above it the signal is worth WEIGHTS["name_trigram"]
TOKENS_STRONG = 0.85
TOKENS_WEAK = 0.6
WEIGHT_TOLERANCE = Decimal("0.05")  # +-5% ...
WEIGHT_MIN_TOLERANCE = Decimal("0.01")  # ... but at least 10 g (columns are kg)
DIMENSION_TOLERANCE = Decimal("0.03")  # +-3% per axis, axis order ignored
# Image thresholds (research r01 §4): pHash <= 5 is the same picture, <= 10 is the same shot re-worked;
# an off-the-shelf embedding needs a high cosine before it means anything.
PHASH_NEAR_EXACT = 5
PHASH_NEAR = 10
COSINE_STRONG = 0.90
COSINE_SIMILAR = 0.80

# --- flags ---
FLAG_IDENTIFIER_EXACT = "identifier_exact"  # trusted GTIN or brand+MPN hit: always worth a verdict
FLAG_BRAND_CONFLICT = "brand_conflict"
FLAG_VARIANT_CONFLICT = "variant_conflict"
FLAG_IMAGE_ONLY = "image_only"  # the picture is the only thing that agrees — review at best
FLAG_BRAND_DIRTY = "brand_dirty_data"  # brand disagrees, but identifier + name + physicals corroborate
CONFLICT_FLAGS = frozenset({FLAG_BRAND_CONFLICT, FLAG_VARIANT_CONFLICT})
# Any of these caps the verdict at review, whatever the score adds up to.
CAPPING_FLAGS = CONFLICT_FLAGS | {FLAG_IMAGE_ONLY}

SCORE_MIN = 0
SCORE_MAX = 100

# --- relevance (what /search/ reports) ---
RELEVANCE_MAX = 100
# Identifier group: an exact trusted key is the whole answer; a partial one (MPN without a brand on one
# side, a catalog sku) or an untrusted GTIN says "probably" on the same 0-100 scale.
RELEVANCE_IDENTIFIER: dict[str, int] = {
    "gtin_exact": 100,
    "brand_mpn_exact": 100,
    "mpn_exact": 80,
    "sku_exact": 80,
    "gtin_exact_untrusted": 60,
}
# Image group: the same file is the whole answer; the same shot reworked nearly so; otherwise the
# embedding cosine mapped over [COSINE_SIMILAR, 1.0].
RELEVANCE_PHASH_NEAR_EXACT = 100
RELEVANCE_PHASH_NEAR = 85
# The vector can veto the pHash shortcut: the same file re-saved, resized or recompressed embeds at
# ~0.98-1.0, so a pHash <= PHASH_NEAR_EXACT paired with a cosine below this is a contradiction — a
# DCT collision (flat packshots on white collide easily), not the same picture. Without a comparable
# vector the pHash still stands alone.
SAME_FILE_COSINE = 0.95
# Mixed query (text + picture): the share the picture gets. 0.5 is the alpha every hybrid engine
# defaults to; an exact identifier short-circuits it to 100 regardless.
FIND_IMAGE_WEIGHT = 0.5


@dataclass(frozen=True)
class Reason:
    """One piece of evidence. `observed` carries both sides so a reviewer can judge the reason."""

    code: str
    label: str
    score: int
    observed: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PairScore:
    score: int
    decision: str
    reasons: list[Reason]
    flags: frozenset[str]
    # What /search/ reports: 0-100 relevance to the query as given (see module docstring), and the
    # kind of match it is. Neither feeds the verdict.
    relevance: int = 0
    match: MatchKind = MatchKind.NONE


def _observed(query_value: object, candidate_value: object) -> dict[str, str]:
    return {"query": str(query_value), "candidate": str(candidate_value)}


def _reason(code: str, label: str, query_value: object, candidate_value: object) -> Reason:
    return Reason(code=code, label=label, score=WEIGHTS[code], observed=_observed(query_value, candidate_value))


def _gtin_level(query: ParsedQuery, candidate: Fingerprint) -> tuple[list[Reason], set[str]]:
    """L0 / L0b — a restricted-circulation prefix (supplier-invented EAN) is evidence, not proof."""
    if not query.gtin14 or query.gtin14 != candidate.gtin14:
        return [], set()
    if query.gtin_trusted and candidate.gtin_trusted:
        label = f"GTIN {candidate.gtin14} identical"
        return [_reason("gtin_exact", label, query.gtin14, candidate.gtin14)], {FLAG_IDENTIFIER_EXACT}
    label = f"GTIN {candidate.gtin14} identical but not trusted (restricted-circulation prefix)"
    return [_reason("gtin_exact_untrusted", label, query.gtin14, candidate.gtin14)], set()


def _mpn_level(query: ParsedQuery, candidate: Fingerprint) -> tuple[list[Reason], set[str]]:
    """L1 / L1b — the MPN only identifies a product together with its brand."""
    if not query.mpn_norm or query.mpn_norm != candidate.mpn_norm:
        return [], set()
    if not query.brand_norm or not candidate.brand_norm:
        label = f"MPN {candidate.mpn_norm} identical, brand unknown on one side"
        return [_reason("mpn_exact", label, query.mpn_norm, candidate.mpn_norm)], set()
    if query.brand_norm == candidate.brand_norm:
        label = f"Brand + MPN identical ({candidate.brand_norm} {candidate.mpn_norm})"
        return [_reason("brand_mpn_exact", label, query.mpn_norm, candidate.mpn_norm)], {FLAG_IDENTIFIER_EXACT}
    label = f"MPN {candidate.mpn_norm} identical but the brand differs"
    return [_reason("mpn_exact_brand_conflict", label, query.brand_norm, candidate.brand_norm)], set()


def _sku_level(query: ParsedQuery, candidate: Fingerprint) -> tuple[list[Reason], set[str]]:
    """L2 — a sku is unique inside one catalog only, so it never carries a verdict on its own."""
    if not query.sku or query.sku != candidate.ref:
        return [], set()
    label = f"Catalog reference {candidate.ref} identical"
    return [_reason("sku_exact", label, query.sku, candidate.ref)], set()


def _trigram_score(similarity: float) -> int:
    """L3 — linear map of the pg_trgm similarity: floor -> 0, ceiling -> full weight."""
    capped = min(similarity, TRIGRAM_CEILING)
    span = TRIGRAM_CEILING - TRIGRAM_FLOOR
    return round(WEIGHTS["name_trigram"] * (capped - TRIGRAM_FLOOR) / span)


def _name_level(query: ParsedQuery, candidate: Fingerprint) -> tuple[list[Reason], set[str]]:
    """L3 + L4 — trigram (blocking measure) and token_set_ratio (reordered titles)."""
    if not query.name_norm or not candidate.name_norm:
        return [], set()
    reasons = []
    similarity = float(getattr(candidate, "name_similarity", 0.0) or 0.0)
    if similarity >= TRIGRAM_FLOOR and (points := _trigram_score(similarity)):
        label = f"Name similar (trigram {similarity:.2f})"
        reasons.append(Reason("name_trigram", label, points, _observed(query.name_norm, candidate.name_norm)))
    ratio = token_set_ratio(query.name_norm, candidate.name_norm) / 100
    if ratio >= TOKENS_WEAK:
        code = "name_tokens_strong" if ratio >= TOKENS_STRONG else "name_tokens_weak"
        label = f"Name tokens overlap ({ratio:.2f})"
        reasons.append(_reason(code, label, query.name_norm, candidate.name_norm))
    return reasons, set()


def _brand_level(query: ParsedQuery, candidate: Fingerprint) -> tuple[list[Reason], set[str]]:
    """L5 — a brand conflict caps the decision at review even when everything else agrees."""
    if not query.brand_norm or not candidate.brand_norm:
        return [], set()
    if query.brand_norm == candidate.brand_norm:
        label = f"Brand identical ({candidate.brand_norm})"
        return [_reason("brand_equal", label, query.brand_norm, candidate.brand_norm)], set()
    label = f"Brand differs: {query.brand_norm} vs {candidate.brand_norm}"
    return [_reason("brand_conflict", label, query.brand_norm, candidate.brand_norm)], {FLAG_BRAND_CONFLICT}


def _within(query_value: Decimal, candidate_value: Decimal, tolerance: Decimal, minimum: Decimal) -> bool:
    allowed = max(abs(query_value) * tolerance, minimum)
    return abs(query_value - candidate_value) <= allowed


def _weight_level(query: ParsedQuery, candidate: Fingerprint) -> tuple[list[Reason], set[str]]:
    """L6a — a missing weight on either side is silence, not evidence."""
    if query.weight is None or candidate.weight is None:
        return [], set()
    in_tolerance = _within(query.weight, candidate.weight, WEIGHT_TOLERANCE, WEIGHT_MIN_TOLERANCE)
    code = "weight_match" if in_tolerance else "weight_conflict"
    label = f"Weight {'within' if in_tolerance else 'outside'} tolerance ({query.weight} vs {candidate.weight})"
    return [_reason(code, label, query.weight, candidate.weight)], set()


def _dimensions(source: ParsedQuery | Fingerprint) -> list[Decimal] | None:
    values = [getattr(source, axis) for axis in ("width", "height", "deep")]
    return sorted(values) if all(value is not None for value in values) else None


def _dimensions_level(query: ParsedQuery, candidate: Fingerprint) -> tuple[list[Reason], set[str]]:
    """L6b — all three axes on both sides or nothing; axis order is ignored (a box can be rotated)."""
    query_axes, candidate_axes = _dimensions(query), _dimensions(candidate)
    if query_axes is None or candidate_axes is None:
        return [], set()
    in_tolerance = all(
        _within(q, c, DIMENSION_TOLERANCE, Decimal(0)) for q, c in zip(query_axes, candidate_axes, strict=True)
    )
    code = "dimensions_match" if in_tolerance else "dimensions_conflict"
    label = f"Dimensions {'within' if in_tolerance else 'outside'} tolerance"
    return [_reason(code, label, query_axes, candidate_axes)], set()


# (attribute, reason prefix, label) — the hard discriminators of research r02 §1 L7.
_VARIANT_ATTRS = (("color", "color", "Colour"), ("size", "size", "Size"), ("pack_qty", "pack", "Pack quantity"))


def _variant_reason(prefix: str, label: str, query_value: object, candidate_value: object) -> Reason:
    if str(query_value) == str(candidate_value):
        return _reason(f"{prefix}_equal", f"{label} identical ({candidate_value})", query_value, candidate_value)
    text = f"{label} differs: {query_value} vs {candidate_value} (variant)"
    return _reason(f"{prefix}_conflict", text, query_value, candidate_value)


def _variant_level(query: ParsedQuery, candidate: Fingerprint) -> tuple[list[Reason], set[str]]:
    """L7 — colour / size / pack are discriminators: fuzzy evidence must never outvote them."""
    reasons, flags = [], set()
    for attr, prefix, label in _VARIANT_ATTRS:
        query_value, candidate_value = getattr(query, attr), getattr(candidate, attr)
        if not query_value or not candidate_value:
            continue
        reason = _variant_reason(prefix, label, query_value, candidate_value)
        reasons.append(reason)
        if reason.score < 0:
            flags.add(FLAG_VARIANT_CONFLICT)
    return reasons, flags


_LEVELS = (
    _gtin_level,
    _mpn_level,
    _sku_level,
    _name_level,
    _brand_level,
    _weight_level,
    _dimensions_level,
    _variant_level,
)


def decide(score: int, flags: frozenset[str] | set[str]) -> str:
    """Thresholds from settings + the decision matrix of research r02 §4.

    An exact identifier (trusted GTIN, brand+MPN) always earns at least a review — a bare EAN query
    has nothing else to add up. A capping flag (conflict, or image-only evidence) holds the verdict
    at review whatever the score.
    """
    thresholds = get_thresholds()
    identified = FLAG_IDENTIFIER_EXACT in flags
    if not flags & CAPPING_FLAGS and (score >= thresholds["match"] or identified):
        return DecisionAuto.MATCH
    if score >= thresholds["review"] or identified:
        return DecisionAuto.REVIEW
    return DecisionAuto.NO_MATCH


def best_signal(reasons: list[Reason]) -> int:
    """The strongest single piece of positive evidence, 0 when none agrees (kept for callers that
    want the dedup-scale view; /search/ reports `relevance` instead)."""
    return max((reason.score for reason in reasons if reason.score > 0), default=0)


def _linear(value: float, floor: float, ceiling: float) -> int:
    """0 at or below `floor`, RELEVANCE_MAX at or above `ceiling`, linear in between."""
    if value <= floor:
        return 0
    return round(RELEVANCE_MAX * (min(value, ceiling) - floor) / (ceiling - floor))


def _identifier_relevance(reasons: list[Reason]) -> int:
    return max((RELEVANCE_IDENTIFIER.get(reason.code, 0) for reason in reasons), default=0)


def _text_relevance(query: ParsedQuery, candidate: Fingerprint) -> int:
    if not query.name_norm or not candidate.name_norm:
        return 0
    trigram = float(getattr(candidate, "name_similarity", 0.0) or 0.0)
    tokens = token_set_ratio(query.name_norm, candidate.name_norm) / 100
    return max(_linear(trigram, TRIGRAM_FLOOR, TRIGRAM_CEILING), _linear(tokens, TOKENS_WEAK, 1.0))


def _phash_distance(candidate: Fingerprint, image: QueryImage | None) -> int | None:
    if image is None or candidate.phash is None:
        return None
    return hamming(image.phash, candidate.phash)


def _cosine(candidate: Fingerprint, image: QueryImage) -> float | None:
    """Embedding cosine, or None when the pair has no comparable vectors."""
    vector_distance = getattr(candidate, "image_distance", None)
    if image.vector is None or vector_distance is None or candidate.vec_model != image.model_id:
        return None
    return 1.0 - float(vector_distance)


def _same_file(candidate: Fingerprint, image: QueryImage | None) -> bool:
    """pHash says near-exact AND the vector does not contradict it (see SAME_FILE_COSINE)."""
    if image is None:
        return False
    distance = _phash_distance(candidate, image)
    if distance is None or distance > PHASH_NEAR_EXACT:
        return False
    cosine = _cosine(candidate, image)
    return cosine is None or cosine >= SAME_FILE_COSINE


def _image_relevance(candidate: Fingerprint, image: QueryImage | None) -> int:
    """The same file (pHash, unless the vector vetoes it) wins outright; otherwise the reworked-shot
    band, otherwise the cosine, mapped over [COSINE_SIMILAR, 1.0] so the floor of any image evidence
    is 0 and identical is 100. A vetoed pHash near-exact falls into the reworked-shot band."""
    if image is None:
        return 0
    if _same_file(candidate, image):
        return RELEVANCE_PHASH_NEAR_EXACT
    cosine = _cosine(candidate, image) or 0.0
    by_cosine = _linear(cosine, COSINE_SIMILAR, 1.0)
    distance = _phash_distance(candidate, image)
    if distance is not None and distance <= PHASH_NEAR:
        return max(RELEVANCE_PHASH_NEAR, by_cosine)
    return by_cosine


def _has_text(query: ParsedQuery) -> bool:
    return any((query.gtin14, query.mpn_norm, query.sku, query.name_norm, query.brand_norm))


def relevance(
    query: ParsedQuery, candidate: Fingerprint, image: QueryImage | None, reasons: list[Reason]
) -> tuple[int, MatchKind]:
    """0-100 relevance of one candidate to the query *as given*, plus the kind of match.

    The query's modality picks the scale: a picture alone is judged by the picture, text alone by the
    best of identifier and name, both by `FIND_IMAGE_WEIGHT` — unless an exact identifier settles it.
    Conflicts (brand, variant) do not lower it: they are dedup facts, and `/check/` says so.
    """
    identifier = _identifier_relevance(reasons)
    text = _text_relevance(query, candidate)
    picture = _image_relevance(candidate, image)
    if image is None:
        value = max(identifier, text)
    elif not _has_text(query):
        value = picture
    elif identifier == RELEVANCE_MAX:
        value = RELEVANCE_MAX
    else:
        value = round(FIND_IMAGE_WEIGHT * picture + (1 - FIND_IMAGE_WEIGHT) * max(identifier, text))
    if identifier == RELEVANCE_MAX or _same_file(candidate, image):
        return value, MatchKind.EXACT
    return value, MatchKind.SIMILAR if value > 0 else MatchKind.NONE


def _phash_reasons(candidate: Fingerprint, image: QueryImage) -> list[Reason]:
    """L8a — the free near-exact gate: a re-saved, resized or recompressed copy of the same file."""
    if candidate.phash is None:
        return []
    distance = hamming(image.phash, candidate.phash)
    if distance > PHASH_NEAR:
        return []
    near_exact = distance <= PHASH_NEAR_EXACT
    code = "image_near_exact" if near_exact else "image_near"
    label = f"Image {'near-identical' if near_exact else 'similar'} (pHash distance {distance})"
    return [_reason(code, label, image.phash, candidate.phash)]


def _vector_reasons(candidate: Fingerprint, image: QueryImage) -> list[Reason]:
    """L8b — the same product photographed differently; only rows embedded by our model may speak."""
    distance = getattr(candidate, "image_distance", None)
    if image.vector is None or distance is None or candidate.vec_model != image.model_id:
        return []
    similarity = 1.0 - float(distance)
    if similarity < COSINE_SIMILAR:
        return []
    code = "image_similar_strong" if similarity >= COSINE_STRONG else "image_similar"
    label = f"Image looks like the same product (cosine {similarity:.2f})"
    return [_reason(code, label, image.model_id, round(similarity, 4))]


def _image_reasons(candidate: Fingerprint, image: QueryImage | None) -> list[Reason]:
    """L8 — evidence from the picture. Both legs may fire; blocking annotated what they need."""
    if image is None:
        return []
    return _phash_reasons(candidate, image) + _vector_reasons(candidate, image)


# A brand conflict means one of two very different things: a corrupted brand field on ONE product, or a
# recycled barcode on TWO products. Only the second changes the name as well — so when a trusted GTIN, a
# strong name and the physicals all agree, the conflict is dirty data and must not cap the verdict.
# (Research r02 §4 writes the cap for the recycled-barcode case; this narrows it to that case.)
_CORROBORATING_NAME = {"name_tokens_strong"}
_PHYSICAL_CONFLICTS = {"weight_conflict", "dimensions_conflict"}
_STRONG_TRIGRAM = 20  # of WEIGHTS["name_trigram"] = 25, i.e. similarity ~0.8 and up


def _brand_conflict_is_dirty_data(reasons: list[Reason], flags: set[str]) -> bool:
    codes = {reason.code for reason in reasons}
    if FLAG_VARIANT_CONFLICT in flags or codes & _PHYSICAL_CONFLICTS or "gtin_exact" not in codes:
        return False
    trigram = next((reason.score for reason in reasons if reason.code == "name_trigram"), 0)
    return bool(codes & _CORROBORATING_NAME) or trigram >= _STRONG_TRIGRAM


def score_pair(query: ParsedQuery, candidate: Fingerprint, image: QueryImage | None = None) -> PairScore:
    """Score one candidate. `name_similarity` / `image_distance` (annotated by blocking) drive L3 / L8."""
    reasons: list[Reason] = []
    flags: set[str] = set()
    for level in _LEVELS:
        level_reasons, level_flags = level(query, candidate)
        reasons.extend(level_reasons)
        flags.update(level_flags)
    if FLAG_BRAND_CONFLICT in flags and _brand_conflict_is_dirty_data(reasons, flags):
        flags.discard(FLAG_BRAND_CONFLICT)
        flags.add(FLAG_BRAND_DIRTY)
    if (image_reasons := _image_reasons(candidate, image)) and not any(reason.score > 0 for reason in reasons):
        flags.add(FLAG_IMAGE_ONLY)
    reasons.extend(image_reasons)
    reasons.sort(key=lambda reason: -abs(reason.score))
    total = min(max(sum(reason.score for reason in reasons), SCORE_MIN), SCORE_MAX)
    found, match = relevance(query, candidate, image, reasons)
    return PairScore(
        score=total,
        decision=decide(total, flags),
        reasons=reasons,
        flags=frozenset(flags),
        relevance=found,
        match=match,
    )
