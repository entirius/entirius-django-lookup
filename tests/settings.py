# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Standalone test settings — Postgres only (pgvector / pg_trgm / unaccent have no sqlite equivalent).

DB resolution: DATABASE_URL when set (zeno container), else LOOKUP_TEST_DB_* (host default = zeno's
published port 5532). The role must be allowed to CREATE DATABASE and CREATE EXTENSION (superuser in
the pgvector image).
"""

import os

import dj_database_url

SECRET_KEY = "not so secret test secret"  # noqa: S105 — test-only
DEBUG = True
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.postgres",
    "rest_framework",
    "rest_framework_simplejwt",
    "drf_spectacular",
    "django_lookup",
]

MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
]

ROOT_URLCONF = "tests.urls"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework_simplejwt.authentication.JWTAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "django_utils.api.v2_errors.v2_exception_handler",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "django-lookup Admin API v2",
    "VERSION": "2.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    # Pydantic documents examples the JSON Schema 2020-12 way, which is legal from 3.1 on.
    "OAS_VERSION": "3.1.0",
}

_DEFAULT_URL = (
    f"postgresql://{os.environ.get('LOOKUP_TEST_DB_USER', 'entirius')}:"
    f"{os.environ.get('LOOKUP_TEST_DB_PASSWORD', 'entirius-dev')}@"
    f"{os.environ.get('LOOKUP_TEST_DB_HOST', 'localhost')}:"
    f"{os.environ.get('LOOKUP_TEST_DB_PORT', '5532')}/"
    f"{os.environ.get('LOOKUP_TEST_DB_NAME', 'entirius')}"
)
DATABASES = {"default": dj_database_url.parse(os.environ.get("DATABASE_URL", _DEFAULT_URL))}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOOKUP_PROVIDERS = {"fake": "tests.fake_provider"}

# Image layer: on, with a deterministic in-process provider. `dim` must equal the default the
# migration froze into halfvec(N) — the suite runs the real migrations.
LOOKUP_IMAGE_ENABLED = True
LOOKUP_EMBEDDING = {"provider": "tests.fake_embedding.FakeEmbeddingProvider", "model": "fake-model", "dim": 1152}
# The suite never leaves the process, but `test_url_guard` asserts the guard is armed by default.
LOOKUP_BLOCK_PRIVATE_HOSTS = True
