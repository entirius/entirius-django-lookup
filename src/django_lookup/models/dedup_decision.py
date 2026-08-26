# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.conf import settings
from django.db import models

from django_lookup.enums import DecisionAuto, DecisionHuman, DecisionSource, FingerprintKind


# Plain models.Model on purpose: append-only log — no modified_at, no soft delete.
class DedupDecision(models.Model):
    """Audit + training log: the feature vector behind every automatic decision and the human verdict.

    Append-only. `features` carries the reasons list ({code, label, score, observed}) exactly as
    returned by the API, so a later classifier can be trained without an API change.
    `subject_ref` + `candidate_ref` are the pair a human verdict answers (see `services.dedup_log`).
    """

    query = models.JSONField(default=dict, blank=True)
    # The catalog row the query stood for, when there is one (`<source_idx>:<external_id>` for an
    # atlas source product, sku for a PIM product). Empty for a free-text search from the CMS box —
    # only a proposal / create-hook decision has a subject. It is what makes a *pair* addressable:
    # "was this candidate already rejected for this subject?" (django_atlas enrichment adapter).
    subject_ref = models.CharField(max_length=255, blank=True, default="", db_index=True)
    candidate_kind = models.CharField(max_length=32, choices=FingerprintKind.choices)
    candidate_ref = models.CharField(max_length=255)
    score = models.IntegerField()
    features = models.JSONField(default=list, blank=True)
    decision_auto = models.CharField(max_length=16, choices=DecisionAuto.choices)
    decision_human = models.CharField(max_length=16, choices=DecisionHuman.choices, blank=True, default="")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    source = models.CharField(max_length=16, choices=DecisionSource.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["candidate_kind", "candidate_ref"], name="lookup_dec_candidate_idx"),
            models.Index(fields=["-created_at"], name="lookup_dec_created_idx"),
        ]

    def __str__(self) -> str:
        return f"DedupDecision({self.candidate_kind}:{self.candidate_ref} {self.decision_auto} {self.score})"
