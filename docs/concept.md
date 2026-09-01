---
title: Concept
description: Lookup vs dedup, the fingerprint model, blocking, scoring and the decision rules.
---

django-lookup answers two related but distinct questions over the PIM and atlas (supplier feed)
catalogs: "does something like this already exist?" (**lookup**) and "is this specific product a
duplicate of something we already have?" (**dedup**). Both go through the same pipeline — parse the
query, generate candidates cheaply (blocking), score each candidate against the query (scoring),
turn the score into a verdict (decide) — the only difference is whether the caller wants the ranked
candidates (`/search/`) or the same candidates plus a verdict (`/check/`).

## The fingerprint model

`Fingerprint(kind, ref)` is the normalised, index-ready view of one catalog item — a PIM product or
an atlas source product. It holds **only normalised columns**; raw values stay in the source module,
which the fingerprint never re-derives from and never edits by hand. One row per `(kind, ref)`,
rebuilt whenever the provider's own data changes (freshness signals) or explicitly (`lookup_backfill`
/ `lookup_reconcile`).

| Group | Columns | Purpose |
|---|---|---|
| Identifiers | `gtin14` (+ `gtin_trusted`), `brand_norm`, `mpn_norm` | Exact-match keys — GS1-validated GTIN, folded brand, normalised MPN |
| Name | `name_norm` (GIN trigram), `name_tokens` (sorted, for Jaccard/token overlap) | Fuzzy-match keys |
| Variant | `pack_qty`, `color`, `size` | Discriminators — never let fuzzy evidence outvote them |
| Physical | `weight`, `width`, `height`, `deep` (Decimal) | Tolerance-based agreement, silent when absent on either side |
| Image | `phash` (signed 64-bit), `image_vec` (`halfvec(EMBEDDING_DIM)`, HNSW cosine), `vec_model`, `image_sha1` | Perceptual hash + embedding evidence (image layer) |
| Audit | `source_updated_at`, `created_at` / `modified_at` (`BaseModel`) | Freshness bookkeeping |

`kind` is the key into `settings.LOOKUP_PROVIDERS` (`FingerprintKind`: `pim_product` /
`atlas_source_product`); `ref` is the catalog's own identifier (a PIM sku, or `<source_idx>:<external_id>`
for an atlas source product).

## Blocking: cheap candidate generation

Scoring every fingerprint against every query would not scale, so `services/blocking.candidates()`
narrows the table to a bounded pool (`CANDIDATE_LIMIT = 100`) *before* scoring runs — a UNION of four
legs, each cheap in SQL:

| Leg | Mechanism | Cap |
|---|---|---|
| Exact keys | `gtin14` equality, `brand_norm + mpn_norm` equality, `ref` equality (B-tree) | all matches |
| pHash neighbourhood | `bit_count(phash # query) <= 10` (seq scan over 8-byte columns) | top 20 |
| Name trigram | `pg_trgm` GIN index, similarity ≥ `TRIGRAM_FLOOR` (0.35) | top 50 |
| Image embedding | pgvector HNSW, cosine distance, same `vec_model` only | top 20 |

Every row blocking returns carries the annotations scoring needs: `name_similarity` (the pg_trgm
value) and, when the query has a vector, `image_distance` (cosine distance) — computed once, on the
same query text/vector, for every leg, so scoring and blocking never disagree about what a candidate
looked like. Scoring never sees a row blocking did not return: a candidate that misses every leg is
invisible to `/search/` and `/check/`, however good it would have scored — this is why
`lookup_eval`'s blocking-recall diagnostics matter (see `operations.md`).

## Scoring: additive, explainable evidence

`services/scoring.score_pair(query, candidate, image)` compares a parsed query against one candidate
fingerprint across eight core levels (`_LEVELS`, L0–L7) plus L8 image evidence when a picture is
present (research r02 §1/§3/§4). Every level that fires emits a `Reason`
(`code`, `label`, `score`, `observed`) — so the total is always explainable, and `sum(reason.score)`
reconstructs the score before the final 0–100 clamp. The same reasons list is what the API returns
and what `DedupDecision.features` logs.

### Levels and weights (`scoring.WEIGHTS`)

