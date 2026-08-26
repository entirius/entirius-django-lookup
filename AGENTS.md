# AGENTS.md

entirius-django-lookup — product lookup & dedup module for Volkanos (Django). App label `django_lookup`.

## Quick Reference

- Tech: Python ≥ 3.12, Django ≥ 5, PostgreSQL + pgvector / pg_trgm / unaccent, `uv`, ruff, hatchling, MPL-2.0.

## Commands

| Command | Meaning |
|---|---|
| `make install` | `uv sync --all-extras` |
| `make test` | pytest (Postgres only — see Testing) |
| `make check` / `fix` | ruff lint + format (+ canonical `.gitleaks.toml` guard) |
| in zeno: `make module-test MODULE=entirius-django-lookup` | same suite inside the service container |

## Conventions

- English only; MPL-2.0 header on every `.py` (`insert-license`); no Claude attribution trailers.
- Layered layout: `models/` · `normalize/` (pure) · `providers/` · `services/` · `schemas/` ·
  `api/admin/`. No logic in models; normalizers never touch the DB or settings. Never rename the
  package, app label or the `django_lookup_` table prefix.
- Git flow: `develop` + `master`, PRs. Do not commit by default — the operator decides.

## Architecture

```
src/django_lookup/
├── apps.py  enums.py  settings.py  constants.py      # constants: EMBEDDING_DIM resolved once at import
├── models/        fingerprint.py  dedup_decision.py
├── schemas/       requests/lookup.py (LookupQuery, Attrs)  responses/lookup.py (hits, reasons, query_parsed)
├── api/admin/     views/lookup_views.py  urls.py  permissions.py (IsAdminUser)  throttling.py
├── urls.py        api/lookup/v2/admin/ -> api.admin.urls (host service includes it when installed)
├── migrations/    0001_initial.py                     # VectorExtension, TrigramExtension, UnaccentExtension, then models
├── providers/     base.py (ProviderItem, BasicData, protocol)  registry.py (dotted path, cache, clear_cache)
├── normalize/     text.py  gtin.py  brand.py  mpn.py  name.py
├── dictionaries/  brand_aliases / legal_forms / stopwords / colors / units (.json, pl/en/de; lru_cache loaders)
├── services/      fingerprint_service.py (build / upsert / refresh)  backfill_service.py (backfill, reconcile)
│                 query_parser.py (LookupQuery -> ParsedQuery)  blocking.py (candidates)
│                 scoring.py (score_pair, decide)  lookup_service.py (search, check)
│                 dedup_log.py (human verdicts on a pair: record / rejected_pairs)
│                 image_prep.py (pure: crop/hash/encode)  image_service.py (fetch, embed, write)
├── embedding/     base.py (contract)  hosted.py (batching/validation)  http_provider.py  voyage_provider.py
│                 null_provider.py  transport.py (SSRF + backoff)  factory.py (settings -> provider)
├── security/      url_guard.py (assert_safe_url, safe_get — outbound fetches)
├── signals.py     provider-declared senders -> refresh_fingerprint    tasks.py (Celery, queue `lookup`)
└── management/commands/  lookup_backfill.py  lookup_reconcile.py  lookup_doctor.py  lookup_eval.py
```

Flow (later plans): provider `iter_items()` → `build_fingerprint` → `Fingerprint` rows → blocking (GTIN / brand+MPN
B-tree, name trigram GIN, image HNSW) → scoring → `DedupDecision` log.

## Data Model

- `Fingerprint(kind, ref)` unique — normalised columns only: `gtin14` (+`gtin_trusted`), `brand_norm`, `mpn_norm`,
  `name_norm` (GIN trigram), `name_tokens` (sorted, for Jaccard), `pack_qty`, `color`, `size`, dimensions
  (Decimal), `phash`, `image_vec` (`halfvec(EMBEDDING_DIM)`, HNSW cosine), `vec_model`, `image_sha1`,
  `source_updated_at`; `created_at`/`modified_at` from `django_utils.BaseModel`.
- `DedupDecision` — append-only log: `query`, `subject_ref`, candidate `(kind, ref)`, `score`, `features`
  (reasons list), `decision_auto`, `decision_human`, `user`, `source`. `subject_ref` is the catalog row the
  query stood for (empty for a free-text search); with `candidate_ref` it is the *pair* a human verdict
  answers — `services/dedup_log.py` writes those verdicts (`record`) and reads them back
  (`rejected_pairs`, so a proposing caller never re-proposes a rejected pair once the bus's own
  cooldown expires).

## Provider protocol

