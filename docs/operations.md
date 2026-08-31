---
title: Operations
description: Day 2 — management commands, provider registration, Celery tasks, degradation, outbound fetches (SSRF), tuning the image search, re-measuring calibration.
---

Install-time facts — prerequisites, the settings table, the embedding backend, bootstrap order,
sizing — are in `install.md`. This file starts after the first `lookup_doctor` passed.

## Management commands

| Command | Flags | What it does |
|---|---|---|
| `manage.py lookup_backfill` | `--kind`, `--since` (ISO), `--batch` (500), `--images` | Without `--images`: streams every provider's `iter_items(since)` and upserts fingerprints (`backfill_service.backfill`). With `--images`: touches no text column — enqueues every fingerprint of the kind onto the `lookup` queue in batches of `IMAGE_TASK_BATCH` (32) for the worker to fetch, hash and embed. |
| `manage.py lookup_reconcile` | `--kind`, `--batch` (500) | Repairs drift after lost signals: creates fingerprints for items that have none, deletes rows whose item is gone. Idempotent — schedule it. Prints `created=N deleted=N` per kind. |
| `manage.py lookup_doctor` | `--skip-worker`, `--worker-timeout` (60 s) | Fail-closed health check of the image layer: settings, the `image_vec` column's real dimension vs `EMBEDDING_DIM`, the HNSW index, a live embed from the web process **and** from the worker, a discrimination probe (a black and a white square must embed apart), coverage counts. Exit 1 on any failure. |
| `manage.py lookup_eval` | `--pairs <csv>`, `--thresholds` (`45,75`), `--image-only`, `--log-decisions` | Calibration harness — § Re-measuring. Exit code is always 0. |

`lookup_backfill` and `lookup_reconcile` raise `CommandError` for an unknown `--kind` or an unregistered
provider; every other failure is per item — a stale ref just does not appear.

## Provider registration

`LOOKUP_PROVIDERS` maps a `FingerprintKind` to a dotted module implementing `providers/base.py`:
`iter_items(since=None)`, `get_item(ref)` (raises `LookupError`), `basic(ref)`, `detail_url(ref)`,
optionally `signal_specs()` for freshness and `basics(refs)` / `detail_urls(refs)` for one-round-trip
display data. `registry.get_provider(kind)` imports lazily and caches by path; an unknown kind raises
`ValueError` naming the known ones.

A host missing one catalog module simply has that kind stay unwired: `signals._specs()` logs a warning,
`lookup_service._scope` drops the kind from the query and answers with `kind_unavailable:<kind>` in
`warnings`.

## Freshness and the Celery queue

Every task runs on queue **`lookup`** (`constants.CELERY_QUEUE`); the host worker must consume it.

`signals.connect()` (from `LookupConfig.ready()`) walks each provider's `signal_specs()` — dicts
`{"model": "<app>.<Model>", "signal": "post_save" | "post_delete", "ref": callable}` — and enqueues
`refresh_fingerprint` after commit.

| Task | Trigger | Does |
|---|---|---|
| `refresh_fingerprint(kind, ref)` | freshness signal, after commit | rebuilds one row from its provider, or deletes it when the provider no longer serves the ref; chains an image task when the row was rebuilt and the image layer is on |
| `refresh_fingerprints(kind, refs)` | a bulk writer that bypasses signals (atlas full sync) | the batched twin — one publish per `REFRESH_TASK_BATCH` (200) refs, one image publish per batch |
| `embed_fingerprint_images(kind, refs)` | `lookup_backfill --images`; chained from the refreshes | fetch → pre-crop → pHash → embed → `bulk_update`; skips a row whose `image_sha1` is unchanged *and* `vec_model` current |
| `probe_embedding()` | `lookup_doctor` | one real embedding call from *inside the worker* — web-tier reachability proves nothing about it |

## Degradation, never failure

- A dead embedding backend leaves `image_vec` NULL — `phash` is still written, the next run retries,
  and the pHash leg keeps blocking regardless.
- `/search/` and `/check/` still answer; the response carries `warnings: ["image_layer_unavailable"]`
  instead of a 5xx.
- A provider refresh never touches the image columns; a since-replaced model's rows are filtered from
  the HNSW leg, never deleted, until re-embedded.
- `LookupConfig.ready()` logs a settings/column dimension mismatch as a **warning** — a service boots
  with a broken image layer, it just does not stay quiet. `lookup_doctor` is the fail-closed twin.

## Outbound fetches (SSRF guard)

`security/url_guard.py` — `http`/`https` only; a hostname resolving to a private, loopback, link-local,
reserved, multicast or unspecified address is rejected unless allowlisted; redirects are never
auto-followed (each hop re-validated, ≤ `MAX_REDIRECTS` 3); the body is capped while streaming. A
deliberate sibling of atlas's guard, not a shared import — lookup never depends on a catalog module.

