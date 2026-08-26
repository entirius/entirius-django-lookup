# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Human verdicts on a (subject, candidate) pair — the write/read side of `DedupDecision`.

`lookup_service.check` logs what the *machine* decided (`source="api_check"`, no subject: the CMS
box has none). This module logs what a *human* decided about a concrete pair — an operator
accepting or rejecting an enrichment proposal — and answers the only question a proposing caller
has afterwards: "which candidates were already rejected for this subject?".

Rejections are durable on purpose. The enrichment bus blocks a re-proposal only for its own
`cooldown_days`; without this log the same rejected pair would come back forever.
"""

from dataclasses import dataclass

from django.contrib.auth.base_user import AbstractBaseUser

from django_lookup.enums import DecisionHuman, DecisionSource
from django_lookup.models import DedupDecision


@dataclass(frozen=True)
class Verdict:
    """One human answer about a pair: `subject_ref` is the row that was being deduplicated."""

    subject_ref: str
    candidate_kind: str
    candidate_ref: str
    decision_human: str
    decision_auto: str
    score: int


def record(verdict: Verdict, user: AbstractBaseUser | None = None) -> DedupDecision:
    """Append the verdict to the log (`source="proposal"` — the only human surface in v1)."""
    if verdict.decision_human not in DecisionHuman.values:
        raise ValueError(f"unknown human decision {verdict.decision_human!r}")
    return DedupDecision.objects.create(
        subject_ref=verdict.subject_ref,
        candidate_kind=verdict.candidate_kind,
        candidate_ref=verdict.candidate_ref,
        score=verdict.score,
        decision_auto=verdict.decision_auto,
        decision_human=verdict.decision_human,
        user=user,
        source=DecisionSource.PROPOSAL,
    )


def rejected_pairs(subject_refs: list[str]) -> set[tuple[str, str]]:
    """`{(subject_ref, candidate_ref)}` a human already rejected — one query for a whole page."""
    if not subject_refs:
        return set()
    rows = DedupDecision.objects.filter(
        subject_ref__in=subject_refs, decision_human=DecisionHuman.REJECTED
    ).values_list("subject_ref", "candidate_ref")
    return set(rows)
