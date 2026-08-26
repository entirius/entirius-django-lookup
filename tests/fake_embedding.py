# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Deterministic embedding provider for the suite — wired through `LOOKUP_EMBEDDING["provider"]`.

The vector is derived from the bytes, so the same picture always embeds the same way, and two
different pictures land far apart. `fail_with()` makes the backend "unreachable" for degrade tests.
"""

import hashlib
import random

from django_lookup.constants import EMBEDDING_DIM
from django_lookup.embedding.base import EmbeddingProvider, EmbeddingResult, ProviderInfo

MODEL_ID = "fake-model"

_FAILURE: type[Exception] | None = None
CALLS: list[int] = []


def reset() -> None:
    global _FAILURE
    _FAILURE = None
    CALLS.clear()


def fail_with(exception: type[Exception] | None) -> None:
    global _FAILURE
    _FAILURE = exception


def vector_for(data: bytes) -> list[float]:
    seed = int.from_bytes(hashlib.sha1(data, usedforsecurity=False).digest()[:8], "big")
    generator = random.Random(seed)  # noqa: S311 — reproducible test fixtures, not cryptography
    return [generator.uniform(-1.0, 1.0) for _ in range(EMBEDDING_DIM)]


class FakeEmbeddingProvider(EmbeddingProvider):
    def embed_images(self, images: list[bytes]) -> list[EmbeddingResult]:
        CALLS.append(len(images))
        if _FAILURE is not None:
            raise _FAILURE("fake embedding backend is down")
        return [EmbeddingResult(vector_for(image), MODEL_ID, EMBEDDING_DIM) for image in images]

    def info(self) -> ProviderInfo:
        if _FAILURE is not None:
            raise _FAILURE("fake embedding backend is down")
        return ProviderInfo(model_id=MODEL_ID, dim=EMBEDDING_DIM)
