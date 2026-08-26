---
title: Operations
description: Backfill, reconcile, doctor and eval commands; embedding providers; the SSRF guard; degradation.
---

## Management commands

| Command | Flags | What it does |
|---|---|---|
| `manage.py lookup_backfill` | `--kind`, `--since` (ISO timestamp), `--batch` (default 500), `--images` | Without `--images`: streams every configured provider's `iter_items(since)` in batches and upserts fingerprints (`services/backfill_service.backfill`). With `--images`: does not touch text columns at all — enqueues every existing fingerprint of the kind onto the `lookup` queue in batches of `IMAGE_TASK_BATCH` (32) so the worker fetches/hashes/embeds its picture. |
| `manage.py lookup_reconcile` | `--kind`, `--batch` (default 500) | Repairs drift after lost signals: creates fingerprints for provider items that have none, deletes rows whose item is gone. Idempotent — safe to run on a schedule. Prints `created=N deleted=N` per kind. |
| `manage.py lookup_doctor` | `--skip-worker`, `--worker-timeout` (default 60s) | Fail-closed health check of the image layer: settings, the `image_vec` column's actual halfvec dimension vs `EMBEDDING_DIM`, the HNSW index, a live embed from the web process **and** from the worker (`probe_embedding` task), plus coverage counts (total / hashed / embedded / on-another-model). Exits 1 on any failed check. |
| `manage.py lookup_eval` | `--pairs <csv>` (required), `--thresholds` (default `45,75`), `--image-only`, `--log-decisions` | Calibration harness — see below. Exit code is always 0. `--log-decisions` leaves one `DedupDecision` row per candidate behind (`source=lookup_eval`); off by default, so a full pairs file does not flood the audit log. |

Both `lookup_backfill` and `lookup_reconcile` raise `CommandError` for an unknown `--kind` or an
unregistered provider; every other row-level failure is per-item (a stale ref just does not appear).

## Provider registration

`settings.LOOKUP_PROVIDERS: dict[str, str]` maps a `FingerprintKind` to a dotted module path
implementing the provider protocol (`providers/base.py`): `iter_items(since=None)`, `get_item(ref)`
(raises `LookupError` for an unknown ref), `basic(ref)`, `detail_url(ref)`, and optionally
`signal_specs()` for freshness. `providers/registry.get_provider(kind)` imports lazily and caches by
dotted path — a kind with no entry raises `ValueError` naming the known kinds.

```python
LOOKUP_PROVIDERS: dict[str, str] = {
    "pim_product": "django_pim.services.lookup_provider",
    "atlas_source_product": "django_atlas.services.lookup_provider",
}
```

PIM and atlas never import each other, nor this module: they mirror `ProviderItem` / `BasicData`
(field names are the contract) so an optional consumer never becomes a hard dependency of a catalog.
A host that installs `django_lookup` without one of the catalog modules simply has that kind stay
unwired — `signals._specs()` logs a warning and continues, `lookup_service._scope` drops the kind
from a query and returns `kind_unavailable:<kind>` in `warnings` instead of failing the request.

## Embedding providers

`settings.LOOKUP_EMBEDDING` selects the image-embedding backend; no model is ever loaded in a Django
process — a provider is an HTTP client (or `none`). `LOOKUP_IMAGE_ENABLED` is the separate on/off
switch for the whole image layer.

| Setting | Default | Meaning |
|---|---|---|
| `LOOKUP_PROVIDERS` | `{}` | kind → dotted provider module path |
| `LOOKUP_EMBEDDING` | `{"provider": "none"}` | `{provider, url, model, dim, api_key, timeout_s}` |
| `LOOKUP_IMAGE_ENABLED` | `False` | Image layer on/off |
| `LOOKUP_THRESHOLDS` | `{"match": 75, "review": 45}` | Decision thresholds on the 0–100 score |
| `LOOKUP_EMBED_ALLOWED_HOSTS` | `[]` | Private hosts the embedding client and the catalog/worker image fetch may reach — never the request-supplied `image_url` path (see SSRF guard below) |
| `LOOKUP_EMBED_CONCURRENCY` | `2` | Batches in flight per process — the embedding box is shared hardware |
| `LOOKUP_HNSW_EF_SEARCH` | `60` | `hnsw.ef_search` for the image blocking query — higher = better recall, slower search |
| `LOOKUP_BLOCK_PRIVATE_HOSTS` | `True` | SSRF guard master switch; `False` only in a dev harness whose fixtures/embed containers are private |