| Level | Signal | Weight |
|---|---|---|
| L0 | `gtin_exact` — trusted GTIN identical | **60** |
| L0b | `gtin_exact_untrusted` — GTIN identical but a restricted-circulation prefix | 15 |
| L1 | `brand_mpn_exact` — brand + MPN identical | **45** |
| L1b | `mpn_exact` — MPN identical, brand unknown on one side | 20 |
| L1c | `mpn_exact_brand_conflict` — MPN identical but the brand differs | **−30** |
| L2 | `sku_exact` — catalog reference identical (never carries a verdict alone) | 30 |
| L3 | `name_trigram` — linear map of pg_trgm similarity (see below), max at the ceiling | 25 |
| L4 | `name_tokens_strong` — `token_set_ratio` ≥ 0.85 | 20 |
| L4b | `name_tokens_weak` — `token_set_ratio` ≥ 0.60 | 10 |
| L5 | `brand_equal` | 10 |
| L5b | `brand_conflict` | **−25** |
| L6a | `weight_match` / `weight_conflict` | 5 / **−15** |
| L6b | `dimensions_match` / `dimensions_conflict` | 5 / **−15** |
| L7 | `color_equal` / `color_conflict` | 5 / **−40** |
| L7 | `size_equal` / `size_conflict` | 5 / **−40** |
| L7 | `pack_equal` / `pack_conflict` | 5 / **−40** |
| L8a | `image_near_exact` / `image_near` — pHash Hamming distance ≤ 5 / ≤ 10 | 10 / 6 |
| L8b | `image_similar_strong` / `image_similar` — embedding cosine ≥ 0.90 / ≥ 0.80 | 10 / 5 |

### Tolerances and thresholds behind the levels

