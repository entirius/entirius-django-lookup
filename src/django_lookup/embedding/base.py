# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Embedding provider contract — the only thing the rest of the module knows about vectors.

No model is ever loaded in a Django process: an implementation is an HTTP client, nothing more
(research r01 §3, notes §Embedding providers). Query images and catalog images go through the same
provider and the same pre-crop, or the cosine numbers mean nothing.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

PROVIDER_NONE = "none"


class EmbeddingError(RuntimeError):
    """The image layer could not answer. Callers degrade — they never turn this into a 5xx."""


class ImageLayerDisabled(EmbeddingError):
    """`LOOKUP_EMBEDDING = {"provider": "none"}` — deliberate, not a fault."""


@dataclass(frozen=True)
class EmbeddingConfig:
    """`settings.LOOKUP_EMBEDDING` as a typed value; the factory is the only place that builds it."""

    provider: str = PROVIDER_NONE
    url: str = ""
    model: str = ""
    dim: int = 0
    api_key: str = ""
    timeout_s: float = 10.0


@dataclass(frozen=True)
class EmbeddingResult:
    vector: list[float]
    model_id: str
    dim: int


@dataclass(frozen=True)
class ProviderInfo:
    """What the backend answers *right now* — the doctor compares it with the configured column."""

    model_id: str
    dim: int


class EmbeddingProvider(ABC):
    def __init__(self, config: EmbeddingConfig) -> None:
        self.config = config

    @abstractmethod
    def embed_images(self, images: list[bytes]) -> list[EmbeddingResult]:
        """One result per input, in order. Raises `EmbeddingError` — including on a dimension mismatch."""

    @abstractmethod
    def info(self) -> ProviderInfo:
        """Live handshake used by `lookup_doctor`; may cost one request."""
