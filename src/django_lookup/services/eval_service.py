# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Calibration harness: labelled pairs -> precision/recall/F1 (test-strategy §4).

Runs the same `check()` the API uses for every row, so score/decision match production exactly —
but with `limit` lifted to the blocking pool size (`blocking.CANDIDATE_LIMIT`), not `MAX_LIMIT` (20):
the API cap would otherwise be the dominant reason a true candidate is missing from `candidates`,
swamping any real scoring signal. `retrieved` on the outcome says whether `check()` returned the
candidate at all; the report counts rows where it did not, separately from the P/R/F1 sweep.

Recall@50 (name) and recall@20 (image) isolate one blocking leg each by blanking the `ParsedQuery`
fields the *other* legs key on (`blocking.candidates` unions exact -> pHash -> trigram -> HNSW and a
plain `pool[:K]` slice would credit exact/trigram hits to "image blocking" and truncate HNSW rows
out). The image leg reuses the query's own already-embedded `Fingerprint` row — no fetch, no
re-embedding — and is skipped (not fabricated) when that row has no pHash.

Nothing here is a CI gate (test-strategy §4: "Number enters AGENTS.md only after being measured") —
a malformed row, an unknown kind, a bad label or a stale ref is skipped and counted by reason, never
raised; the command wraps `evaluate()` too, so a defect here degrades the numbers, not the exit code.
"""

import csv
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path

from django_lookup.enums import DecisionAuto, DecisionSource, FingerprintKind
from django_lookup.models import Fingerprint
from django_lookup.providers.registry import get_provider
from django_lookup.schemas.requests.lookup import Attrs, LookupQuery
from django_lookup.services import blocking, query_parser
from django_lookup.services.fingerprint_service import pick_name
from django_lookup.services.image_service import QueryImage
from django_lookup.services.lookup_service import check

CSV_FIELDS = ("query_kind", "query_ref", "candidate_kind", "candidate_ref", "label", "why")
LABELS = frozenset({"match", "variant", "no"})
MATCH_LABELS = frozenset({"match"})
# "variant" is a findable duplicate for blocking recall, but a negative for the binary P/R/F1 sweep
# unless a caller explicitly asks otherwise — see the two threshold sweeps in `EvalReport`.
MATCH_OR_VARIANT_LABELS = frozenset({"match", "variant"})
NAME_BLOCK_LIMIT = 50
IMAGE_BLOCK_LIMIT = 20
UNKNOWN_KIND = "unknown kind"
BAD_LABEL = "bad label"
STALE_REF = "stale ref"


@dataclass(frozen=True)
class PairRow:
    query_kind: str
    query_ref: str
    candidate_kind: str
    candidate_ref: str
    label: str
    why: str = ""


@dataclass(frozen=True)
class _Outcome:
    row: PairRow
    score: int
    decision: str
    retrieved: bool  # True: check() returned this candidate at all (whatever its score)
    name_blocked: bool
    image_blocked: bool | None  # None: no embeddings on the query side to test with


@dataclass(frozen=True)
class ThresholdReport:
    positives: str
    threshold: int
    precision: float
    recall: float
    f1: float
    tp: int
    fp: int
    fn: int
    tn: int


@dataclass(frozen=True)
class EvalReport:
    total: int
    skipped: int
    skip_reasons: dict[str, int]
    not_retrieved: int
    thresholds: list[ThresholdReport]
    thresholds_with_variant: list[ThresholdReport]
    confusion: dict[tuple[str, str], int]  # (true label, engine decision) -> count
    recall_at_50_name: float | None
    recall_at_20_image: float | None


def load_pairs(path: str) -> list[PairRow]:
    """Parse the labelled CSV. Blank `query_ref` rows (stray blank lines) are dropped silently."""
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        return [_row(raw) for raw in csv.DictReader(handle) if (raw.get("query_ref") or "").strip()]


def _row(raw: dict) -> PairRow:
    values = {field: (raw.get(field) or "").strip() for field in CSV_FIELDS}
    return PairRow(**{**values, "label": values["label"].lower()})


def evaluate(
    pairs: list[PairRow], thresholds: list[int], image_only: bool = False, log_decisions: bool = False
) -> EvalReport:
    """`image_only=True` reports only `recall_at_20_image` — everything else needs the full pipeline.

    `log_decisions=False` (the default) runs `check()` with `log=False`: a calibration run over a
    large pairs file must not leave one `DedupDecision` row per candidate behind. Pass `True` when the
    audit trail itself is what is being inspected — the rows still carry `DecisionSource.LOOKUP_EVAL`.
    """
    build_row = _image_only_row if image_only else partial(_evaluate_row, log_decisions=log_decisions)
    outcomes, skip_reasons = _run(pairs, build_row)
    if image_only:
        embedded = [o for o in outcomes if o.image_blocked is not None]
        return _report(pairs, outcomes, skip_reasons, [], [], {}, None, _recall(embedded, _image_hit))
    return _report(
        pairs,
        outcomes,
        skip_reasons,
        [_threshold_report(outcomes, t, MATCH_LABELS, "match") for t in thresholds],
        [_threshold_report(outcomes, t, MATCH_OR_VARIANT_LABELS, "match+variant") for t in thresholds],
        _confusion(outcomes),
        _recall(outcomes, _name_hit),
        _recall([o for o in outcomes if o.image_blocked is not None], _image_hit),
    )


def _run(pairs: list[PairRow], build_row: Callable) -> tuple[list[_Outcome], dict[str, int]]:
    outcomes: list[_Outcome] = []
    skip_reasons: dict[str, int] = {}
    for row in pairs:
        outcome, reason = build_row(row)
        if outcome is None:
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
        else:
            outcomes.append(outcome)
    return outcomes, skip_reasons


def _report(pairs, outcomes, skip_reasons, thresholds, thresholds_variant, confusion, recall50, recall20):
    return EvalReport(
        total=len(pairs),
        skipped=len(pairs) - len(outcomes),
        skip_reasons=skip_reasons,
        not_retrieved=sum(1 for o in outcomes if not o.retrieved),
        thresholds=thresholds,
        thresholds_with_variant=thresholds_variant,
        confusion=confusion,
        recall_at_50_name=recall50,
        recall_at_20_image=recall20,
    )


def _name_hit(outcome: _Outcome) -> bool:
    return outcome.name_blocked


def _image_hit(outcome: _Outcome) -> bool | None:
    return outcome.image_blocked


def _skip_reason(row: PairRow) -> str | None:
    if row.label not in LABELS:
        return BAD_LABEL
    if row.query_kind not in FingerprintKind.values or row.candidate_kind not in FingerprintKind.values:
        return UNKNOWN_KIND
    return None


def _query_or_reason(kind: str, ref: str) -> tuple[LookupQuery | None, str | None]:
    """The provider's own view of this ref, turned into a query — never raises."""
    try:
        item = get_provider(kind).get_item(ref)
    except ValueError:
        return None, UNKNOWN_KIND  # kind is a valid FingerprintKind but has no registered provider
    except LookupError:
        return None, STALE_REF  # provider no longer serves this ref
    query = _to_query(item)
    return (query, None) if query is not None else (None, STALE_REF)


