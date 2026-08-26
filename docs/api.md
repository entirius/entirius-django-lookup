---
title: Admin API
description: POST /search/ and /check/ — request/response shapes, auth, throttling and errors.
---

Two endpoints under `/api/lookup/v2/admin/` (the host service mounts `django_lookup.urls`).
`POST /search/` returns ranked candidates with the evidence behind each one, no verdict.
`POST /check/` runs the same pipeline plus pairwise scoring: every candidate gets a score, a
`match` / `review` / `no_match` decision, and is logged as one `DedupDecision` row. `search` never
writes to the audit log; `check` writes one row per returned candidate (`source="api_check"` unless
the caller passes a different `DecisionSource`, e.g. `create_hook` from the PIM create endpoint).

Views are thin (`api/admin/views/lookup_views.py`): parse the request into `LookupQuery`, call
`services.lookup_service`, serialise the result. No ORM in the view layer; unhandled exceptions reach
the v2 exception handler.

## Auth and throttling

| | |
|---|---|
| Authentication | `JWTAuthentication` |
| Permission | `IsAdminUser` (`is_staff` or `is_superuser` — plain `IsAuthenticated` alone would let storefront customers read the catalogs through the lookup) |
| Throttle (text) | `LookupThrottle`, scope `lookup_check`, fallback `60/min` |
| Throttle (image) | `LookupImageThrottle`, scope `lookup_image`, fallback `30/min` |

A request is routed to the image bucket when it carries a picture — a multipart `image` file, or a
JSON body with `image_url` — because either pays for a guarded outbound fetch plus a synchronous
embedding call; everything else draws on the text bucket. The bucket is decided in `get_throttles()`,
which runs in `APIView.initial()` after `perform_authentication()`/`check_permissions()` — an
unauthenticated or non-admin caller is already 401/403 before it ever touches `request.data`, so
reading the body there to detect `image_url` no longer leaks a parse error ahead of auth. A malformed
body falls back to the text bucket; the view's own parsing then raises the real 400. Both throttles
fall back to their class default when the host has not configured
`DEFAULT_THROTTLE_RATES["lookup_check"]` / `["lookup_image"]`, or when the configured rate is
malformed — a throttle is never silently disabled by a bad config.

## Request

Both endpoints accept the same `LookupQuery` shape as either a JSON body or `multipart/form-data`
(the same field names as form values, plus an `image` file). At least one of `q`, `ean`, `name`,
`mpn`, `sku`, `image_url` or an uploaded `image` file is required — the schema is the only validation
layer, so this and every bound below are enforced before anything reaches SQL.

| Field | Type | Notes |
|---|---|---|
| `q` | string, ≤ 500 chars | Free text; a GTIN found inside it is auto-detected and pulled out |
| `ean` | string, ≤ 32 chars | GTIN-8/12/13/14, any punctuation |
| `brand` | string, ≤ 255 chars | |
| `mpn` | string, ≤ 255 chars | Manufacturer part number |
| `sku` | string, ≤ 255 chars | Catalog reference (PIM sku / atlas ref) |
| `name` | string, ≤ 500 chars | Replaces the text part of `q` when present |
| `attrs` | object \| null | `weight`/`width`/`height`/`deep` (Decimal, kg/cm), `color`, `size` (≤ 64 chars), `pack_qty` (1–9999) |
| `scope` | list of `pim_product` \| `atlas_source_product` | Default: every kind |
| `limit` | int, 1–20 | Default 10 |
| `image_url` | URL \| null | Fetched server-side through the SSRF guard, never stored |
| `has_image` | bool | **Server-set** — true only when the multipart request carried an `image` file; a client-supplied value is ignored |

Explicit fields always win over whatever the query parser extracts from `q` — the caller is assumed
to know more than the parser.

### Multipart upload

`image`: JPEG / PNG / WEBP, ≤ 5 MB. Decoded with Pillow `verify()` then reopened (the documented
Pillow double-open dance), EXIF-oriented, cropped to its non-white bounding box and downscaled —
**never persisted**; the bytes are gone once the response is written. An unusable image is a 400
(`InvalidImage`), not a 500.

## Response

`SearchResponse`:

```json
{
  "query_parsed": {"gtin14": "", "brand_norm": "bosch", "name_norm": "cordless drill 18v", "...": "..."},
  "hits": [
    {
      "kind": "pim_product",
      "ref": "SKU-123",
      "similarity": 60,
      "reasons": [{"code": "gtin_exact", "label": "GTIN 05901234123457 identical", "score": 60,
                   "observed": {"query": "05901234123457", "candidate": "05901234123457"}}],
      "basic": {"sku": "SKU-123", "name": "...", "brand": "Bosch", "ean": "05901234123457",
                "main_image_url": "https://...", "detail_url": "/api/pim/v2/admin/products/SKU-123/"}
    }
  ],
  "warnings": []
}
```

`CheckResponse` adds `decision` (the best verdict among the candidates) at the top level, and each
candidate gets its own `score` (clamped total, may include negative reasons) and `decision`.

- `query_parsed` — what the parser understood: the exact normalised keys blocking and scoring worked
  with. The first thing to check when a lookup returns nothing.
- `hits` / `candidates` — ranked best first. `similarity` is the strongest single piece of positive
  evidence (0 when nothing agrees); `score` (check only) is the clamped sum of every reason.
- `reasons` — every level that fired, strongest first, each with `observed` (both sides of the
  comparison) so a reviewer can judge it without re-running the query.
- `basic` — inline display data only (`sku`, `name`, `brand`, `ean`, `main_image_url`,
  `detail_url`); never a full product payload. One round trip for the whole hit list when the
  provider defines the batch entry points (`basics`/`detail_urls`), else one provider call per hit
  (`basic` + `detail_url`) — either way `limit` caps it at 20. A ref the provider no longer serves (a
  fingerprint the refresh task has not caught up with) is silently dropped from the response.
- `warnings` — degradations that did not stop the answer, e.g. `image_layer_unavailable` when the
  embedding backend is unreachable, or `kind_unavailable:<kind>` when `scope` names a kind with no
  registered provider on this host.

## Errors

| Status | Cause |
|---|---|
| 400 | `LookupQuery` validation failure — missing required signal, bad enum, out-of-range field (`raise_pydantic_as_drf`); or a malformed request body / oversized upload (plain DRF `ValidationError`); or unusable image bytes (`InvalidImage` from `image_prep`, surfaced as its message, not a 500) |
| 401 | No/invalid JWT |
| 403 | Authenticated but not staff/superuser |
| 429 | Throttle exceeded (`lookup_check` or `lookup_image` scope) |

Pydantic validation errors go through `raise_pydantic_as_drf` — never a raw
`Response({"detail": ...})` and never `exc.errors()` verbatim. Unhandled exceptions reach the v2
exception handler, which logs and answers with a `debug_id` instead of an exception message.

## OpenAPI

Both views are `@extend_schema`-annotated (tag `Lookup`) with a `summary` and worked `description`,
so the schema generates without warnings. **The host service must set
`SPECTACULAR_SETTINGS["OAS_VERSION"] = "3.1.0"`** — Pydantic documents examples the JSON Schema
2020-12 way, which `spectacular --validate` rejects under the default OpenAPI 3.0.3. Browse the
generated schema at the host's `/api/docs/` or `/api/schema/`.
