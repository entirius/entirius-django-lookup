# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Everything a hosted (HTTP) provider does apart from shaping its request body.

Batching, bounded parallelism, dimension validation and response decoding are identical whatever
the vendor; only the payload differs, so that is the single abstract hook.
"""

from abc import abstractmethod
from concurrent.futures import ThreadPoolExecutor

from django_lookup.embedding import transport
from django_lookup.embedding.base import EmbeddingError, EmbeddingProvider, EmbeddingResult, ProviderInfo
from django_lookup.services.image_prep import probe_bytes
from django_lookup.settings import get_embed_concurrency


class HostedEmbeddingProvider(EmbeddingProvider):
    @property
    @abstractmethod
    def endpoint(self) -> str:
        """Absolute URL of the embeddings resource."""

    @abstractmethod
    def payload(self, images: list[bytes]) -> dict:
        """Vendor-specific request body for one batch."""

    def embed_images(self, images: list[bytes]) -> list[EmbeddingResult]:
        if not images:
            return []
        return [EmbeddingResult(vector, self.config.model, len(vector)) for vector in self._vectors(images)]

    def info(self) -> ProviderInfo:
        """One probe image down the real path — the only way to learn the dimension actually returned."""
        body = self._post([probe_bytes()])
        return ProviderInfo(model_id=body.get("model") or self.config.model, dim=len(_vectors_of(body)[0]))

    def _vectors(self, images: list[bytes]) -> list[list[float]]:
        chunks = transport.batches(images)
        if len(chunks) == 1:
            return self._batch(chunks[0])
        with ThreadPoolExecutor(max_workers=get_embed_concurrency()) as pool:
            return [vector for chunk in pool.map(self._batch, chunks) for vector in chunk]

    def _batch(self, images: list[bytes]) -> list[list[float]]:
        vectors = _vectors_of(self._post(images))
        if len(vectors) != len(images):
            raise EmbeddingError(f"embedding backend returned {len(vectors)} vectors for {len(images)} images")
        for vector in vectors:
            _assert_dimension(vector, self.config.dim)
        return vectors

    def _post(self, images: list[bytes]) -> dict:
        return transport.post_json(self.endpoint, self.payload(images), self.config.api_key, self.config.timeout_s)


def _vectors_of(body: dict) -> list[list[float]]:
    """`data` is documented as unordered, so the declared index decides — never the arrival order."""
    try:
        rows = sorted(body["data"], key=lambda row: row.get("index", 0))
        return [row["embedding"] for row in rows]
    except (KeyError, TypeError) as exc:
        raise EmbeddingError(f"unexpected embedding response shape: {exc}") from exc


def _assert_dimension(vector: list[float], expected: int) -> None:
    """A silent model swap on the shared box must break here, not in the search results."""
    if len(vector) != expected:
        raise EmbeddingError(
            f"embedding dimension {len(vector)} does not match the configured {expected} — "
            "the column is halfvec(dim); re-point the model or re-embed the catalog"
        )