`settings.LOOKUP_PROVIDERS = {"pim_product": "django_pim.services.lookup_provider", ...}`. A provider is a
module exposing `iter_items(since=None)`, `get_item(ref)` (raises `LookupError`), `basic(ref)`, `detail_url(ref)`
and optionally `signal_specs()` — see `providers/base.py`. `registry.get_provider(kind)` imports lazily and
caches by dotted path; `clear_cache()` is the test hook. PIM and atlas never import each other — nor this
module: they mirror `ProviderItem` / `BasicData` (field names are the contract, `providers/base.py` is
authoritative) so an optional consumer never becomes a dependency of a catalog.

## API

Full request/response reference: `docs/api.md`. Scoring weights, tolerances and decision
pseudocode: `docs/concept.md`.

- `POST /api/lookup/v2/admin/search/` — ranked hits, **no verdict**: `lookup_service.Hit` carries no
  `score`/`decision` field at all, so the boundary is enforced on the dataclass, not merely hidden by
  the response schema.
- `POST /api/lookup/v2/admin/check/` — the same pipeline plus `Candidate` (`Hit` + `score` +
  `decision`) and one `DedupDecision` row per candidate (unless the caller passes `log=False`).

JSON or multipart (`image` file plus the same fields as form values), `IsAdminUser` + JWT.
Throttling splits by cost: `LookupThrottle` (scope `lookup_check`, fallback `60/min`) for text, and
`LookupImageThrottle` (scope `lookup_image`, fallback `30/min`) whenever the request carries a
picture — a multipart upload or a JSON `image_url` — because both pay for a guarded outbound fetch
plus a synchronous embedding call.

Request `LookupQuery`: `q` (a GTIN inside it is detected), `ean`, `brand`, `mpn`, `sku`, `name`,
`attrs`, `scope`, `limit` (≤ 20), `image_url`, `has_image` (server-set). At least one of
`q / ean / name / mpn / sku / image_url` or an uploaded `image`. Uploads: jpeg/png/webp, ≤ 5 MB.

Pipeline: `query_parser.parse` (+ `image_service.prepare_query` for a picture) →
`blocking.candidates` (exact keys ∪ pHash ≤ 10 ∪ trigram top-50 ∪ HNSW top-20, ≤ 100 rows, each
annotated with `name_similarity` and `image_distance`) → `scoring.score_pair` (L0-L8) →
`scoring.decide`.

Weights live in `scoring.WEIGHTS`, thresholds in `settings.get_thresholds()` (`match` 75 /
`review` 45). Image-search depth is three settings — `LOOKUP_HNSW_EF_SEARCH`,
`LOOKUP_IMAGE_TOP_K`, `LOOKUP_PHASH_MAX_DISTANCE` — with ready profiles in `docs/operations.md`;
the defaults are what the published baseline was measured with.

## Image layer

- **Pre-crop is the whole trick:** catalog shots sit on white, which dominates the DCT.
  `image_prep.load_and_crop` verifies, applies EXIF orientation, crops to the non-white bounding box
  and downscales to 1024 px. **Query and catalog images MUST take the same path.**
- `phash` is an `imagehash` 64-bit value reinterpreted as a **signed** bigint (`_signed`) — compare with `image_prep.hamming`, never a raw XOR.
- Providers implement `embed_images(list[bytes]) -> list[EmbeddingResult]` and `info() -> ProviderInfo`;
  `LOOKUP_EMBEDDING["provider"]` is `http` / `voyage` / `none` / a dotted path (`docs/operations.md`).
  **No model is ever loaded in a Django process.**
- `embed_fingerprint_images(kind, refs)` (queue `lookup`) does fetch → prep → hash → embed →
  `bulk_update`, skipping rows whose `image_sha1` is unchanged *and* `vec_model` is current.
  `lookup_backfill --images` enqueues everything in batches of 32.
- **Degrade, never fail:** a dead backend leaves `image_vec` NULL (retried next run) and `/search/`
  answers with `warnings: ["image_layer_unavailable"]`; the pHash leg keeps working regardless.
- `manage.py lookup_doctor` is the fail-closed handshake: settings, column dimension, HNSW index, a
  live embed from **both** the web process and the worker, plus coverage counts. `LookupConfig.ready()`
  logs the same dimension mismatch as a warning and never blocks the boot.

## Calibration

`manage.py lookup_eval --pairs <csv> [--thresholds 45,75] [--image-only] [--log-decisions]`
(`services/eval_service.py`) replays the production `check()` over a hand-labelled CSV and reports
threshold sweeps, a confusion matrix and two single-leg blocking-recall diagnostics. Exit code is
always 0 — the numbers are a measurement, not a gate, and the fixture is adversarial by design.

