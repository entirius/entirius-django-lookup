# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Self-hosted, OpenAI-compatible image embeddings (Infinity / TEI) — the recommended default.

`POST {url}` with `{"model": ..., "input": ["data:image/jpeg;base64,..."]}`; no product photo ever
leaves the network and the Django processes stay torch-free (notes §Embedding providers).
"""

from django_lookup.embedding import transport
from django_lookup.embedding.hosted import HostedEmbeddingProvider


class HttpEmbeddingProvider(HostedEmbeddingProvider):
    @property
    def endpoint(self) -> str:
        return self.config.url

    def payload(self, images: list[bytes]) -> dict:
        return {"model": self.config.model, "input": [transport.data_url(image) for image in images]}
