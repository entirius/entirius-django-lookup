# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The two entry points behind the API: `search` (ranked hits) and `check` (hits + verdict).

`search` answers "do we have something like this?" and stays free of verdicts — `Hit`, its return
type, has no `score`/`decision` field at all, so a direct caller cannot read one. Its `similarity` is
`PairScore.relevance`: 0-100 relevance to the query *as given* (a photo-only query is judged by the
photo — `scoring.relevance`), ranked by it. `check` is the thin dedup layer on top: the same pipeline
(parse -> block -> score), run once, with `Candidate` (`Hit` plus the verdict), ranked by the additive
dedup score, and an audit row per candidate.
"""

import logging
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from types import ModuleType

from django.contrib.auth.base_user import AbstractBaseUser

from django_lookup.enums import DecisionAuto, DecisionSource, MatchKind
from django_lookup.models import DedupDecision, Fingerprint
from django_lookup.providers.base import BasicData
from django_lookup.providers.registry import get_provider
from django_lookup.schemas.requests.lookup import LookupQuery
from django_lookup.services import blocking, image_service, query_parser
from django_lookup.services.image_service import QueryImage
from django_lookup.services.query_parser import ParsedQuery
from django_lookup.services.scoring import PairScore, Reason, score_pair
from django_lookup.settings import get_providers as configured_providers
from django_lookup.settings import image_enabled

logger = logging.getLogger("process")

WARNING_IMAGE_UNAVAILABLE = "image_layer_unavailable"
WARNING_KIND_UNAVAILABLE = "kind_unavailable:{kind}"
# Best decision wins when a check returns several candidates.
_DECISION_RANK = {DecisionAuto.MATCH: 2, DecisionAuto.REVIEW: 1, DecisionAuto.NO_MATCH: 0}


@dataclass(frozen=True)
class Hit:
    """One ranked candidate — `/search/`'s whole answer. No score, no decision: see module docstring."""

    kind: str
    ref: str
    similarity: int
    match: MatchKind
    reasons: list[Reason] = field(default_factory=list)
    basic: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Candidate:
    """A `Hit` plus the verdict — what `/check/` adds on top."""

    kind: str
    ref: str
    similarity: int
    match: MatchKind
    score: int
    decision: str
    reasons: list[Reason] = field(default_factory=list)
    basic: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchResult:
    parsed: ParsedQuery
    hits: list[Hit]
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CheckResult:
    decision: str
    parsed: ParsedQuery
    candidates: list[Candidate]
    warnings: list[str] = field(default_factory=list)


def _scope(query: LookupQuery) -> tuple[list[str], list[str]]:
    """Drop kinds without a registered provider — a misconfigured scope warns, it does not fail."""
    known = configured_providers()
    scope = [kind for kind in query.scope if kind in known]
    warnings = [WARNING_KIND_UNAVAILABLE.format(kind=kind) for kind in query.scope if kind not in known]
    return scope, warnings


def _query_image(query: LookupQuery, data: bytes | None) -> tuple[QueryImage | None, list[str]]:
    """Uploaded bytes win over `image_url`. Anything that stops the image layer is a warning, not a
    failure — except bytes that are not a usable image, which is the caller's mistake (ValueError)."""
    if data is None and not query.image_url:
        return None, []
    if not image_enabled():
        return None, [WARNING_IMAGE_UNAVAILABLE]
    if data is None:
        try:
            # No `allowed_hosts` — `image_url` is request-supplied (an admin-JWT caller's own
            # input), so it must never reach the operator-configured embed allowlist (SSRF oracle
            # against internal hosts). See fetch_remote's docstring.
            data = image_service.fetch_remote(str(query.image_url))
        except (OSError, ValueError) as exc:
            logger.warning("lookup: image_url unusable (%s)", exc)
            return None, [WARNING_IMAGE_UNAVAILABLE]
    prepared = image_service.prepare_query(data)
    return prepared, [WARNING_IMAGE_UNAVAILABLE] if prepared.degraded else []


def _basic_dict(data: BasicData, detail_url: str) -> dict[str, str]:
    return {
        "sku": data.ref,
        "name": data.name,
        "brand": data.brand,
        "ean": data.gtin,
        "main_image_url": data.image_url,
        "detail_url": detail_url,
    }


def _display_batch(kind: str, provider: ModuleType, refs: list[str]) -> dict[str, dict[str, str]]:
    """One round trip for the whole hit list instead of two per ref (providers/base.py).

    `basics`/`detail_urls` MUST omit unknown refs, not raise — but a provider that gets this wrong
    (the batch form is optional, easy to get wrong on a first implementation) falls back to the
    singular `basic`/`detail_url` pair instead of failing the whole request, exactly like a provider
    that never implemented the batch form in the first place.
    """
    try:
        basics, urls = provider.basics(refs), provider.detail_urls(refs)
    except LookupError:
        return _display_singular(kind, provider, refs)
    display = {ref: _basic_dict(data, urls[ref]) for ref, data in basics.items() if ref in urls}
    if missing := set(refs) - set(display):
        logger.warning("lookup: stale fingerprints %s:%s — provider no longer serves them", kind, sorted(missing))
    return display


