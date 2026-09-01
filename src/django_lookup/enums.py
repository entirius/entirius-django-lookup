# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from enum import StrEnum

from django.db import models


class FingerprintKind(models.TextChoices):
    """Catalog a fingerprint row describes; the key into settings.LOOKUP_PROVIDERS."""

    PIM_PRODUCT = "pim_product", "PIM product"
    ATLAS_SOURCE_PRODUCT = "atlas_source_product", "Atlas source product"


class DecisionAuto(models.TextChoices):
    MATCH = "match", "Match"
    REVIEW = "review", "Review"
    NO_MATCH = "no_match", "No match"


class DecisionHuman(models.TextChoices):
    ACCEPTED = "accepted", "Accepted"
    REJECTED = "rejected", "Rejected"


class DecisionSource(models.TextChoices):
    API_CHECK = "api_check", "API check"
    CREATE_HOOK = "create_hook", "PIM create hook"
    PROPOSAL = "proposal", "Enrichment proposal"
    LOOKUP_EVAL = "lookup_eval", "Calibration eval (lookup_eval)"


class MatchKind(StrEnum):
    """How a `/search/` hit relates to the query — API vocabulary, never a column.

    `exact`: the same identifier (trusted GTIN, brand+MPN) or the same picture file (pHash near-exact
    the embedding does not contradict — `scoring.SAME_FILE_COSINE`).
    `similar`: something agreed, short of that. `none`: a blocking neighbour nothing agreed on.
    """

    EXACT = "exact"
    SIMILAR = "similar"
    NONE = "none"
