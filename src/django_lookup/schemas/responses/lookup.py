# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Response schemas for POST /search/ and POST /check/.

`reasons` are part of the contract (a reviewer must see why a candidate is proposed), and so is
`query_parsed` — it shows what the engine understood, which is the first thing to check when a
lookup returns nothing.
"""

from decimal import Decimal

from pydantic import BaseModel, Field

from django_lookup.enums import DecisionAuto, FingerprintKind


class Observed(BaseModel):
    query: str = Field(description="Value taken from the query", examples=["05901234123457"])
    candidate: str = Field(description="Value of the candidate", examples=["05901234123457"])


class ReasonOut(BaseModel):
    code: str = Field(description="Stable machine code of the evidence", examples=["gtin_exact"])
    label: str = Field(description="Human-readable explanation", examples=["GTIN 05901234123457 identical"])
    score: int = Field(description="Points this evidence contributed (may be negative)", examples=[60])
    observed: Observed = Field(description="Both sides of the comparison")


class BasicOut(BaseModel):
    """Inline display data — never a full product payload; details live behind `detail_url`."""

    sku: str = Field(description="Catalog reference (PIM sku, atlas `<source>:<external_id>`)")
    name: str = Field(description="Product name in the catalog's default language")
    brand: str = Field(default="", description="Brand as stored in the catalog")
    ean: str = Field(default="", description="GTIN as stored in the catalog")
    main_image_url: str = Field(default="", description="Main picture, empty when the item has none")
    detail_url: str = Field(description="Admin API deep link of the catalog owning the item")


class QueryParsed(BaseModel):
    """What the parser made of the query — the exact keys blocking and scoring worked with."""

    gtin14: str = ""
    gtin_trusted: bool = False
    brand_norm: str = ""
    mpn_norm: str = ""
    sku: str = ""
    name_norm: str = ""
    name_tokens: list[str] = Field(default_factory=list, description="Sorted unique tokens used for Jaccard")
    pack_qty: int | None = None
    color: str = ""
    size: str = ""
    weight: Decimal | None = None
    width: Decimal | None = None
    height: Decimal | None = None
    deep: Decimal | None = None


class SearchHit(BaseModel):
    kind: FingerprintKind = Field(description="Catalog the hit comes from")
    ref: str = Field(description="Fingerprint reference inside that catalog")
    similarity: int = Field(description="Strongest single piece of evidence, 0-100", examples=[60])
    reasons: list[ReasonOut] = Field(description="Evidence, strongest first")
    basic: BasicOut


class CheckCandidate(SearchHit):
    score: int = Field(description="Total evidence, clamped to 0-100", examples=[82])
    decision: DecisionAuto = Field(description="Verdict for this candidate")


class SearchResponse(BaseModel):
    query_parsed: QueryParsed
    hits: list[SearchHit] = Field(description="Ranked candidates, best first")
    warnings: list[str] = Field(
        default_factory=list,
        description="Degradations that did not stop the answer",
        examples=[["image_layer_unavailable"]],
    )


class CheckResponse(BaseModel):
    decision: DecisionAuto = Field(description="Best verdict among the candidates")
    query_parsed: QueryParsed
    candidates: list[CheckCandidate] = Field(description="Ranked candidates, best first")
    warnings: list[str] = Field(default_factory=list, description="Degradations that did not stop the answer")