| Fetch | URL comes from | Allowlist | Because |
|---|---|---|---|
| `embedding/transport.py` | the operator (`LOOKUP_EMBEDDING["url"]`) | `LOOKUP_EMBED_ALLOWED_HOSTS` | the embed host is private by design |
| `image_service._load` (worker) | the catalog provider | `LOOKUP_EMBED_ALLOWED_HOSTS` | provider-owned URLs, same trust as the operator's |
| `lookup_service._query_image` (API) | **the request** (`image_url`) | **none** | an admin JWT must not become a blind GET oracle against allowlisted internal hosts |

Caps (`constants.py`): `MAX_UPLOAD_IMAGE_BYTES` 5 MB, `MAX_REMOTE_IMAGE_BYTES` 10 MB,
`REMOTE_IMAGE_TIMEOUT_S` 10. `LOOKUP_BLOCK_PRIVATE_HOSTS = False` drops the IP check wholesale — a
dev-harness switch only.

## Deepening the image search

The defaults are deliberately shallow — they are what the published baseline was measured with.
`similarity` / `match` on `/search/` are derived from the same evidence and move no baseline —
the weights and the verdict path are untouched by them.
Three settings move the recall/latency trade-off; nothing else about the layer changes.

| Setting | Default | Buys | Costs |
|---|---|---|---|
| `LOOKUP_HNSW_EF_SEARCH` | `60` | how hard pgvector searches the HNSW graph | CPU per query, roughly linear |
| `LOOKUP_IMAGE_TOP_K` | `20` | neighbours each image leg hands to scoring | rows scored + one provider `basic()` per surviving hit |
| `LOOKUP_PHASH_MAX_DISTANCE` | `10` | width of the free near-exact gate, bits | above ~14 unrelated product shots slip in |

```python
# default    — the documented baseline          # thorough — operator hunting one product   # exhaustive — batch audits, not a UI path
LOOKUP_HNSW_EF_SEARCH = 60                       LOOKUP_HNSW_EF_SEARCH = 200                  LOOKUP_HNSW_EF_SEARCH = 500
LOOKUP_IMAGE_TOP_K = 20                          LOOKUP_IMAGE_TOP_K = 40                      LOOKUP_IMAGE_TOP_K = 100
LOOKUP_PHASH_MAX_DISTANCE = 10                   LOOKUP_PHASH_MAX_DISTANCE = 12               LOOKUP_PHASH_MAX_DISTANCE = 14
```

- **Deepening is not free recall.** `ef_search` helps only while the graph still holds unexplored
  neighbours; past that point it costs latency and returns the same rows. Find the point by measuring.
- **The gate recruits, it does not score.** `scoring.PHASH_NEAR` (10 bits) stays fixed: a wider
  `LOOKUP_PHASH_MAX_DISTANCE` lets 11–14-bit neighbours reach scoring, where they earn no pHash reason
  and must win on text — recall widens without moving the band the weights were calibrated on.
- **Widen the pHash gate first** — one sequential scan over a bigint column — and stop early: every extra
  bit admits visually similar, unrelated products the text legs then have to reject.
- **Re-measure after every change, on a fresh seed.** With real vectors the image leg is reproducible
  (three fresh seeds, the same `recall@20`); a spread between runs is the embedding route collapsing
  (`lookup_doctor`'s discrimination check), not HNSW noise.

## Re-measuring the calibration numbers

```
manage.py lookup_eval --pairs pairs.csv [--thresholds 45,75] [--image-only] [--log-decisions]
```

CSV columns `query_kind,query_ref,candidate_kind,candidate_ref,label,why`, `label ∈ {match, variant, no}`
— a `variant` is a real, findable duplicate for blocking but never a `/check/` `match`. Every row runs
the production `check()` with `limit` lifted to `blocking.CANDIDATE_LIMIT` (100), so the API's hit cap is
never why a true candidate is missing.

Output, in order: `pairs: N (skipped S, not retrieved R)` with a skip-reason breakdown; two threshold
sweeps (`match`, then `match+variant`) with P/R/F1 per threshold; a confusion matrix against
`decide()`; and two single-leg blocking-recall diagnostics — `recall@50` (name trigram alone) and
`recall@20` (pHash/HNSW alone). How the legs are isolated without crediting one for another's hits:
`services/eval_service.py` docstring.

Rows run with `log=False`; `--log-decisions` keeps one `DedupDecision` per candidate, tagged
`source=lookup_eval`. **The numbers are a measurement, not a gate** — exit code is always 0, and a low
number on the adversarial fixture is data. In zeno: `make seed` (fresh labelled fixture, every time),
then `make lookup-eval` with the embed container up.