def _to_query(item) -> LookupQuery | None:
    """Mirrors `build_fingerprint`: same name-language priority, and the physical attrs L6/L7 score
    on — the previous version dropped `item.attrs` entirely, so weight/dimensions never fired."""
    name = pick_name(item.name_by_lang) or None
    attrs = Attrs(**{key: value for key, value in item.attrs.items() if key in Attrs.model_fields})
    try:
        return LookupQuery(ean=item.gtin, brand=item.brand, mpn=item.mpn, name=name, attrs=attrs)
    except ValueError:  # none of ean/brand/mpn/name carried a signal
        return None


def _name_leg(parsed) -> query_parser.ParsedQuery:
    """Isolate the trigram leg: no exact-key leg, no image."""
    return replace(parsed, gtin14="", mpn_norm="", sku="")


def _image_leg(parsed) -> query_parser.ParsedQuery:
    """Isolate the image legs: no exact-key leg, no trigram leg (name blanked too)."""
    return replace(parsed, gtin14="", mpn_norm="", sku="", name_norm="", name_tokens=[])


def _name_blocked(row: PairRow, query: LookupQuery) -> bool:
    parsed = _name_leg(query_parser.parse(query))
    pool = blocking.candidates(parsed, [row.candidate_kind], limit=NAME_BLOCK_LIMIT)
    return any(candidate.ref == row.candidate_ref for candidate in pool)


