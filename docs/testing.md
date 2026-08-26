# Test suite map

- Suites: normalizer tables (`test_gtin`, `test_brand`, `test_mpn`, `test_name`), dictionary invariants
  (`test_dictionaries`: keys already folded, views immutable), registry with `tests/fake_provider.py`,
  `build_fingerprint`, model/index smoke, query parsing (`test_query_parser`), golden pairs + properties
  (`test_scoring`), blocking recall (`test_blocking`: 1000 rows / 20 probes / recall@50), `search` and `check`
  (`test_lookup_service`), the API contract incl. 400/401/403/429 (`test_lookup_api`) and the generated
  OpenAPI document (`test_openapi`). Image layer: `test_image_prep` (crop/EXIF/hash stability/format gate),
  `test_embedding` (factory, batching, dimension mismatch, backoff), `test_url_guard`, `test_image_service`
  (skip/retry/degrade), `test_image_blocking` (both SQL legs), `test_image_scoring` (L8 tiers, `image_only`),
  `test_image_api` (multipart, validation, degrade, throttle) and `test_lookup_doctor`. Calibration:
`test_eval_service.py` (CSV parsing, threshold sweep, confusion, both recall diagnostics) and
`test_lookup_eval_command.py` (the command never raises, `--image-only` output shape).
