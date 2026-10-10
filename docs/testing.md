---
title: Test suite map
description: Which test file covers what.
---

- Suites: normalizer tables (`test_gtin`, `test_brand`, `test_mpn`, `test_name`), dictionary invariants
  (`test_dictionaries`: keys already folded, views immutable), registry with `tests/fake_provider.py`,
  `build_fingerprint`, model/index smoke, query parsing (`test_query_parser`), golden pairs + properties
  (`test_scoring`), relevance / match kind per query modality (`test_relevance`), blocking recall (`test_blocking`: 1000 rows / 20 probes / recall@50, the word-similarity leg, the per-candidate brand strip), `search` and `check`
  (`test_lookup_service`), the API contract incl. 400/401/403/429 (`test_lookup_api`) and the generated
  OpenAPI document (`test_openapi`). Image layer: `test_image_prep` (crop/EXIF/hash stability/format gate),
  `test_embedding` (factory, batching, dimension mismatch, backoff), `test_url_guard`, `test_image_service`
  (skip/retry/degrade), `test_image_blocking` (both SQL legs), `test_image_scoring` (L8 tiers, `image_only`),
  `test_image_api` (multipart, validation, degrade, throttle) and `test_lookup_doctor`. Calibration:
`test_eval_service.py` (CSV parsing, threshold sweep, confusion, both recall diagnostics) and
`test_lookup_eval_command.py` (the command never raises, `--image-only` output shape).

## Database and fixtures

- Postgres only (`tests/settings.py`): `DATABASE_URL` wins, else
  `LOOKUP_TEST_DB_HOST/PORT/NAME/USER/PASSWORD` (the defaults point at the database of the entirius-zeno harness
  on its host port). The role needs CREATE DATABASE + CREATE EXTENSION. Migrations run in tests.
- Images are generated (`tests/images.py`), never committed. `tests/fake_embedding.py` backs
  `LOOKUP_EMBEDDING["provider"]`; `tests/vectors.py` builds vectors at an exact cosine;
  `tests/fake_provider.py` is the catalog.
- `tests/test_docs_example.py` asserts on `docs/settings_example.py` — the documented config is under test.

## In the entirius-zeno harness

In the entirius-zeno harness: `make module-test MODULE=entirius-django-lookup` runs the same suite inside the
service container, where `DATABASE_URL` is already set.
