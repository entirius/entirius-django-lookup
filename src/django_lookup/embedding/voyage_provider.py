# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Hosted multimodal embeddings (Voyage) — the "no GPU anywhere" option.

Same contract as the self-hosted provider, different body: Voyage wraps every image in a content
list. Product photography leaves the network here, which is a deployment decision, not a default.
"""

from django_lookup.embedding import transport
from django_lookup.embedding.hosted import HostedEmbeddingProvider

DEFAULT_URL = "https://api.voyageai.com/v1/multimodalembeddings"


class VoyageEmbeddingProvider(HostedEmbeddingProvider):
    @property
    def endpoint(self) -> str:
        return self.config.url or DEFAULT_URL

    def payload(self, images: list[bytes]) -> dict:
        content = [{"content": [{"type": "image_base64", "image_base64": transport.data_url(i)}]} for i in images]
        return {"model": self.config.model, "inputs": content, "input_type": "document"}
