# Changelog

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