| Constant | Value | Meaning |
|---|---|---|
| `TRIGRAM_FLOOR` | 0.35 | Below it the trigram signal scores nothing (also blocking's cutoff) |
| `TRIGRAM_CEILING` | 0.9 | At and above it the trigram signal is worth the full `name_trigram` weight |
| `TOKENS_STRONG` | 0.85 | `token_set_ratio` cutoff for `name_tokens_strong` |
| `TOKENS_WEAK` | 0.6 | `token_set_ratio` cutoff for `name_tokens_weak` |
| `WEIGHT_TOLERANCE` | 5% (`WEIGHT_MIN_TOLERANCE` floor 0.01 kg / 10 g) | Weight agreement band |
| `DIMENSION_TOLERANCE` | 3% per axis | Width/height/depth agreement band, axis order ignored (a box can be rotated) |
| `PHASH_NEAR_EXACT` | 5 | pHash Hamming distance — same picture (re-saved/resized/recompressed) |
| `PHASH_NEAR` | 10 | pHash Hamming distance — same shot, reworked |
| `COSINE_STRONG` | 0.90 | Embedding cosine similarity — strong image match |
| `COSINE_SIMILAR` | 0.80 | Embedding cosine similarity — floor for any image evidence |

L3's trigram score is a **linear map**: `WEIGHTS["name_trigram"] * (min(similarity, CEILING) - FLOOR) / (CEILING - FLOOR)`,
rounded — 0 at the floor, the full 25 points at and above the ceiling.

### Flags and the decision rules

Five flags can be raised while scoring a pair:

- `identifier_exact` — a trusted GTIN or a brand+MPN hit fired. An exact identifier always earns at
  least `review`, whatever the rest of the score says: a bare EAN query has nothing else to add up.
- `brand_conflict` / `variant_conflict` — the brand, colour, size or pack quantity disagree. Either
  **caps the verdict at `review`**, however high the additive score climbed — fuzzy evidence must
  never outvote a hard discriminator.
- `image_only` — the picture is the *only* positive evidence (every non-image level scored ≤ 0). It
  can never reach `match` on its own: two black t-shirts photograph alike (research r01 §2/§3), so a
  picture is evidence, never proof.

- `brand_dirty_data` — a brand conflict that is **not** treated as a discriminator, because a trusted
  GTIN, a strong name and the physicals all corroborate the pair. A recycled barcode (the case the cap
  was written for in research r02 §4) changes the product name as well; a corrupted brand field in one
  feed does not. The `brand_conflict` reason and its −25 stay visible, only the cap is lifted.

`brand_conflict`, `variant_conflict` and `image_only` together are `scoring.CAPPING_FLAGS`;
`brand_dirty_data` replaces `brand_conflict` in the flag set when the corroboration test passes.

`services/scoring.decide(score, flags)` applies `settings.LOOKUP_THRESHOLDS` (default
`{"match": 75, "review": 45}`, overridable per host):

```
not (flags & CAPPING_FLAGS) and (score >= match_threshold or identifier_exact)  -> match
score >= review_threshold or identifier_exact                                  -> review
otherwise                                                                       -> no_match
```

The clamped `sum(reason.score)` is `score`, reported (together with `decision`) only by `/check/`.
`/search/` reports something else — see the next section.

## Relevance (find) vs score (dedup)

The additive score answers *"is this the same product?"*. A `/search/` caller asks a different
question — *"how well does this hit match what I gave you?"* — and a photo-only query shows why the
two must not share a scale: the only evidence a picture can earn is L8 (≤ 10 points), so the identical
photo would read as 10/100 on the dedup scale while being a perfect answer to the question asked.

`scoring.relevance(query, candidate, image, reasons)` → `(0–100, MatchKind)`, carried as
`PairScore.relevance` / `PairScore.match` and reported by `/search/` as `similarity` / `match`.
Each evidence group is normalised to 0–100 on its own scale, and **the query's modality decides which
one counts** — the same rule every hybrid search engine applies (Meilisearch `semanticRatio`,
Weaviate / Typesense `alpha`; Elasticsearch fuses by rank for the same reason): heterogeneous scores
are never summed, because the larger scale wins regardless of what the other said.

| Group | 0–100 from |
|---|---|
| identifier | `gtin_exact` / `brand_mpn_exact` → 100 · `mpn_exact` / `sku_exact` → 80 · `gtin_exact_untrusted` → 60 (`RELEVANCE_IDENTIFIER`) |
| text | max of trigram mapped linearly over `[TRIGRAM_FLOOR, TRIGRAM_CEILING]` and `token_set_ratio` over `[TOKENS_WEAK, 1.0]` |
| image | pHash ≤ `PHASH_NEAR_EXACT` *and the cosine does not contradict it* (≥ `SAME_FILE_COSINE` 0.95, or no comparable vector) → 100 · ≤ `PHASH_NEAR` (a vetoed near-exact included) → 85 · else cosine mapped linearly over `[COSINE_SIMILAR, 1.0]` (0.99 → 95, 0.90 → 50); rows on the current `vec_model` only |

| Query | `relevance` |
|---|---|
| picture only | image |
| text only | max(identifier, text) |
| both | 100 when identifier is 100, else `FIND_IMAGE_WEIGHT` · image + (1 − `FIND_IMAGE_WEIGHT`) · max(identifier, text), with `FIND_IMAGE_WEIGHT = 0.5` |

`match` is `exact` when the identifier group is 100 or the picture is the same file (pHash ≤
`PHASH_NEAR_EXACT`, unvetoed — the same file re-saved or resized embeds at ~0.98-1.0, so a cosine
below `SAME_FILE_COSINE` marks the hash a DCT collision), `similar` when anything else agreed, `none` for a blocking neighbour nothing
agreed on. Conflicts (brand, variant) never lower relevance — they are dedup facts, and `/check/`
reports them through `decision`. `search` ranks by relevance (dedup score as tie-break); `check`
ranks by score, as before.

## What `DedupDecision` records

`DedupDecision` is an **append-only** audit and training log — one row per candidate a `check()` call
returned, plus (separately) one row per human verdict on a proposed pair.

| Field | Meaning |
|---|---|
| `query` | The `ParsedQuery` the caller searched with, as JSON |
| `subject_ref` | The catalog row the query stood for (empty for a free-text CMS search) — what makes a *pair* addressable |
| `candidate_kind` / `candidate_ref` | The fingerprint scored against the query |
| `score`, `features` | The clamped score and the exact `reasons` list the API returned |
| `decision_auto` | The machine verdict (`match` / `review` / `no_match`) |
| `decision_human` | Empty until a human answers (`accepted` / `rejected`) — `services/dedup_log.record` |
| `source` | Who asked: `api_check`, `create_hook`, `proposal`, or `lookup_eval` (`DecisionSource`) |

`subject_ref` + `candidate_ref` together are the *pair* a human verdict answers.
`services/dedup_log.rejected_pairs(subject_refs)` reads back every rejected pair for a page of
subjects in one query — the enrichment adapter's cooldown expires, but a human "no, these are not the
same product" answer must not.

## Normalisation rules (binding)

`normalize/` is pure — no DB, no settings — and never raises; invalid input comes back as a value.

- **GTIN**: digits only; length ∈ {8, 12, 13, 14}; GS1 check digit (`python-stdnum`); key = 14 digits
  zero-padded. Indicator 1–8 → `related_unit` (a multipack, never equal). Prefixes 2 / 20–29 / 02 / 04
  → `trusted=False`.
- **MPN**: uppercase, drop ` -/.`; strip leading zeros only if ≥ 4 chars remain; `loose` = before the last `-`.
- **Brand**: fold, drop legal forms, alias table (`hewlett packard` → `hp`).
- **Name**: fold + unaccent, units (`1,5 l` → `1.5l`), stopwords pl/en/de, brand strip (given, or leading
  tokens in the dictionary), pack (`2x`, `3-pack`, `zestaw 2`, `4 szt`), colour dictionary → english,
  size (`xl`, `eu 42`).

