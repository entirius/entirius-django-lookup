# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Request schema shared by POST /search/ and POST /check/ — the only input validation layer.

Format and bounds live here (KISS: the services never re-check them). `scope` and `limit` are
validated against the enum / range *before* they reach SQL.
"""

from decimal import Decimal

from pydantic import BaseModel, Field, HttpUrl, model_validator

from django_lookup.enums import FingerprintKind

MAX_LIMIT = 20
DEFAULT_LIMIT = 10
# One of these must be present — an empty query would block the whole table. `has_image` is the
# server's own marker for an uploaded file, which is a signal the schema cannot see for itself.
REQUIRED_ANY = ("q", "ean", "name", "mpn", "sku", "image_url", "has_image")


class Attrs(BaseModel):
    """Physical / variant attributes the caller already knows; they win over values parsed from `q`."""

    weight: Decimal | None = Field(None, ge=0, description="Weight in kg", examples=["0.150"])
    width: Decimal | None = Field(None, ge=0, description="Width in cm")
    height: Decimal | None = Field(None, ge=0, description="Height in cm")
    deep: Decimal | None = Field(None, ge=0, description="Depth in cm")
    color: str | None = Field(None, max_length=64, description="Colour, any language")
    size: str | None = Field(None, max_length=64, description="Size, e.g. `xl`, `42`")
    pack_qty: int | None = Field(None, ge=1, le=9999, description="Units per pack (multipack marker)")


class LookupQuery(BaseModel):
    """Free text and/or explicit identifiers. Explicit fields win over anything parsed out of `q`."""

    q: str | None = Field(None, max_length=500, description="Free text; a GTIN inside it is detected")
    ean: str | None = Field(None, max_length=32, description="GTIN-8/12/13/14, any punctuation")
    brand: str | None = Field(None, max_length=255, description="Brand name", examples=["Bosch"])
    mpn: str | None = Field(None, max_length=255, description="Manufacturer part number")
    sku: str | None = Field(None, max_length=255, description="Catalog reference (PIM sku / atlas ref)")
    name: str | None = Field(None, max_length=500, description="Product name; replaces the text part of `q`")
    attrs: Attrs | None = None
    scope: list[FingerprintKind] = Field(
        default_factory=lambda: list(FingerprintKind), description="Catalogs to search (default: all)"
    )
    limit: int = Field(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT, description="Maximum hits returned")
    image_url: HttpUrl | None = Field(
        None, description="Picture to look up; the server fetches it through its SSRF guard, never stores it"
    )
    has_image: bool = Field(
        False,
        description="Server-set: true when the multipart request carried an `image` file. Client values are ignored.",
        json_schema_extra={"readOnly": True},
    )

    @model_validator(mode="after")
    def _require_one_signal(self) -> "LookupQuery":
        if not any(getattr(self, field) for field in REQUIRED_ANY):
            raise ValueError(f"at least one of {', '.join(REQUIRED_ANY[:-1])} or an image is required")
        return self
