# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Module settings — one accessor per knob, read at call time so the host service and
override_settings always win. Defaults live here and nowhere else.

Only EMBEDDING_DIM (constants.py) is frozen at import: it shapes the halfvec column.
"""

from django.conf import settings

DEFAULT_EMBEDDING: dict = {"provider": "none"}
DEFAULT_THRESHOLDS: dict[str, int] = {"match": 75, "review": 45}
DEFAULT_EMBED_CONCURRENCY = 2  # the embedding box is shared hardware (notes §Remote GPU)
DEFAULT_HNSW_EF_SEARCH = 60  # pgvector recall knob (research r01 §4)
DEFAULT_IMAGE_TOP_K = 20  # neighbours per image leg (research r01 §3)
DEFAULT_PHASH_MAX_DISTANCE = 10  # bits; the free near-exact gate


def get_providers() -> dict[str, str]:
    """kind -> dotted module path implementing the provider protocol (providers/base.py)."""
    return getattr(settings, "LOOKUP_PROVIDERS", {})


def get_embedding() -> dict:
    """Image embedding backend; {"provider": "none"} disables the image layer."""
    return getattr(settings, "LOOKUP_EMBEDDING", DEFAULT_EMBEDDING)


def image_enabled() -> bool:
    return getattr(settings, "LOOKUP_IMAGE_ENABLED", False)


def get_thresholds() -> dict[str, int]:
    """Decision thresholds on the 0-100 score: >= match -> match, >= review -> review, else no_match."""
    return getattr(settings, "LOOKUP_THRESHOLDS", DEFAULT_THRESHOLDS)


def embed_allowed_hosts() -> list[str]:
    """Private hosts the embedding client may call (SSRF guard allowlist)."""
    return getattr(settings, "LOOKUP_EMBED_ALLOWED_HOSTS", [])


def get_embed_concurrency() -> int:
    """How many embedding batches one process may have in flight; the GPU is shared."""
    return int(getattr(settings, "LOOKUP_EMBED_CONCURRENCY", DEFAULT_EMBED_CONCURRENCY))


def get_hnsw_ef_search() -> int:
    """`hnsw.ef_search` for the image blocking query — higher means better recall, slower search."""
    return int(getattr(settings, "LOOKUP_HNSW_EF_SEARCH", DEFAULT_HNSW_EF_SEARCH))


def get_image_top_k() -> int:
    """How many neighbours each image leg returns. Raising it deepens the search — see docs/operations.md."""
    return int(getattr(settings, "LOOKUP_IMAGE_TOP_K", DEFAULT_IMAGE_TOP_K))


def get_phash_max_distance() -> int:
    """Width of the free pHash gate in bits. Above ~14 unrelated product shots start to slip in."""
    return int(getattr(settings, "LOOKUP_PHASH_MAX_DISTANCE", DEFAULT_PHASH_MAX_DISTANCE))


def block_private_hosts() -> bool:
    """SSRF guard master switch; False only in a dev harness whose fixtures live on a private network."""
    return bool(getattr(settings, "LOOKUP_BLOCK_PRIVATE_HOSTS", True))
