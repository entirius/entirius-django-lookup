# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django_utils.models.base_model import BaseModel
from pgvector.django import HalfVectorField, HnswIndex

from django_lookup.constants import EMBEDDING_DIM
from django_lookup.enums import FingerprintKind


class Fingerprint(BaseModel):
    """Normalised, index-ready view of one catalog item (PIM product or atlas source product).

    One row per (kind, ref); rebuilt by the provider backfill / signals — never edited by hand.
    Only normalised columns live here; raw values stay in the source module.
    """

    kind = models.CharField(max_length=32, choices=FingerprintKind.choices)
    ref = models.CharField(max_length=255)

    gtin14 = models.CharField(max_length=14, blank=True, default="", db_index=True)
    gtin_trusted = models.BooleanField(default=False)
    brand_norm = models.CharField(max_length=255, blank=True, default="")
    mpn_norm = models.CharField(max_length=255, blank=True, default="")
    name_norm = models.TextField(blank=True, default="")
    name_tokens = ArrayField(models.TextField(), default=list, blank=True)
    pack_qty = models.PositiveIntegerField(null=True, blank=True)
    color = models.CharField(max_length=64, blank=True, default="")
    size = models.CharField(max_length=64, blank=True, default="")
    weight = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    width = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    height = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    deep = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)

    phash = models.BigIntegerField(null=True, blank=True)
    image_vec = HalfVectorField(dimensions=EMBEDDING_DIM, null=True, blank=True)
    vec_model = models.CharField(max_length=64, blank=True, default="")
    image_sha1 = models.CharField(max_length=40, blank=True, default="")

    source_updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["kind", "ref"], name="uq_lookup_fingerprint_kind_ref")]
        indexes = [
            models.Index(fields=["brand_norm", "mpn_norm"], name="lookup_fp_brand_mpn_idx"),
            GinIndex(fields=["name_norm"], name="lookup_fp_name_trgm_idx", opclasses=["gin_trgm_ops"]),
            HnswIndex(
                fields=["image_vec"],
                name="lookup_fp_image_vec_hnsw_idx",
                m=16,
                ef_construction=64,
                opclasses=["halfvec_cosine_ops"],
            ),
        ]

    def __str__(self) -> str:
        return f"Fingerprint({self.kind}:{self.ref})"