def _query_image(kind: str, ref: str) -> QueryImage | None:
    """The query's own already-embedded Fingerprint row. `phash` is required by the pHash leg's SQL
    (`bit_count`, never optional there) — a row with a vector but no pHash yet is skipped rather than
    fed a fabricated `0`, which would otherwise fake near-exact hash hits against real zeros."""
    row = Fingerprint.objects.filter(kind=kind, ref=ref).first()
    if row is None or row.image_vec is None or row.phash is None:
        return None
    return QueryImage(phash=row.phash, model_id=row.vec_model or "", vector=list(row.image_vec))


def _image_blocked(row: PairRow, query: LookupQuery) -> bool | None:
    image = _query_image(row.query_kind, row.query_ref)
    if image is None:
        return None
    parsed = _image_leg(query_parser.parse(query))
    pool = blocking.candidates(parsed, [row.candidate_kind], image=image, limit=IMAGE_BLOCK_LIMIT)
    return any(candidate.ref == row.candidate_ref for candidate in pool)


def _image_only_row(row: PairRow) -> tuple[_Outcome | None, str | None]:
    reason = _skip_reason(row)
    if reason:
        return None, reason
    query, reason = _query_or_reason(row.query_kind, row.query_ref)
    if query is None:
        return None, reason
    image_blocked = _image_blocked(row, query)
    outcome = _Outcome(row, 0, DecisionAuto.NO_MATCH, False, False, image_blocked)
    return outcome, None


def _evaluate_row(row: PairRow, log_decisions: bool = False) -> tuple[_Outcome | None, str | None]:
    reason = _skip_reason(row)
    if reason:
        return None, reason
    query, reason = _query_or_reason(row.query_kind, row.query_ref)
    if query is None:
        return None, reason
    scoped = query.model_copy(update={"scope": [row.candidate_kind], "limit": blocking.CANDIDATE_LIMIT})
    result = check(scoped, source=DecisionSource.LOOKUP_EVAL, log=log_decisions)
    hit = next((candidate for candidate in result.candidates if candidate.ref == row.candidate_ref), None)
    outcome = _Outcome(
        row=row,
        score=hit.score if hit else 0,
        decision=hit.decision if hit else DecisionAuto.NO_MATCH,
        retrieved=hit is not None,
        name_blocked=_name_blocked(row, query),
        image_blocked=_image_blocked(row, query),
    )
    return outcome, None


def _threshold_report(
    outcomes: list[_Outcome], threshold: int, positive_labels: frozenset, label: str
) -> ThresholdReport:
    tp = fp = fn = tn = 0
    for outcome in outcomes:
        predicted = outcome.score >= threshold
        actual = outcome.row.label in positive_labels
        tp += predicted and actual
        fp += predicted and not actual
        fn += (not predicted) and actual
        tn += (not predicted) and not actual
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return ThresholdReport(label, threshold, precision, recall, f1, tp, fp, fn, tn)


def _confusion(outcomes: list[_Outcome]) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    for outcome in outcomes:
        key = (outcome.row.label, outcome.decision)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _recall(outcomes: list[_Outcome], blocked: Callable[[_Outcome], bool | None]) -> float | None:
    positives = [o for o in outcomes if o.row.label in MATCH_OR_VARIANT_LABELS]
    if not positives:
        return None
    hits = sum(1 for o in positives if blocked(o))
    return hits / len(positives)
