---
title: Embedding backend
description: Running the image-embedding engine, the wire contract any alternative must satisfy, bringing your own provider, and changing the model later.
---

The vector leg of the image search is an HTTP service — **no model is ever loaded in a Django
process**. The worker calls it when hashing catalog photos (`lookup_backfill --images`, freshness
refreshes) and the web tier calls it once per image query. When it is down or slow the engine
degrades: pHash and the text legs keep answering, `/search/` adds an `image_layer_unavailable`
warning, and nothing turns into a 5xx. Which provider to *choose* (self-hosted `http`, hosted
`voyage`, `none`, or your own class) is the table in `install.md` § Embedding backend; this file is
everything below that choice.

## Enabling it

1. Copy the settings block from `docs/settings_example.py` (`provider`, `url`, `model`, `dim`,
   `timeout_s`) and set `LOOKUP_IMAGE_ENABLED = True`.
2. Name the backend's host in `LOOKUP_EMBED_ALLOWED_HOSTS` when it lives on a private address — the
   SSRF guard refuses private hosts it was not told about.
3. Backfill and verify in the order `install.md` § Bootstrap order gives; the verdict on the whole
   layer is `manage.py lookup_doctor` — exit 0, or do not ship.

## Running the reference backend (Infinity)

[Infinity](https://github.com/michaelfeil/infinity) serving `google/siglip-so400m-patch14-384`
(1152-d) is the reference `http` backend:

```
michaelf34/infinity:0.0.77-cpu   v2 --model-id google/siglip-so400m-patch14-384 --port 7997 --engine torch --device cpu  --no-trust-remote-code
michaelf34/infinity:0.0.77       …same, --device cuda   (GPU; needs nvidia CDI or the container toolkit)
```

- First start downloads ~3.3 GB into `HF_HOME` — put it on a volume, and set `HF_HUB_DISABLE_XET=1`
  (the Xet transfer stalls mid-blob; the plain CDN path finishes).
- **Bind it to loopback or the private network only.** The API has no auth and fetches URLs
  server-side — on a LAN it is an SSRF pivot.
- **The URL ends in `/embeddings_image`.** `/embeddings` is the text route: it answers 200 with the
  right model and width, having embedded the `data:image/jpeg;base64,…` *string* — every photo
  shares that prefix, so the whole catalog collapses onto one vector and recall becomes noise
  without an error. `lookup_doctor`'s `discrimination` check exists to catch exactly this.
- Remote GPU box (`https://ai.internal/…`): add `api_key`, keep `timeout_s` ~10, and confirm both the
  web tier **and** the worker reach it — `lookup_doctor` probes both.

CPU vs GPU is only throughput — the vectors are identical; per-image costs are the sizing table in
`install.md`.

## The wire contract (any alternative `http` endpoint)

`provider = "http"` speaks the OpenAI embeddings shape and works with any endpoint that satisfies:

| Aspect | Requirement |
|---|---|
| Request | `POST {url}` with `{"model": "<model>", "input": ["data:image/jpeg;base64,…", …]}`, `Authorization: Bearer <api_key>` when configured |
| Input | each item is an inline base64 **data URL**; the endpoint must embed it *as an image* (Infinity: the `/embeddings_image` route; text-only servers disqualify themselves via the doctor's discrimination check) |
| Response | `{"data": [{"index": N, "embedding": [floats]}, …], "model": "…"}` — one vector per input; the declared `index` decides order (the array is documented as unordered) |
| Dimension | every vector must have exactly `LOOKUP_EMBEDDING["dim"]` values — a mismatch fails the batch, never writes a truncated vector |
| Batching | at most 64 images per request; `LOOKUP_EMBED_CONCURRENCY` (default 2) batches in flight per process |
| Errors | 429/503 are retried 3× with exponential backoff (a shared GPU means "wait"); anything else loses the batch and the caller degrades |

[Infinity](https://github.com/michaelfeil/infinity) is the reference; text-embeddings-inference and
other OpenAI-compatible servers work when they expose a real *image* route — always confirm with
`lookup_doctor`, whose discrimination probe (a black and a white square must embed apart) is the
test that matters.

## Hosted (Voyage)

`provider = "voyage"` posts to `https://api.voyageai.com/v1/multimodalembeddings` (override with
`url`), wraps each image in Voyage's content-list body, and needs `api_key`. Same contract, same
degradation — but **product photography leaves the network**, which is a deployment decision, not a
default (`install.md`'s table).

## Bringing your own provider

`LOOKUP_EMBEDDING["provider"]` accepts a dotted path to an `EmbeddingProvider` subclass — the same
escape hatch `LOOKUP_PROVIDERS` gives the catalogs. The contract (`embedding/base.py`):

- `embed_images(images: list[bytes]) -> list[EmbeddingResult]` — one result per input, **in
  order**; raise `EmbeddingError` on any failure (callers degrade, they never 5xx).
- `info() -> ProviderInfo` — a live handshake reporting the model id and dimension actually
  returned; `lookup_doctor` compares it with the configured column. May cost one request.
- The constructor receives an `EmbeddingConfig` built from the settings dict (`provider`, `url`,
  `model`, `dim`, `api_key`, `timeout_s` — unknown keys are ignored).

For anything HTTP-shaped, subclass `HostedEmbeddingProvider` instead and override only `endpoint`
and `payload(images)` — batching, bounded parallelism, dimension validation, index-ordered decoding
and the SSRF-guarded transport with backoff are inherited. `embedding/voyage_provider.py` is the
whole pattern in 24 lines. The test suite's `tests/fake_embedding.py` is the same mechanism used to
inject a deterministic fake.

## Changing the model (or backend) later

- **Same dimension, different model** — change `model`: it is the `vec_model` stamp on every row.
  Rows embedded by the old model are filtered out of the vector leg (never deleted) until
  `lookup_backfill --images` re-embeds them; `lookup_doctor` counts the strays. pHash keeps blocking
  throughout.
- **Different dimension** — the column is frozen: migration 0001 created `Fingerprint.image_vec` as
  `halfvec(dim)`. A new dimension means a new migration and a full re-embed; `makemigrations
  --check` fails on purpose and `lookup_doctor` reports the mismatch. Decide the model before the
  first big backfill (`install.md`).
- After any swap, re-measure the image-leg calibration (`operations.md` § Re-measuring) — cosine
  bands are model-dependent.
