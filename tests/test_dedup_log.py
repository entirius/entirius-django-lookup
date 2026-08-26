# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Human verdicts on a (subject, candidate) pair — the durable "no" behind the proposal flow."""

import pytest

from django_lookup.enums import DecisionAuto, DecisionHuman, DecisionSource, FingerprintKind
from django_lookup.services import dedup_log

pytestmark = pytest.mark.django_db


def _verdict(subject: str = "acme:EXT-1", candidate: str = "SKU-1", decision: str = DecisionHuman.REJECTED):
    return dedup_log.Verdict(
        subject_ref=subject,
        candidate_kind=FingerprintKind.PIM_PRODUCT,
        candidate_ref=candidate,
        decision_human=decision,
        decision_auto=DecisionAuto.REVIEW,
        score=60,
    )


def test_record_appends_the_verdict_as_a_proposal_decision():
    row = dedup_log.record(_verdict(decision=DecisionHuman.ACCEPTED))

    assert (row.subject_ref, row.candidate_ref) == ("acme:EXT-1", "SKU-1")
    assert row.decision_human == DecisionHuman.ACCEPTED
    assert row.decision_auto == DecisionAuto.REVIEW
    assert row.source == DecisionSource.PROPOSAL


def test_record_refuses_an_unknown_human_decision():
    with pytest.raises(ValueError, match="unknown human decision"):
        dedup_log.record(_verdict(decision="maybe"))


def test_rejected_pairs_returns_only_rejections_for_the_asked_subjects():
    dedup_log.record(_verdict(candidate="SKU-1"))
    dedup_log.record(_verdict(candidate="SKU-2", decision=DecisionHuman.ACCEPTED))
    dedup_log.record(_verdict(subject="acme:EXT-2", candidate="SKU-3"))

    assert dedup_log.rejected_pairs(["acme:EXT-1"]) == {("acme:EXT-1", "SKU-1")}
    assert dedup_log.rejected_pairs([]) == set()
