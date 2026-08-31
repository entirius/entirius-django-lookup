---
title: Gotchas
description: The one list of rules that bite — read before touching blocking, scoring, the image layer or the API.
---

Install-time traps (extensions, `lookup` queue, OAS 3.1, frozen `dim`, `/embeddings_image`) live in
`install.md`, not here. Each item below: the rule, then where it is enforced.

## Image layer

- **Query and catalog images take the same `image_prep.load_and_crop` path.** A different pre-crop is a
  different feature space; catalog shots sit on white, which dominates the DCT until cropped away.
- **`image_prep.encode` downscales a copy.** `Image.thumbnail` resizes in place and the caller hashes the
  object it passed in — shrinking the argument would silently move the caller's feature space.
- **`phash` is a signed 64-bit reinterpretation of imagehash's value** (`image_prep._signed`). Compare with
  `image_prep.hamming`, never a raw XOR.
- **`bit_count` takes bit/bytea, not bigint** — the pHash leg casts `(phash # v)::bit(64)` and passes the
  query hash as `Cast(Value(...), BigIntegerField())`; psycopg adapts a Python int as `numeric`.
- **`hnsw.ef_search` is set with `SELECT set_config(..., true)` inside `transaction.atomic()`** — `SET LOCAL`
  takes no bound parameter, and outside a transaction it silently does nothing.
- **HNSW is approximate, yet the image leg's recall is reproducible** — three fresh seeds return the
  same `recall@20`. The run-to-run spread once blamed on it (0.28–0.34) was the text route
  (`/embeddings`) collapsing every photo onto one vector, so ties fell to traversal order. A moving
  image number means the embedding route (`lookup_doctor`'s discrimination check), not HNSW noise.
- **An upsert never touches `phash` / `image_vec` / `vec_model` / `image_sha1`.** The image layer owns
  them (`fingerprint_service._UPSERT_FIELDS`); a text-only refresh must not blank a hashed picture.
- **A row embedded by another model is filtered out of the HNSW leg, never deleted** (`vec_model`
  mismatch) — until `lookup_backfill --images` re-embeds it. `lookup_doctor` counts these rows.
- **`image_only` never reaches `match`**, whatever the thresholds: two black t-shirts photograph alike.
  A picture is evidence, not proof (`scoring.CAPPING_FLAGS`).

## Scoring and decisions

- **`identifier_exact` bypasses the score gate** — a bare EAN query scores 60 and still decides `match`;
  a capping flag (`brand_conflict`, `variant_conflict`, `image_only`) pins it to `review` regardless.
- **Normalizers return values, never raise** — an invalid GTIN is `GtinResult(gtin14="", valid=False)`.
- **Dictionary keys are stored folded** (lowercase, ASCII, no punctuation): lookups happen after `fold()`,
  an accented key is unreachable. `test_dictionaries` enforces it.
- **Stopwords include `a`, `z`, `i`, `w`** — single-letter pl/en tokens vanish from `name_norm` by design.
- **`Fingerprint.deep` mirrors `django_pim.RealProduct.deep`** on purpose — not `depth`.

## API

- **`has_image` is server-set, never trusted from the client.** Uploaded bytes are Pillow `verify()`-ed,
  reopened, and **never persisted** — gone once the response is written.
- **`get_throttles()` runs after authentication and permissions**, so 401/403 come before `request.data` is
  touched; a malformed body falls back to the text bucket and the view's own parsing raises the real 400.
- **`check` writes one `DedupDecision` per returned candidate** (`source="api_check"`) unless `log=False`;
  `search` writes none.
- **`similarity` is relevance to the *query*, not the dedup score.** A photo-only query reports 100 for
  the same picture file while `/check/` on that photo still refuses `match` — two questions, two scales
  (`concept.md` § Relevance). Conflicts never lower it; `decision` carries them.
- **Display data is an N+1 unless the provider defines `basics(refs)` / `detail_urls(refs)`.** Measured:
  20 hits × singular `basic()` + `detail_url()` = 246 queries against the seeded PIM provider (zeno,
  2026-08-24). `lookup_service._display` uses the batch pair when both exist. **TODO (other repos):** add
  them to `django_pim` and `django_atlas` `services/lookup_provider` — only `tests/fake_provider.py` has them.

## Outbound fetches

- **`image_service.fetch_remote` carries no allowlist opinion; its two callers carry different trust.**
  The worker's catalog fetch passes `LOOKUP_EMBED_ALLOWED_HOSTS`; the API's `image_url` passes none — an
  admin token must never aim a fetch at an allowlisted internal host. Table: `operations.md` § Outbound fetches.

## Module boundaries and runtime

- **Settings are read through `django_lookup.settings.get_*()` at call time** — never snapshot one at
  import. `EMBEDDING_DIM` is the single, deliberate exception.
- **Catalog modules mirror `ProviderItem` / `BasicData`, they never import `django_lookup`** — field names
  are the contract (`providers/base.py`); an optional consumer must not become a catalog's dependency.
- **Bulk writers call `tasks.refresh_fingerprints(kind, refs)`**, not `refresh_fingerprint.delay()` per row —
  `bulk_create` / `bulk_update` fire no `post_save`, and one publish per `REFRESH_TASK_BATCH` (200) refs is
  the point.
- **The worker has no autoreload** — restart it after any change to `tasks.py` or what it calls.
- **Postgres only.** pgvector / pg_trgm / unaccent have no sqlite equivalent; the suite has no sqlite path.
