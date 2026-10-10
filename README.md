# entirius-django-lookup

A Django module for product lookup and deduplication in Entirius, the open-source modular ecommerce platform.
It answers "do we already have this?" across the catalog and supplier feeds, by identifier, text or image, and
explains every `match`, `review` or `no_match` verdict with its reasons.

[![PyPI](https://img.shields.io/pypi/v/entirius-django-lookup.svg)](https://pypi.org/project/entirius-django-lookup/)
[![Python](https://img.shields.io/pypi/pyversions/entirius-django-lookup.svg)](https://pypi.org/project/entirius-django-lookup/)
[![License: MPL-2.0](https://img.shields.io/badge/license-MPL--2.0-blue.svg)](https://github.com/entirius/entirius-django-lookup/blob/develop/LICENSE)

## What it does

- Builds a fingerprint of every product a registered provider exposes: normalized GTIN, brand, part number, name.
- Finds candidates by exact identifiers, trigram and word similarity, perceptual image hash and image embeddings.
- Scores each candidate pair level by level and returns the reasons next to the score.
- Serves two admin endpoints: `/search/` (ranked candidates, nothing written) and `/check/` (a verdict per
  candidate, logged as a decision).
- Ships commands for backfill, reconciliation, a health check of the image layer and calibration.

## Installation

```shell
uv add entirius-django-lookup
pip install entirius-django-lookup
```

```python
INSTALLED_APPS = [
    ...
    "django.contrib.postgres",
    "django_utils",
    "django_lookup",
]

# urls.py
urlpatterns = [
    path("", include("django_lookup.urls")),
]
```

Settings (`LOOKUP_*`), the embedding backend, the Celery queue and the bootstrap order are described in
https://github.com/entirius/entirius-django-lookup/blob/develop/docs/install.md; the settings block to copy is
https://github.com/entirius/entirius-django-lookup/blob/develop/docs/settings_example.py

## Quick start

```python
from django_lookup.schemas.requests.lookup import LookupQuery
from django_lookup.services import lookup_service

result = lookup_service.search(LookupQuery(q="Bosch GSR 12V-15", limit=10))
for hit in result.hits:
    print(hit)
```

The same search over HTTP is `POST /api/lookup/v2/admin/search/`.

## Requirements

- Python 3.12 or newer, Django 5.0 or newer.
- PostgreSQL with the `vector`, `pg_trgm` and `unaccent` extensions available (the migrations create them).
- A Celery worker consuming the `lookup` queue.
- Entirius packages pulled in: `entirius-django-utils`.

## Documentation

- Install (prerequisites, settings, bootstrap order, sizing): https://github.com/entirius/entirius-django-lookup/blob/develop/docs/install.md
- Image embedding backend: https://github.com/entirius/entirius-django-lookup/blob/develop/docs/embedding.md
- Admin API (`/search/` and `/check/`): https://github.com/entirius/entirius-django-lookup/blob/develop/docs/api.md
- Operations (commands, Celery, degradation, tuning, calibration): https://github.com/entirius/entirius-django-lookup/blob/develop/docs/operations.md
- Concept (why a weight, flag or threshold is what it is): https://github.com/entirius/entirius-django-lookup/blob/develop/docs/concept.md
- Gotchas: https://github.com/entirius/entirius-django-lookup/blob/develop/docs/gotchas.md
- Test suite map: https://github.com/entirius/entirius-django-lookup/blob/develop/docs/testing.md
- Entirius documentation: https://docs.entirius.com/
- Changelog: https://github.com/entirius/entirius-django-lookup/blob/develop/CHANGELOG.md

## Part of Entirius

This module runs inside the Entirius backend service (Volkanos); the catalog and the external product data
engine register as its providers.

- Backend service: https://github.com/entirius/entirius-service-volkanos
- Docker harness: https://github.com/entirius/entirius-zeno
- Sibling modules: https://github.com/entirius/entirius-django-pim, https://github.com/entirius/entirius-django-atlas,
  https://github.com/entirius/entirius-django-enrichment

## Development

```shell
make install     # sync dependencies (uv, incl. extras)
make check       # lint + format check (ruff)
make test        # test suite (pytest against PostgreSQL; DATABASE_URL or LOOKUP_TEST_DB_*, see tests/settings.py)
```

Development and agent instructions: https://github.com/entirius/entirius-django-lookup/blob/develop/AGENTS.md

## License

Mozilla Public License 2.0 (MPL-2.0) — see https://github.com/entirius/entirius-django-lookup/blob/develop/LICENSE.
