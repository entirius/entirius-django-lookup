# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""`docs/settings_example.py` is the block operators paste — it must satisfy the rules `docs/install.md` states."""

import runpy
from pathlib import Path

from django_lookup.constants import DEFAULT_EMBEDDING_DIM
from django_lookup.embedding.factory import BUILTIN_PROVIDERS

EXAMPLE = Path(__file__).resolve().parent.parent / "docs" / "settings_example.py"


def test_settings_example_follows_the_install_guide():
    example = runpy.run_path(str(EXAMPLE))
    embedding = example["LOOKUP_EMBEDDING"]

    assert embedding["provider"] in BUILTIN_PROVIDERS
    # The text route embeds a data URL as a string and every photo shares its prefix.
    assert embedding["url"].endswith("/embeddings_image")
    # Anything else needs a migration; the example must match the shipped column.
    assert embedding["dim"] == DEFAULT_EMBEDDING_DIM
    assert embedding["timeout_s"] > 0
    # The example names a private host, so the guard must be told about it.
    assert example["LOOKUP_EMBED_ALLOWED_HOSTS"] == ["embed"]
    assert example["LOOKUP_IMAGE_ENABLED"] is True
    assert set(example["LOOKUP_PROVIDERS"]) == {"pim_product", "atlas_source_product"}