`LOOKUP_EMBEDDING["provider"]`:

| Provider | Class | Notes |
|---|---|---|
| `http` | `HttpEmbeddingProvider` | Self-hosted, OpenAI-compatible (Infinity / TEI) — the recommended default. `POST {url}` with `{"model", "input": ["data:image/jpeg;base64,..."]}`. No product photo ever leaves the network. |
| `voyage` | `VoyageEmbeddingProvider` | Hosted (Voyage multimodal embeddings) — the "no GPU anywhere" option. Product photography leaves the network here; a deployment decision, not a default. Defaults to `https://api.voyageai.com/v1/multimodalembeddings` when `url` is unset. |
| `none` | `NullEmbeddingProvider` | Image layer off. `embed_images` raises `ImageLayerDisabled`; pHash still works, it needs no backend. |
| a dotted path | any `EmbeddingProvider` subclass | The same escape hatch `LOOKUP_PROVIDERS` gives the catalogs — how the test suite injects a fake, and how a bespoke backend can be wired without a code change here. |

Example (zeno dev harness — self-hosted Infinity behind the `embed` compose service):

```python
LOOKUP_EMBEDDING = {
    "provider": "http",
    "url": "http://embed:7997/embeddings",
    "model": "google/siglip-so400m-patch14-384",
    "dim": 1152,
    "timeout_s": 10,
}
LOOKUP_EMBED_ALLOWED_HOSTS = ["embed"]
LOOKUP_IMAGE_ENABLED = True
```

`http` and `voyage` share `embedding/hosted.py`: batching (`MAX_BATCH = 64`), bounded parallelism
(`ThreadPoolExecutor(max_workers=get_embed_concurrency())` above one batch), and dimension validation
— a vector of the wrong length raises `EmbeddingError` immediately, so a silent model swap on the
shared box breaks loudly instead of quietly returning vectors that stop matching.

`EMBEDDING_DIM` (`constants.py`) is resolved **once at import** from `LOOKUP_EMBEDDING["dim"]`
(default 1152 = SigLIP so400m) because it shapes the `halfvec(N)` column the migration created.
Changing the configured dimension without a new migration + full re-embed makes
`makemigrations --check` fail on purpose, and `lookup_doctor` reports the mismatch.

## SSRF guard

`security/url_guard.py` protects every outbound fetch the module makes: the embedding endpoint
(`embedding/transport.py`) and remote catalog images (`services/image_service.fetch_remote`). It is
a deliberate sibling of `django_atlas.security.url_guard` rather than a shared import — the lookup
module must never depend on a catalog module.

- `assert_safe_url(url, allowed_hosts=())` rejects non-`http`/`https` schemes, a missing hostname, and
  (unless the host is in `allowed_hosts` or `LOOKUP_BLOCK_PRIVATE_HOSTS` is `False`) any hostname that
  resolves to a private, loopback, link-local, reserved, multicast or unspecified IP.
- `safe_get(url, timeout, cap, allowed_hosts=())` never auto-follows redirects
  (`allow_redirects=False`) — each hop is re-validated by `assert_safe_url(hop, allowed_hosts)` before
  it is fetched, up to `MAX_REDIRECTS = 3`, and the body is capped while streaming (`ValueError` past
  the cap, never buffered unbounded). `allowed_hosts` travels with every hop, not just the first URL.
