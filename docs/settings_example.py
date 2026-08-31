# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The settings block `docs/install.md` tells operators to copy: a self-hosted Infinity backend on an
in-network host called `embed`.

`tests/test_docs_example.py` imports this file and asserts on it — keep it importable without Django
(plain assignments only) so the documented example cannot drift from the rules it illustrates.
"""

LOOKUP_PROVIDERS: dict[str, str] = {
    "pim_product": "django_pim.services.lookup_provider",
    "atlas_source_product": "django_atlas.services.lookup_provider",
}

LOOKUP_EMBEDDING: dict = {
    "provider": "http",
    # Infinity's *image* route. `/embeddings` is the text route: it answers 200 with the right model
    # and width, having embedded the data URL as a string — the whole catalog collapses onto one vector.
    "url": "http://embed:7997/embeddings_image",
    "model": "google/siglip-so400m-patch14-384",
    "dim": 1152,  # frozen into Fingerprint.image_vec as halfvec(1152) by migration 0001
    "timeout_s": 10,
}
LOOKUP_EMBED_ALLOWED_HOSTS: list[str] = ["embed"]  # a private host: the SSRF guard needs it named
LOOKUP_IMAGE_ENABLED = True