Rows run with `log=False` unless `--log-decisions` is passed. **A number only belongs in an
AGENTS.md after being measured on a fresh `make seed`** — never derived. Full reference and the
re-measurement procedure: `docs/operations.md` (§Re-measuring the calibration numbers).

## Freshness

Full command flags and provider registration: `docs/operations.md`.

`signals.connect()` (from `LookupConfig.ready()`) walks every provider's `signal_specs()` — plain
dicts `{"model": "<app>.<Model>", "signal": "post_save" | "post_delete", "ref": callable}` — and
connects a handler per sender. The handler enqueues `refresh_fingerprint(kind, ref)` on the `lookup`
queue after commit; the task rebuilds the row, or deletes it when the provider no longer serves that
ref (how a linked `SourceProduct` leaves the candidate pool). `lookup_backfill` fills everything;
`lookup_reconcile` repairs drift and is idempotent.

`tasks.refresh_fingerprints(kind, refs)` is the batched twin for callers that persist many rows
outside the signal path (`bulk_create`/`bulk_update` fire no `post_save`): one Celery publish per
`constants.REFRESH_TASK_BATCH` (200) refs, and one image-task publish per batch rather than per ref.
**Any bulk writer should reach for this instead of looping `refresh_fingerprint.delay()` per row** —
atlas's full sync is the first consumer.

## Normalisation rules (binding)

- GTIN: digits only; length ∈ {8,12,13,14}; GS1 check digit (`python-stdnum`); key = 14-digit zero-padded.
  Indicator 1–8 → `related_unit` (multipack, never equal). Prefixes 2 / 20–29 / 02 / 04 → `trusted=False`.
- MPN: uppercase, drop ` -/.`; strip leading zeros only if ≥ 4 chars remain; `loose` = before the last `-`.
- Brand: fold, drop legal forms, alias table (`hewlett packard` → `hp`).
- Name: fold + unaccent, units (`1,5 l` → `1.5l`), stopwords pl/en/de, brand strip (given or leading tokens in
  the dictionary), pack (`2x`, `3-pack`, `zestaw 2`, `4 szt`), colour dictionary → english, size (`xl`, `eu 42`).

## Testing

- Postgres only (`tests/settings.py`): `DATABASE_URL` wins (zeno container), else `LOOKUP_TEST_DB_HOST/PORT/NAME/
  USER/PASSWORD` (defaults = zeno host port 5532, `entirius`/`entirius-dev`). The role needs CREATE DATABASE +
  CREATE EXTENSION (superuser in the pgvector image). Migrations run in tests (extensions come from 0001).
- Per-suite map (what each `test_*.py` covers): `docs/testing.md`.
- Images are **generated** (`tests/images.py`), not committed — the hash-stability tests rotate, resize
  and recompress the same picture. `tests/fake_embedding.py` backs `LOOKUP_EMBEDDING["provider"]`;
  `tests/vectors.py` builds vectors at an exact cosine.

## Gotchas

Full list: `docs/gotchas.md`. The ones that bite hardest:

- **`has_image` is server-set, never trusted from the client** — the schema is the only validation
  layer, and uploaded images are decoded with Pillow `verify()` + reopen and **never persisted**.
- **`get_throttles()` runs after authentication/permissions**, so an unauthenticated or non-admin
  caller is 401/403 before `request.data` is ever touched; a malformed JSON body falls back to the
  text bucket so the view's own parsing raises the real 400.
- **`identifier_exact` bypasses the score gate** — a bare EAN query scores 60 but still decides
  `match`; a capping flag pins it to `review` instead, whatever the score.
- **The image leg is approximate (HNSW, `ef_search`)** — its recall varies run to run; the text
  metrics do not. Re-measure image numbers over several seeds before recording them.
- **Initial image backfill: drop `lookup_fp_image_vec_hnsw_idx`, bulk-load, then recreate** —
  incremental HNSW insertion is far slower than a rebuild.
- **Postgres only** — pgvector / pg_trgm / unaccent have no sqlite equivalent, so there is no
  sqlite fallback in the suite.

## Reference Docs

| File | Content |
|------|---------|
| `docs/concept.md` | Lookup vs dedup, blocking legs, scoring weights, decision rules |
| `docs/api.md` | Endpoints, request/response, auth, errors |
| `docs/operations.md` | Commands, providers, embedding, SSRF guard, degradation, calibration |
| `docs/gotchas.md` | Full gotcha list |
| `docs/testing.md` | Per-test-file scope map |
| `docs/erd-config.yaml` | ERD diagram config |