- `embed_allowed_hosts()` (`LOOKUP_EMBED_ALLOWED_HOSTS`) punches a host-specific hole for a private
  network — `fetch_remote` itself carries no allowlist opinion, its caller decides. The embedding
  service (`embedding/transport.py`) and `image_service._load` (the catalog/worker path — a
  provider-owned URL from `embed_refs`) pass it in. `lookup_service._query_image` (the API `image_url`
  field — REQUEST-supplied, an admin-JWT caller's own input) passes none: punching the same hole there
  would let any admin token use `image_url` as a blind SSRF oracle against the allowlisted hosts, past
  the private-IP check. One setting, two different trust levels — never shared automatically.
- `LOOKUP_BLOCK_PRIVATE_HOSTS = False` drops the IP check wholesale — a dev-harness switch only (zeno:
  the `embed` and `fixtures` containers live on the compose network).

Image size caps: `MAX_UPLOAD_IMAGE_BYTES` (5 MB, what a CMS client may POST — it downscales
client-side first) and `MAX_REMOTE_IMAGE_BYTES` (10 MB, catalog images fetched by the worker or read
from disk), `REMOTE_IMAGE_TIMEOUT_S = 10.0`.

## Degradation, never failure

The image layer is designed to fail soft everywhere:

- A dead or unreachable embedding backend during `embed_refs`/`prepare_query` leaves `image_vec` NULL
  (`phash` is still written) — the next backfill/refresh run retries, and the pHash blocking
  leg keeps working regardless of the embedding backend's health.
- `/search/` and `/check/` still answer when the embedding call fails; the response carries
  `warnings: ["image_layer_unavailable"]` instead of a 5xx.
- An upsert from a provider refresh **never touches** the image columns
  (`phash`/`image_vec`/`vec_model`/`image_sha1`) — the image layer owns them exclusively, so a
  text-only provider refresh cannot blank a picture that was already hashed/embedded.
- A row embedded by a since-replaced model (`vec_model` mismatch) stays invisible to the HNSW blocking
  leg — filtered out, never deleted — until `lookup_backfill --images` re-embeds it.

`LookupConfig.ready()` logs a settings/column dimension mismatch as a **warning** — a service must
still boot with a broken image layer, it just must not stay quiet about it.
`manage.py lookup_doctor` is the fail-closed version of the same check: it exits non-zero.

## Celery queue

Every task in `tasks.py` runs on the `lookup` queue (`constants.CELERY_QUEUE`) — the host worker
**must** consume it (`celery -A main worker -Q ...,lookup`), otherwise freshness signals pile up
unprocessed and only `lookup_reconcile` keeps the table honest. The worker has no autoreload; restart
it after any change to `tasks.py` or what it calls.

| Task | Trigger | Does |
|---|---|---|
| `refresh_fingerprint(kind, ref)` | Freshness signal, after commit | Rebuilds one row from its provider (or deletes it when the provider no longer serves the ref); re-embeds the picture in its own task when the row was rebuilt and the image layer is on |
| `refresh_fingerprints(kind, refs)` | A bulk writer that bypasses signals (e.g. atlas full sync — `import_service._enqueue_lookup_refresh`) | Batched twin of `refresh_fingerprint`: a thin loop over the same single-ref logic, one publish per up to `constants.REFRESH_TASK_BATCH` (200) refs instead of one per row; batches the follow-on image task the same way |
| `embed_fingerprint_images(kind, refs)` | `lookup_backfill --images`, chained from `refresh_fingerprint`/`refresh_fingerprints` | Hashes and embeds the main image of each ref, skipping a row whose `image_sha1` is unchanged and whose `vec_model` is current |
| `probe_embedding()` | `lookup_doctor` (unless `--skip-worker`) | One real embedding call from *inside the worker* — proves worker-side reachability, which web-tier reachability does not |

Initial image backfill on a large catalog: drop `lookup_fp_image_vec_hnsw_idx`, bulk-load, recreate
it — incremental HNSW inserts over roughly a million rows are far slower than one build.

## Deepening the image search

The image legs trade recall for latency, and the defaults are deliberately shallow — they are what the
measured baseline was taken with. Three settings move that trade-off; nothing else about the layer changes:

| Setting | Default | What it buys | What it costs |
|---|---|---|---|
| `LOOKUP_HNSW_EF_SEARCH` | `60` | how hard pgvector looks inside the HNSW graph before answering | CPU per query, roughly linear |
| `LOOKUP_IMAGE_TOP_K` | `20` | neighbours each image leg hands to scoring | more rows scored, and one provider `basic()` per hit that survives |
| `LOOKUP_PHASH_MAX_DISTANCE` | `10` | width of the free near-exact gate, in bits | above ~14 bits unrelated product shots start to slip in |

Three profiles that work as a starting point:

```python
# default — what the documented baseline was measured with
LOOKUP_HNSW_EF_SEARCH = 60
LOOKUP_IMAGE_TOP_K = 20
LOOKUP_PHASH_MAX_DISTANCE = 10

# thorough — an operator hunting a specific product, latency still interactive
LOOKUP_HNSW_EF_SEARCH = 200
LOOKUP_IMAGE_TOP_K = 40
LOOKUP_PHASH_MAX_DISTANCE = 12

# exhaustive — batch work (a catalog audit, a calibration run), not a UI path
LOOKUP_HNSW_EF_SEARCH = 500
LOOKUP_IMAGE_TOP_K = 100
LOOKUP_PHASH_MAX_DISTANCE = 14
```

Rules that keep this honest:

- **Deepening is not free recall.** `ef_search` only helps while the HNSW graph still holds unexplored
  neighbours; past a point it costs latency and returns the same rows. Find that point by measurement.
- **Re-measure after every change** — `make lookup-eval` in the harness prints the blocking-leg recall
  and the decision view. A number in this file or in the harness `AGENTS.md` that was not produced by such
  a run is not a baseline, it is a guess.
- **The image leg is approximate by construction.** On the reference fixture its recall moves between runs
  (0.28–0.34 measured across three seeds at the defaults), so a change smaller than that spread is noise.
  Before comparing two embedding models, raise `ef_search` until repeated runs agree, or the model
  difference will drown in HNSW's own variance.
- **The gate recruits candidates; it does not score them.** `scoring.PHASH_NEAR` (10 bits) is a calibrated
  constant and stays fixed: raising `LOOKUP_PHASH_MAX_DISTANCE` to 14 lets 11–14-bit neighbours reach scoring,
  but they earn no pHash reason there and must win on the text evidence alone. That is deliberate — it widens
  recall without silently moving the band the weights were calibrated on.
- **Widening the pHash gate is the cheapest first step** and the easiest to overdo: it costs one sequential
  scan over a bigint column, but every extra bit lets in visually similar, unrelated products — which then
  need the text legs to disagree with them.

## Re-measuring the calibration numbers

`manage.py lookup_eval --pairs <csv>` runs the same `check()` the API uses over a hand-labelled CSV
(`query_kind,query_ref,candidate_kind,candidate_ref,label,why`, `label ∈ {match, variant, no}`), with
`limit` lifted to `blocking.CANDIDATE_LIMIT` (100) rather than the API's `MAX_LIMIT` (20) so the hit
cap is never the dominant reason a true candidate is missing.

Output, in order: `pairs: N (skipped S, not retrieved R)` and a skip-reason breakdown
(`unknown kind` / `bad label` / `stale ref`); two threshold sweeps (`positives: match`, then
`positives: match+variant` — a `variant` pair is a real, findable duplicate for blocking purposes but
never one `/check/` should call `match`), each with precision/recall/F1/tp/fp/fn/tn per threshold; a
confusion matrix (`label` vs the production `decide()` bucket); and two blocking-recall diagnostics
that isolate one leg of `blocking.candidates()` each via `dataclasses.replace` on the `ParsedQuery` —
`recall@50` blanks the exact-key fields and passes no image (the name-trigram leg alone),
`recall@20` additionally blanks the name fields and passes only the query's own already-embedded
image evidence (the pHash/HNSW legs alone, skipped rather than fabricated when that fingerprint has
no pHash yet). `--image-only` skips the `check()`-driven sections and reports only `recall@20`. Every
row runs `check(..., log=False)` by default — a full pairs file would otherwise leave one
`DedupDecision` per candidate behind; pass `--log-decisions` to keep them (`source=lookup_eval`).

The numbers are a measurement, not a gate — exit code is always 0, and a low number on the
adversarial fixture (test-strategy §4) is data, not a bug. In zeno:

```bash
make seed         # fresh, labelled fixture — needed before every measurement
make lookup-eval  # runs lookup_eval against the seeded pairs, embed container must be up
```

A number only belongs in `AGENTS.md` / the harness `AGENTS.md` after being measured this way on a
fresh seed — never derived or carried forward by assumption (`entirius-zeno/AGENTS.md` §Green
baselines records the current measured run).

### Calibration internals

`eval_service._to_query` mirrors `fingerprint_service.pick_name` and carries `item.attrs`
(weight / width / height / deep) into `Attrs`, so **L6/L7 score during calibration exactly as they
do in production** — a divergence here would silently change the measured numbers without any
production behaviour changing.

The two blocking-recall diagnostics isolate one leg each via `dataclasses.replace` on the
`ParsedQuery`, never a `pool[:K]` slice of the mixed union — a slice would credit an exact or
trigram hit to "image blocking" and truncate real HNSW rows out of the measurement.

Rows the run leaves behind are tagged `DecisionSource.LOOKUP_EVAL`, so an eval run's audit trail is
distinguishable from production `/check/` traffic.