def _display_singular(kind: str, provider: ModuleType, refs: list[str]) -> dict[str, dict[str, str]]:
    """Fallback for a provider without `basics`/`detail_urls`: one call per ref, per kind."""
    display = {}
    for ref in refs:
        try:
            display[ref] = _basic_dict(provider.basic(ref), provider.detail_url(ref))
        except LookupError:
            logger.warning("lookup: stale fingerprint %s:%s — provider no longer serves it", kind, ref)
    return display


def _display(kind: str, refs: list[str]) -> dict[str, dict[str, str]]:
    """Inline display data per hit — no full product payload, details go through `detail_url`.

    Uses the provider's batch entry points when it defines both; a provider that only implements the
    singular `basic`/`detail_url` pair (the protocol's required minimum) falls back to one call per ref.
    """
    provider = get_provider(kind)
    if hasattr(provider, "basics") and hasattr(provider, "detail_urls"):
        return _display_batch(kind, provider, refs)
    return _display_singular(kind, provider, refs)


def _scored_with_display(
    scored: list[tuple[Fingerprint, PairScore]],
) -> Iterator[tuple[Fingerprint, PairScore, dict[str, str]]]:
    display: dict[str, dict[str, dict[str, str]]] = {}
    for kind in {row.kind for row, _ in scored}:
        display[kind] = _display(kind, [row.ref for row, _ in scored if row.kind == kind])
    for row, pair in scored:
        if row.ref in display[row.kind]:
            yield row, pair, display[row.kind][row.ref]


def _hits(scored: list[tuple[Fingerprint, PairScore]]) -> list[Hit]:
    return [
        Hit(
            kind=row.kind,
            ref=row.ref,
            similarity=pair.relevance,
            match=pair.match,
            reasons=pair.reasons,
            basic=basic,
        )
        for row, pair, basic in _scored_with_display(scored)
    ]


def _candidates(scored: list[tuple[Fingerprint, PairScore]]) -> list[Candidate]:
    return [
        Candidate(
            kind=row.kind,
            ref=row.ref,
            similarity=pair.relevance,
            match=pair.match,
            score=pair.score,
            decision=pair.decision,
            reasons=pair.reasons,
            basic=basic,
        )
        for row, pair, basic in _scored_with_display(scored)
    ]


_Scored = tuple[Fingerprint, PairScore]
_RankKey = Callable[[_Scored], tuple]


def _by_relevance(pair: _Scored) -> tuple:
    """`search`: what matches the query best, dedup score and name similarity as tie-breaks."""
    return pair[1].relevance, pair[1].score, float(pair[0].name_similarity or 0.0)


def _by_score(pair: _Scored) -> tuple:
    """`check`: the additive dedup score, as before."""
    return pair[1].score, float(pair[0].name_similarity or 0.0)


def _pipeline(
    query: LookupQuery, image_data: bytes | None, rank: _RankKey
) -> tuple[ParsedQuery, list[_Scored], list[str]]:
    """Parse, block and score — the one pass shared by `search` and `check`; `rank` orders the cut."""
    parsed = query_parser.parse(query)
    scope, warnings = _scope(query)
    image, image_warnings = _query_image(query, image_data)
    rows = blocking.candidates(parsed, scope, image=image) if scope else []
    scored = [(row, score_pair(parsed, row, image)) for row in rows]
    scored.sort(key=rank, reverse=True)
    return parsed, scored[: query.limit], warnings + image_warnings


def search(query: LookupQuery, image_data: bytes | None = None) -> SearchResult:
    """Ranked candidates with their reasons — no verdict (that is what `check` is for).

    `image_data` are the raw bytes of an uploaded picture; they are hashed, embedded and dropped.
    """
    parsed, scored, warnings = _pipeline(query, image_data, _by_relevance)
    return SearchResult(parsed=parsed, hits=_hits(scored), warnings=warnings)


def _query_json(parsed: ParsedQuery) -> dict:
    return {key: str(value) if isinstance(value, Decimal) else value for key, value in asdict(parsed).items()}


def _log_decisions(
    parsed: ParsedQuery, candidates: list[Candidate], user: AbstractBaseUser | None, source: str
) -> None:
    """Append-only training set: the feature vector behind every candidate the caller was shown."""
    DedupDecision.objects.bulk_create(
        [
            DedupDecision(
                query=_query_json(parsed),
                candidate_kind=candidate.kind,
                candidate_ref=candidate.ref,
                score=candidate.score,
                features=[asdict(reason) for reason in candidate.reasons],
                decision_auto=candidate.decision,
                user=user,
                source=source,
            )
            for candidate in candidates
        ]
    )


def check(
    query: LookupQuery,
    user: AbstractBaseUser | None = None,
    image_data: bytes | None = None,
    source: str = DecisionSource.API_CHECK,
    log: bool = True,
) -> CheckResult:
    """`search`'s pipeline plus a verdict per candidate. The overall decision is the best one among them.

    `source` tags the logged DedupDecision rows with the caller (API, PIM create hook, enrichment
    proposal). `log=False` skips the audit write entirely — the calibration harness runs `check()`
    thousands of times per pairs file and does not want a `DedupDecision` row for every one of them.
    """
    parsed, scored, warnings = _pipeline(query, image_data, _by_score)
    candidates = _candidates(scored)
    decision = max(
        (candidate.decision for candidate in candidates),
        key=lambda value: _DECISION_RANK[value],
        default=DecisionAuto.NO_MATCH,
    )
    if log:
        _log_decisions(parsed, candidates, user, source)
    return CheckResult(decision=decision, parsed=parsed, candidates=candidates, warnings=warnings)
