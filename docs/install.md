---
title: Install
description: What a host needs before the first lookup — prerequisites, the settings table, choosing an embedding backend, bootstrap order, sizing.
---

Read this once, top to bottom, before `migrate`. Day-2 commands and tuning: `operations.md`.

## Prerequisites

| Requirement | Why | Verify |
|---|---|---|
| PostgreSQL with `vector` ≥ 0.7, `pg_trgm`, `unaccent` **available** | migration 0001 runs `CREATE EXTENSION` — the server needs the packages, the migrating role the privilege (`pgvector/pgvector:pg16` ships all three, superuser) | `SELECT name FROM pg_available_extensions WHERE name IN ('vector','pg_trgm','unaccent')` → 3 rows |
| `django.contrib.postgres` and `django_lookup` in `INSTALLED_APPS` | `ArrayField`, `GinIndex` | `manage.py check` |
| Runtime deps in the host lock: `pgvector`, `imagehash`, `rapidfuzz`, `python-stdnum`, `Pillow` | the wheel declares them; a host that links the module `--no-deps` (zeno dev mode) does not get them | `uv lock` / `pip check` |
| A Celery worker consuming queue **`lookup`** | every task in `tasks.py`; without it freshness signals pile up and only `lookup_reconcile` keeps the table honest | `celery -A main worker -Q …,lookup` |
| `SPECTACULAR_SETTINGS["OAS_VERSION"] = "3.1.0"` | Pydantic documents examples as JSON Schema 2020-12, which OpenAPI 3.0.3 rejects | `manage.py spectacular --validate` |
| ≥ 1 catalog provider in `LOOKUP_PROVIDERS` | without one the module boots and every kind stays unwired — silently | `lookup_backfill` prints a per-kind count |
| *Optional:* an image-embedding endpoint | only the vector leg needs it; pHash needs nothing | `manage.py lookup_doctor` |

## Settings

The only settings table. Accessors and defaults: `django_lookup/settings.py`; every value is read at
call time, so `override_settings` and the host always win — except `dim`, see below.

| Setting | Default | Meaning |
|---|---|---|
| `LOOKUP_PROVIDERS` | `{}` | kind → dotted module implementing `providers/base.py` |
| `LOOKUP_EMBEDDING` | `{"provider": "none"}` | `{provider, url, model, dim, api_key, timeout_s}` — see *Embedding backend* |
| `LOOKUP_IMAGE_ENABLED` | `False` | image layer on/off; `False` keeps pHash, drops vectors |
| `LOOKUP_EMBED_ALLOWED_HOSTS` | `[]` | private hosts the embedding client and the worker's catalog-image fetch may reach — never the request's `image_url` (`operations.md` § Outbound fetches) |
| `LOOKUP_EMBED_CONCURRENCY` | `2` | embedding batches in flight per process — the GPU is shared |
| `LOOKUP_THRESHOLDS` | `{"match": 75, "review": 45}` | decision thresholds on the 0–100 score |
| `LOOKUP_HNSW_EF_SEARCH` | `60` | `hnsw.ef_search` for the image blocking leg |
| `LOOKUP_IMAGE_TOP_K` | `20` | neighbours each image leg hands to scoring |
| `LOOKUP_PHASH_MAX_DISTANCE` | `10` | width of the free pHash gate, in bits |
| `LOOKUP_BLOCK_PRIVATE_HOSTS` | `True` | SSRF guard master switch — `False` only in a dev harness |

The last three are the tuning knobs; profiles in `operations.md` § Deepening the image search.

**`LOOKUP_EMBEDDING["dim"]` is frozen into the schema.** Migration 0001 creates
`Fingerprint.image_vec` as `halfvec(dim)` (`constants.EMBEDDING_DIM`, read once at import). A different
value later means a new migration and a full re-embed — `makemigrations --check` fails on purpose and
`lookup_doctor` reports it. **Decide the model before the first big backfill.**

## Embedding backend

`LOOKUP_EMBEDDING["provider"]`:

| Provider | When | Photos leave the network |
|---|---|---|
| `http` | you can run a container or reach a GPU box — **the default choice** | no |
| `voyage` | no GPU anywhere; a one-week quality trial | **yes** — a deployment decision |
| `none` | text / GTIN / pHash only — no warnings, nothing to operate | — |
| dotted path | a bespoke `EmbeddingProvider` subclass | your call |

This table is the whole install-time decision. Everything below it — running the reference backend
(Infinity, CPU/GPU), the wire contract any alternative endpoint must satisfy, bringing your own
provider class, and changing the model later — is **`docs/embedding.md`**.

The settings block operators copy is **`docs/settings_example.py`** — it is a real file, imported
and asserted on by `tests/test_docs_example.py`, so it cannot drift.

## Bootstrap order

```
manage.py migrate                    # extensions, tables, GIN + HNSW indexes
manage.py lookup_backfill            # text fingerprints from every provider
manage.py lookup_backfill --images   # pHash always; vectors via Celery when the image layer is on
manage.py lookup_doctor              # exit 0, or do not ship
manage.py lookup_reconcile           # then on a schedule — idempotent, repairs lost signals
```

Large catalog (≳ 10⁵ pictures): drop `lookup_fp_image_vec_hnsw_idx` before `--images`, recreate it
after with `maintenance_work_mem ≥ 2GB` — incremental HNSW inserts are far slower than one build,
and the build is 10–50× slower without that memory.

## Sizing

Per 1M fingerprints (~500k PIM + ~500k atlas); measured or estimated in the design notes.

| Element | Cost | When |
|---|---|---|
| Key normalisation | ~0.1 ms/row → minutes | backfill once, then per save |
| pHash | ~5 ms/image → ~1.5 h | once, then only changed pictures (`image_sha1`) |
| Embedding, CPU | 30–100 ms/image → 8–25 h; +1–2 GB RAM per process | once, then only changed pictures |
| Embedding, one consumer GPU (RTX 5080) | 1M images ≈ 20–40 min | same |
| `pg_trgm` GIN | minutes; ~300 MB disk | once |
| HNSW `halfvec(1152)` | ~0.8 GB data + ~0.8 GB index — keep in `shared_buffers` | once |
| `/check` without image | ms | online |
| `/check` with image | 50–150 ms embed + ms search | online |
| Hosted embedding (`voyage`) | ≈ $15 per 1M images | per (re-)embed |

Without the vector leg the whole module is indexes plus minutes of CPU.
