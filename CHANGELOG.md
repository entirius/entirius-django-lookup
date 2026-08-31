# Changelog

## Unreleased

- `/search/` hits report **relevance to the query as given** instead of the strongest dedup reason:
  `similarity` is now 0-100 on the scale the query's modality selects (a photo-only query is judged by
  the photo — the same picture file is 100, cosine 0.99 → 95; text by identifier/name; both by a fixed
  `FIND_IMAGE_WEIGHT` blend, an exact identifier short-circuiting to 100), and `match` says whether the
  hit is `exact` (same identifier or same picture file), `similar` or `none`. `search` ranks by
  relevance; `check`, its score, its verdict and the logged features are untouched. Before, a
  photo-only query could never show more than 10/100 because image evidence weighs ≤ 10 on the dedup
  scale — the two questions now have two scales (`docs/concept.md` § Relevance).
  `django_pim`'s `PossibleDuplicateResponse` mirrors the new field (released alongside); a host on the
  older PIM is unaffected at runtime — unknown keys are ignored.
- Docs restructured by reader job: `docs/install.md` (prerequisites, the single settings table,
  embedding backend choice, bootstrap order, sizing) is new; `AGENTS.md` is a map again, not a second
  operations guide; `docs/gotchas.md` is the only gotcha list; `docs/operations.md` and `docs/api.md`
  lost every fact that now has a home elsewhere. Normalisation rules moved to `docs/concept.md`.
- `docs/settings_example.py` replaces the settings example in prose — `tests/test_docs_example.py`
  asserts on it, so the documented block cannot drift (the previous example pointed at Infinity's
  `/embeddings` text route, the silent catalog-collapse `lookup_doctor` now detects).

## 0.1.1

- `image_prep.encode` downscales to `EMBED_SIDE` (384 px) before handing bytes to the embedding
  backend. SigLIP-384 resizes to 384 px itself, so every pixel above that was decoded, base64'd,
  shipped and discarded — ~176 KB against ~33 KB for a bit-identical vector. It also keeps the
  request clear of the per-string input caps hosted backends apply. The downscale runs on a copy:
  `Image.thumbnail` resizes in place, and the caller hashes the picture it passed in.
- `lookup_doctor` gains a `discrimination` check — it embeds a black and a white square and fails
  when they come back at cosine > 0.99. Reading back `model_id` and `dim` cannot tell an image
  endpoint from a text one: a text route answers 200 with the right model and width, having
  embedded the `data:image/jpeg;base64,` string instead of decoding it. Every photo shares that
  prefix, so the catalog collapses onto one vector and recall goes to noise without an error.

## 0.1.0

Initial release.

- Module skeleton — `Fingerprint` and `DedupDecision` models, migration 0001 (vector / pg_trgm /
  unaccent extensions, GIN trigram + HNSW halfvec indexes), settings, provider protocol + lazy registry,
  normalizers (GTIN, brand, MPN, name / pack / colour / size, pl/en/de dictionaries), `build_fingerprint`.

- Freshness signals (`signals.connect()`, provider-declared `signal_specs()`), the `refresh_fingerprint`
  Celery task on the `lookup` queue, and the `lookup_backfill` / `lookup_reconcile` management commands.

- `POST /api/lookup/v2/admin/search/` and `/check/` — query parsing (`services/query_parser`),
  SQL blocking (exact keys, name trigram) and pairwise scoring with explainable reasons (`services/scoring`,
  levels L0–L7), `IsAdminUser` + JWT, the `lookup_check` throttle, and the generated OpenAPI schema.

- Image layer — `image_prep` (white-background pre-crop, EXIF, signed 64-bit pHash), pluggable
  embedding providers (`http` / `voyage` / `none` / dotted path) with batching, backoff and dimension
  validation, `security/url_guard` (SSRF), `embed_fingerprint_images` task + `lookup_backfill --images`,
  pHash and HNSW blocking legs, scoring level L8 with the `image_only` cap, multipart `/search/` and `/check/`
  with `image_url`, the `lookup_image` throttle, graceful degradation (`image_layer_unavailable`) and the
  `lookup_doctor` command + boot handshake.

- `DedupDecision.subject_ref` and `services/dedup_log` (`record` / `rejected_pairs`) — the
  human-verdict log a proposing caller (atlas enrichment adapter) reads to avoid re-proposing a pair a human
  already rejected. `lookup_service.check` takes the caller's `DecisionSource` (`api_check` /
  `create_hook` / `proposal`) instead of hard-coding one.

- Calibration — `manage.py lookup_eval --pairs <csv> [--thresholds] [--image-only]`
  (`services/eval_service.py`) runs `check()` over a hand-labelled pairs CSV and reports precision/recall/F1
  per threshold (both a `match`-only and a `match+variant` sweep), a confusion matrix, and two blocking-recall
  diagnostics that isolate the name-trigram leg and the image (pHash/HNSW) leg of `blocking.candidates()`.
  Never fails the build — a skipped or unretrieved row is counted, not raised. `DecisionSource.LOOKUP_EVAL`
  tags the `DedupDecision` rows a calibration run leaves behind.

- Module docs (`docs/concept.md`, `docs/api.md`, `docs/operations.md`, `docs/gotchas.md`,
  `docs/testing.md`) and ERD config (`docs/erd-config.yaml`).
