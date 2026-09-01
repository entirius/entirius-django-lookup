# AGENTS.md

entirius-django-lookup — product lookup & dedup for Volkanos: "does something like this already exist?"
across PIM and atlas, by identifier, text or image. App label `django_lookup`, table prefix `django_lookup_`.

## Quick Reference

- Python ≥ 3.12, Django ≥ 5, PostgreSQL + pgvector / pg_trgm / unaccent, `uv`, ruff, hatchling, MPL-2.0.
- Read first: `docs/install.md` (host) · `docs/api.md` (caller) · `docs/concept.md` (why) ·
  `docs/gotchas.md` (before editing). This file is the map; it explains nothing twice.

## Commands

| Command | Meaning |
|---|---|
| `make install` | `uv sync --all-extras` |
| `make test` | pytest — Postgres only, see Testing |
| `make check` / `fix` | ruff lint + format (+ canonical `.gitleaks.toml` guard) |
| in zeno: `make module-test MODULE=entirius-django-lookup` | the same suite inside the service container |

## Conventions

- English only; MPL-2.0 header on every `.py` (`insert-license`); no Claude attribution trailers.
- Layered: `models/` · `normalize/` (pure) · `providers/` · `services/` · `schemas/` · `api/admin/`.
  No logic in models; normalizers never touch the DB or settings.
- Never rename the package, the app label or the table prefix.
- Git flow: `develop` + `master`, PRs. Do not commit by default — the operator decides.
- **A number enters this file or the harness AGENTS.md only after being measured on a fresh seed** —
  never derived, never carried forward (`docs/operations.md` § Re-measuring).

## Map

```
src/django_lookup/
├── apps.py  enums.py  settings.py (get_* accessors)  constants.py (EMBEDDING_DIM: frozen at import)
├── models/        fingerprint.py  dedup_decision.py
├── schemas/       requests/lookup.py (LookupQuery, Attrs)  responses/lookup.py
├── api/admin/     views/lookup_views.py  urls.py  permissions.py (IsAdminUser)  throttling.py
├── urls.py        api/lookup/v2/admin/ → api.admin.urls
├── migrations/    0001: VectorExtension, TrigramExtension, UnaccentExtension, models, GIN + HNSW
├── providers/     base.py (ProviderItem, BasicData — the contract)  registry.py (lazy, cached)
├── normalize/     text.py  gtin.py  brand.py  mpn.py  name.py      dictionaries/ (json, pl/en/de)
├── services/      fingerprint_service (build/upsert/refresh)  backfill_service  query_parser
│                  blocking (candidates)  scoring (score_pair, decide)  lookup_service (search, check)
│                  dedup_log (human verdicts)  image_prep (pure)  image_service (fetch/embed/write)
│                  eval_service (calibration)
├── embedding/     base.py  hosted.py (batching, validation)  http_provider  voyage_provider
│                  null_provider  transport.py (SSRF + backoff)  factory.py (settings → provider)
├── security/      url_guard.py (assert_safe_url, safe_get)
├── signals.py     provider-declared senders → refresh_fingerprint      tasks.py (Celery, queue `lookup`)
└── management/commands/  lookup_backfill  lookup_reconcile  lookup_doctor  lookup_eval
```

Flow: provider `iter_items()` → `build_fingerprint` → `Fingerprint` → `blocking.candidates`
(exact keys ∪ pHash ∪ trigram ∪ HNSW, ≤ 100) → `scoring.score_pair` (L0–L8, every level a `Reason`)
→ `scoring.decide` → `/search/` (hits) or `/check/` (+ score, decision, one `DedupDecision` per candidate).

## Where things live

| Question | Answer |
|---|---|
| A setting's name, default, meaning | `settings.py` accessors; the table in `docs/install.md` |
| A batch size, cap, tolerance, weight | `constants.py`, `scoring.py`; quoted once in `docs/concept.md` |
| The provider protocol | `providers/base.py` — field names are the contract; PIM and atlas mirror it, never import it |
| Request / response shape, auth, errors | `docs/api.md`; `schemas/` |
| Why a weight, flag, threshold or cap is what it is | `docs/concept.md` |
| What `/search/` `similarity` means (relevance, not the dedup score) | `scoring.relevance`; `docs/concept.md` § Relevance |
| How the image pipeline works | the docstrings: `services/image_prep.py`, `services/image_service.py`, `tasks.py` |
| Is the image layer healthy | `manage.py lookup_doctor` — the exit code is the answer |
| Calibration numbers and how the legs are isolated | `manage.py lookup_eval`; `services/eval_service.py` docstring |
| Running, replacing or extending the embedding backend | `docs/embedding.md`; `embedding/base.py` is the contract |
| Freshness, tasks, degradation, tuning | `docs/operations.md` |
| Which test file covers what | `docs/testing.md` |
| What changed and why | `CHANGELOG.md` |

## Testing

- Postgres only (`tests/settings.py`): `DATABASE_URL` wins (zeno container), else
  `LOOKUP_TEST_DB_HOST/PORT/NAME/USER/PASSWORD` (defaults = zeno host port 5532, `entirius`/`entirius-dev`).
  The role needs CREATE DATABASE + CREATE EXTENSION. Migrations run in tests.
- Images are generated (`tests/images.py`), never committed. `tests/fake_embedding.py` backs
  `LOOKUP_EMBEDDING["provider"]`; `tests/vectors.py` builds vectors at an exact cosine;
  `tests/fake_provider.py` is the catalog.
- `tests/test_docs_example.py` asserts on `docs/settings_example.py` — the documented config is under test.

## Gotchas

`docs/gotchas.md` — the only list. Read it before touching blocking, scoring, the image layer or the API.
