# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""`provider = "none"`: no vectors anywhere. pHash/dHash still work — they need no backend at all."""

from django_lookup.embedding.base import (
    PROVIDER_NONE,
    EmbeddingProvider,
    EmbeddingResult,
    ImageLayerDisabled,
    ProviderInfo,
)


class NullEmbeddingProvider(EmbeddingProvider):
    def embed_images(self, images: list[bytes]) -> list[EmbeddingResult]:
        raise ImageLayerDisabled(f'LOOKUP_EMBEDDING["provider"] is "{PROVIDER_NONE}" — no vectors are produced')

    def info(self) -> ProviderInfo:
        return ProviderInfo(model_id=PROVIDER_NONE, dim=0)
