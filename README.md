# django-lookup

Product lookup & dedup for the Volkanos platform: "do we already have this?" across PIM and supplier
feeds (atlas) — by identifier, text or image, with explainable `match` / `review` / `no_match` decisions.

Status: module, providers, freshness signals, the `/search/` + `/check/` admin API, the image layer
(pHash + pluggable embedding providers, HNSW blocking), the human-verdict dedup log and the
`lookup_eval` calibration harness have all landed. See `AGENTS.md` and `docs/` for the full
picture: `docs/concept.md` (scoring/decision rules), `docs/api.md` (endpoints), `docs/operations.md`
(commands, providers, SSRF guard, calibration).

## Installation

```shell
pip install entirius-django-lookup
```

Requires PostgreSQL with the `vector` (pgvector ≥ 0.7), `pg_trgm` and `unaccent` extensions available —
migration 0001 creates them. Add `django_lookup` (and `django.contrib.postgres`) to `INSTALLED_APPS`.

Register at least one catalog provider (`docs/operations.md` § Provider registration) — without one,
the module boots but every kind stays unwired.

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `LOOKUP_PROVIDERS` | `{}` | kind → dotted module path implementing the provider protocol |
| `LOOKUP_EMBEDDING` | `{"provider": "none"}` | image embedding backend (`http` / `voyage` / `none` / dotted path: `url`, `model`, `dim`, `api_key`, `timeout_s`) |
| `LOOKUP_IMAGE_ENABLED` | `False` | image layer on/off |
| `LOOKUP_THRESHOLDS` | `{"match": 75, "review": 45}` | decision thresholds on the 0–100 score |
| `LOOKUP_EMBED_ALLOWED_HOSTS` | `[]` | private hosts the embedding client and remote image fetches may reach |
| `LOOKUP_EMBED_CONCURRENCY` | `2` | embedding batches in flight per process — the backend is shared hardware |
| `LOOKUP_HNSW_EF_SEARCH` | `60` | `hnsw.ef_search` for the image blocking query |
| `LOOKUP_BLOCK_PRIVATE_HOSTS` | `True` | SSRF guard master switch; `False` only in a dev harness |

`LOOKUP_EMBEDDING["dim"]` fixes the `halfvec` column dimension at import time — changing it needs a new
migration and a full re-embed. Full settings reference: `docs/operations.md`.

## Development

```shell
make install   # uv sync --all-extras
make test      # pytest against Postgres (DATABASE_URL or LOOKUP_TEST_DB_*; default: localhost:5532 = zeno)
make check     # ruff + canonical .gitleaks.toml
```

Architecture, API contract and agent instructions: [AGENTS.md](AGENTS.md), [docs/](docs/).

## License

MPL-2.0 — see `LICENSE`.
