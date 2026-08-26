# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Values resolved once at import — they shape the schema, so they must not drift at runtime."""

from django_lookup.settings import get_embedding

DEFAULT_EMBEDDING_DIM = 1152  # google/siglip-so400m-patch14-384

# Dimension of Fingerprint.image_vec. Frozen at import because the column type (halfvec(N)) is fixed
# by the migration — changing it means a new migration + full re-embed.
EMBEDDING_DIM: int = int(get_embedding().get("dim", DEFAULT_EMBEDDING_DIM))

# Celery queue for the fingerprint refresh task; the host worker must consume it
# (zeno: the `-Q` list in docker-compose.yml / docker-compose.dev.yml).
CELERY_QUEUE = "lookup"

# Image layer caps. The upload cap is what a CMS client may POST (it downscales client-side first);
# the remote cap covers catalog images fetched by the worker over the network or read from disk.
MAX_UPLOAD_IMAGE_BYTES = 5 * 1024 * 1024
MAX_REMOTE_IMAGE_BYTES = 10 * 1024 * 1024
REMOTE_IMAGE_TIMEOUT_S = 10.0
# Refs per `embed_fingerprint_images` task — one embedding batch, one bulk_update.
IMAGE_TASK_BATCH = 32
# Refs per `refresh_fingerprints` task — a provider-write batch, not an embedding one, so it can
# run far larger than IMAGE_TASK_BATCH: each ref costs a provider fetch plus a single-row upsert
# (no shared compute batch to bound), so the cap only needs to keep one task's runtime and the
# publish it draws from the shared `lookup` queue reasonable. Callers importing a large feed
# (atlas full sync) enqueue in chunks of this size instead of one publish per changed row.
REFRESH_TASK_BATCH = 200
