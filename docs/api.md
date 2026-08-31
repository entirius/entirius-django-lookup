---
title: Admin API
description: POST /search/ and /check/ — request and response shapes, auth, throttling, errors.
---

Two endpoints under `/api/lookup/v2/admin/` (the host mounts `django_lookup.urls`).
`POST /search/` returns ranked candidates with the evidence behind each one — **no verdict**, and
nothing written. `POST /check/` runs the same pipeline plus pairwise scoring: every candidate gets a
`score`, a `match` / `review` / `no_match` decision, and one `DedupDecision` row
(`source="api_check"` unless the caller passes another `DecisionSource`, e.g. `create_hook`).

Views are thin (`api/admin/views/lookup_views.py`): parse into `LookupQuery`, call
`services.lookup_service`, serialise. No ORM in the view layer.

## Auth and throttling

| | |
|---|---|
| Authentication | `JWTAuthentication` |
| Permission | `IsAdminUser` — `IsAuthenticated` alone would let storefront customers read the catalogs |
| Throttle, text | `LookupThrottle`, scope `lookup_check`, fallback `60/min` |
| Throttle, image | `LookupImageThrottle`, scope `lookup_image`, fallback `30/min` |

A request carrying a picture — a multipart `image` or a JSON `image_url` — draws on the image bucket,
because it pays for a guarded outbound fetch plus a synchronous embedding call. The host overrides the
rates via `DEFAULT_THROTTLE_RATES["lookup_check" | "lookup_image"]`; a missing or malformed rate falls
back to the class default, never to "no throttle".

## Request

One `LookupQuery` shape, as JSON or `multipart/form-data` (same field names as form values, plus an
`image` file). At least one of `q`, `ean`, `name`, `mpn`, `sku`, `image_url` or `image` is required —
the schema is the only validation layer, every bound below is enforced before SQL.

| Field | Type | Notes |
|---|---|---|
| `q` | string ≤ 500 | free text; a GTIN inside it is detected and pulled out |
| `ean` | string ≤ 32 | GTIN-8/12/13/14, any punctuation |
| `brand` | string ≤ 255 | |
| `mpn` | string ≤ 255 | manufacturer part number |
| `sku` | string ≤ 255 | catalog reference (PIM sku / atlas ref) |
| `name` | string ≤ 500 | replaces the text part of `q` when present |
| `attrs` | object \| null | `weight`/`width`/`height`/`deep` (Decimal, kg/cm), `color`, `size` (≤ 64), `pack_qty` (1–9999) |
| `scope` | list of `pim_product` \| `atlas_source_product` | default: every kind |
| `limit` | int 1–20 | default 10 |
| `image_url` | URL \| null | fetched server-side through the SSRF guard, never stored |
| `has_image` | bool | **server-set** — true only when multipart carried `image`; a client value is ignored |

Explicit fields win over what the parser extracts from `q` — the caller is assumed to know more.

`image`: JPEG / PNG / WEBP, ≤ 5 MB. Verified, reopened, EXIF-oriented, cropped, downscaled — and
**never persisted**. Unusable bytes are a 400, not a 500.

## Response

`SearchResponse`:

```json
{
  "query_parsed": {"gtin14": "", "brand_norm": "bosch", "name_norm": "cordless drill 18v", "...": "..."},
  "hits": [
    {
      "kind": "pim_product",
      "ref": "SKU-123",
      "similarity": 100,
      "match": "exact",
      "reasons": [{"code": "gtin_exact", "label": "GTIN 05901234123457 identical", "score": 60,
                   "observed": {"query": "05901234123457", "candidate": "05901234123457"}}],
      "basic": {"sku": "SKU-123", "name": "...", "brand": "Bosch", "ean": "05901234123457",
                "main_image_url": "https://...", "detail_url": "/api/pim/v2/admin/products/SKU-123/"}
    }
  ],
  "warnings": []
}
```

`CheckResponse` adds a top-level `decision` (the best verdict among the candidates); each candidate
gains `score` (clamped total, negative reasons included) and its own `decision`.

| Key | Meaning |
|---|---|
| `query_parsed` | what the parser understood — the exact keys blocking and scoring used. First place to look when a lookup returns nothing |
| `hits` / `candidates` | `hits` ranked by `similarity`, `candidates` by `score` |
| `similarity` | 0–100 relevance **to the query as given** — a photo-only query is judged by the photo, text by identifier/name, both by a fixed blend (`concept.md` § Relevance). Not the dedup score |
| `match` | `exact` (same identifier or the same picture file) · `similar` · `none` (a blocking neighbour nothing agreed on — shown so the top neighbour is always visible) |
| `score`, `decision` | check only: the clamped dedup sum and the verdict |
| `reasons` | every level that fired, strongest first, each with `observed` (both sides) — judgeable without re-running |
| `basic` | display data only (`sku`, `name`, `brand`, `ean`, `main_image_url`, `detail_url`), never a full product. One round trip when the provider defines `basics` / `detail_urls`, else one call per hit. A ref the provider no longer serves is dropped silently |
| `warnings` | degradations that did not stop the answer: `image_layer_unavailable`, `kind_unavailable:<kind>` |

## Errors

| Status | Cause |
|---|---|
| 400 | `LookupQuery` validation (`raise_pydantic_as_drf`); malformed body or oversized upload (DRF `ValidationError`); unusable image bytes (`InvalidImage`, surfaced as its message) |
| 401 | no / invalid JWT |
| 403 | authenticated but not staff / superuser |
| 429 | throttle exceeded (`lookup_check` or `lookup_image`) |

Pydantic errors never leave as raw `exc.errors()`; unhandled exceptions reach the host's v2 exception
handler and answer with a `debug_id`.

## OpenAPI

Both views are `@extend_schema`-annotated (tag `Lookup`) and generate without warnings once the host
sets `OAS_VERSION = "3.1.0"` (`install.md` § Prerequisites). Browse at the host's `/api/schema/`.
