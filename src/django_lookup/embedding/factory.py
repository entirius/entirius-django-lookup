# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""`settings.LOOKUP_EMBEDDING` -> a provider instance, resolved at call time like every other knob.

`provider` is one of the built-in names, or a dotted path to an `EmbeddingProvider` subclass — the
same escape hatch `LOOKUP_PROVIDERS` gives the catalogs, and how the test suite injects a fake.
"""

from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string

from django_lookup.constants import EMBEDDING_DIM
from django_lookup.embedding.base import PROVIDER_NONE, EmbeddingConfig, EmbeddingProvider
from django_lookup.embedding.http_provider import HttpEmbeddingProvider
from django_lookup.embedding.null_provider import NullEmbeddingProvider
from django_lookup.embedding.voyage_provider import VoyageEmbeddingProvider
from django_lookup.settings import get_embedding

BUILTIN_PROVIDERS: dict[str, type[EmbeddingProvider]] = {
    "http": HttpEmbeddingProvider,
    "voyage": VoyageEmbeddingProvider,
    PROVIDER_NONE: NullEmbeddingProvider,
}


def embedding_config() -> EmbeddingConfig:
    settings_value = dict(get_embedding())
    known = {field: settings_value[field] for field in EmbeddingConfig.__dataclass_fields__ if field in settings_value}
    return EmbeddingConfig(**known)


def get_embedding_provider() -> EmbeddingProvider:
    config = embedding_config()
    return _provider_class(config.provider)(config)


def current_model_id() -> str:
    """The `vec_model` stamp. Rows embedded by another model stay invisible to search, never deleted."""
    config = embedding_config()
    return config.model or config.provider


def dimension_mismatch() -> str:
    """Settings dim vs the column the migration froze — a message, or "" when they agree.

    Called at boot (a warning, never a hard stop) and by `lookup_doctor` (which does exit non-zero).
    """
    configured = embedding_config().dim
    if configured and configured != EMBEDDING_DIM:
        return (
            f'LOOKUP_EMBEDDING["dim"] is {configured} but Fingerprint.image_vec is halfvec({EMBEDDING_DIM}) — '
            "the column dimension is frozen by the migration; add a migration and re-embed the catalog"
        )
    return ""


def _provider_class(name: str) -> type[EmbeddingProvider]:
    if name in BUILTIN_PROVIDERS:
        return BUILTIN_PROVIDERS[name]
    if "." not in name:
        raise ImproperlyConfigured(
            f'unknown LOOKUP_EMBEDDING["provider"] {name!r} (known: {sorted(BUILTIN_PROVIDERS)}, or a dotted path)'
        )
    try:
        return import_string(name)
    except ImportError as exc:
        raise ImproperlyConfigured(f'LOOKUP_EMBEDDING["provider"] {name!r} cannot be imported: {exc}') from exc
