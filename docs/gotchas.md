# Gotchas

- **`SPECTACULAR_SETTINGS["OAS_VERSION"] = "3.1.0"` is required in the host service** — Pydantic documents
  examples the JSON Schema 2020-12 way, which OpenAPI 3.0.3 rejects; `spectacular --validate` fails otherwise.
- **Display data N+1 — measured, not assumed:** 20 hits through the singular
  `provider.basic(ref)` + `provider.detail_url(ref)` pair cost **246 SQL queries** against the seeded PIM
  provider (`django_pim.services.lookup_provider`), ~76 ms wall time in-cluster (2026-08-24, zeno
  `service` container, 5 samples). `lookup_service._display` now uses the provider's optional
  `basics(refs)`/`detail_urls(refs)` batch entry points when both are defined (one round trip for the
  whole hit list) and falls back to the singular pair otherwise — see `providers/base.py`. Only
  `tests/fake_provider.py` implements the batch pair so far; **TODO (other repos): add
  `basics`/`detail_urls` to `django_pim.services.lookup_provider` and
  `django_atlas.services.lookup_provider`** to actually collapse the 246 queries in production.
- **`check` writes one `DedupDecision` per returned candidate** (`source="api_check"`) unless the caller
  passes `log=False`; `search` writes none.
- **`EMBEDDING_DIM` is read once at import** from `LOOKUP_EMBEDDING["dim"]` (default 1152 = SigLIP so400m). A
  different value makes `makemigrations --check` fail on purpose: new migration + full re-embed.
- **Dictionary keys must be stored folded** (lowercase, ASCII, no punctuation) — lookups happen after `fold()`;
  an accented key is unreachable. `test_dictionaries` enforces it.
- **Settings are read through `django_lookup.settings.get_*()`** at call time; never snapshot them at import
  (only `EMBEDDING_DIM` is).
- **Initial image backfill: drop `lookup_fp_image_vec_hnsw_idx`, bulk-load, recreate** — incremental HNSW
  inserts over ~1M rows are far slower than one build.
- `Fingerprint.deep` mirrors `django_pim.RealProduct.deep` on purpose (not `depth`).
- **`django.contrib.postgres` must be installed** in the host (ArrayField, GinIndex); volkanos has it.
- **Stopwords include `a`, `z`, `i`, `w`** (pl/en) — single-letter tokens vanish from `name_norm` by design.
- **Normalizers return values, never raise** — an invalid GTIN is `GtinResult(gtin14="", valid=False, ...)`.
- **The host worker must consume the `lookup` queue** (`constants.CELERY_QUEUE`) — otherwise signals pile up
  unprocessed and only `lookup_reconcile` keeps the table honest.
- **An upsert never touches the image columns** (`phash`/`image_vec`/`vec_model`/`image_sha1`): the
  image layer owns them, a provider refresh would blank them.
- **`bit_count` takes bit/bytea, not bigint** — the pHash leg casts (`bit_count((phash # v)::bit(64))`), and
  the query hash is `Cast(Value(...), BigIntegerField())` because psycopg adapts a Python int as `numeric`.
- **`hnsw.ef_search` is applied with `SELECT set_config(..., true)` inside `transaction.atomic()`** — `SET LOCAL`
  cannot take a bound parameter, and without a transaction it silently does nothing.
- **A candidate whose only positive evidence is its picture is flagged `image_only`** and can never reach
  `match`, whatever the thresholds say. Two black t-shirts photograph alike.
- **`image_url` and uploaded bytes never reach the client's own network**: `security/url_guard.py` blocks
  private ranges by default, re-validates every redirect hop and caps the body at 10 MB.
  `image_service.fetch_remote(url, allowed_hosts=...)` carries no allowlist opinion of its own — its two
  callers have different trust and must not share one policy. `image_service._load` (the catalog/worker
  path, provider-owned URLs from `embed_refs`) passes `settings.embed_allowed_hosts()`, the same hole
  `embedding/transport.py` punches for the operator-configured embedding host. `lookup_service._query_image`
  (the API `image_url` field — REQUEST-supplied, an admin-JWT caller's own input) passes none: an admin
  token must never be able to aim a fetch at an allowlisted internal host as a blind SSRF oracle.
  `LOOKUP_BLOCK_PRIVATE_HOSTS = False` is a dev-harness switch only.
- Runtime deps absent from the Volkanos registry lock (`pgvector`, `imagehash`, `rapidfuzz`, `python-stdnum`)
  must be declared by the host service — zeno dev mode links modules with `--no-deps`.
