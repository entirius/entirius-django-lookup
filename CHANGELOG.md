# Changelog

## [Unreleased]

- Access: the module declares its own access areas on its AppConfig and its admin views (copied from the
  entirius-django-access defaults; behaviour unchanged).

## 0.3.0

- **A brand typed into the query name no longer lowers its own score.** The indexer removes the
  brand its provider supplies from `name_norm` wherever it sits; the query side strips a brand only
  when the caller passes a `brand` field or a dictionary brand leads the name. Naming a product the
  natural way — brand inside the name, no `brand` field — therefore compared `drill brandx 18v`
  against `drill 18v` (trigram 0.72, below the review line) while the same name without the brand
  word scored 1.00. `name_similarity` is now the better of two measurements: the query name as given,
  and with the candidate's own `brand_norm` removed from it as a whole word — per candidate, exactly
  what the index removed from that row. No normaliser change, no new brand vocabulary, no index
  rebuild; `brand_norm` and the brand level are untouched, so a compatibility word ("case for
  Apple ...") cannot turn into a false `brand_conflict`.
- **Word-similarity blocking leg.** `similarity` is over the whole string, so a query of "make +
  model" typed against a long marketing title never reached `TRIGRAM_FLOOR` and returned zero
  candidates — silence, which reads as "not in the catalog". A fifth leg, pg_trgm
  `word_similarity(query, name_norm)` ≥ 0.6 (`<%`, served by the existing GIN index — no migration),
  returns the top 50 stored names in which the query matches a run of consecutive words; a typo still
  passes, a single-word query never triggers it. It runs after the image legs, so a text+image query
  cannot lose its HNSW neighbours to it at the `CANDIDATE_LIMIT` cut. Scoring is untouched: such a hit
  carries `name_tokens_strong` and shows in `/search/` at full text relevance and in `/check/` as a
  visible candidate with its reasons.
- Docs: `concept.md` (five blocking legs, the two-sided similarity), `gotchas.md`, `testing.md`.

## 0.2.1

- The embedding can veto the pHash same-file shortcut in `/search/` relevance: pHash <=
  `PHASH_NEAR_EXACT` with a cosine below `SAME_FILE_COSINE` (0.95) is a DCT collision (flat
  packshots on white collide easily), not the same picture — the hit drops to the reworked-shot
  band (85, `similar`) instead of 100/`exact`. Without a comparable vector the pHash stands alone,
  as before. `/check/`, its score and verdict are untouched.
- `docs/embedding.md` — the embedding backend's own page: enabling it, running the reference
  Infinity container, the wire contract any OpenAI-compatible alternative must satisfy, bringing
  your own `EmbeddingProvider`, and changing the model later. `install.md` keeps only the
  install-time choice table.

## 0.2.0

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
