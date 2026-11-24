# Phase 0 Audit — TenderExtractPro

Read-only audit of `tender_extraction/`, `api/`, `frontend/src/`, `tests/` and
`scripts/`. Line references are to the working tree at the start of this pass
(the tree already contained an unfinished, uncommitted attempt at Phases 1–4;
this audit records the real state, not the intended state).

---

## 1. Tender-level flags and where they gate matching

| Flag / string | File:line | Trigger | Blocks? |
|---|---|---|---|
| `tender_complete` set | `tender_extraction/tender_bundle.py:926` | `not bundle["missing"]`; missing entry for any unresolved attachment/item association | — |
| `tender_complete` read | `tender_extraction/reconcile.py:929` | adds "Tender sources are incomplete" blocker (only when no specs) | Yes in old code; now only when `not specs` |
| `tender_complete` read | `tender_extraction/reconcile.py:987` | sets reconciliation status `incomplete` | Yes (status only) |
| `tender_complete` read | `tender_extraction/matching.py` (old) | raised before matching | **Removed in WIP** |
| `tender_complete` read | `api/matching_adapter.py:71` (old) | returned "Incomplete tender" | **Removed in WIP** |
| `matching_blocked` set | `tender_bundle.py:945` (old), `main.py:308` (old) | `reconciliation["blocked"]` | — |
| `matching_blocked` read | `matching.py:746/750` (old) | raised if critical blockers | **Removed in WIP** |
| `matching_blocked` read | `api/main.py:172/463` (old) | nulled match data | **Removed in WIP** |
| `_tender_incomplete()` | `api/main.py:221` | now a stub returning `False` | No |
| `_matching_blocked()` | `api/main.py:225` | blocks only if result file missing/unreadable or **zero** requirements | Correct hard stop |
| `pipeline_version` stale | `api/main.py:169/533` | marks `stale` in API payloads; no longer blocks matching | No |
| `reference_unresolved` | `tender_bundle.py:870`, `reconcile.py:840` | missing standard in a spares/tools/consumables item | No (`blocking=False`) |
| `needs_review` (item) | `tender_bundle.py:874` | `classify_item_type()=="review"` | No; item simply yields no requirements |
| `needs_review` (match) | `matching.py:426/500/531` | heuristic / unvalidated quote | No (review flag) |
| `"Tender incomplete"` | `api/main.py` (old), `frontend/src/components/ResultViewer.jsx:221` (old) | stale/incomplete | Banner removed in UI WIP; `ResultViewer.jsx` still computes `matchingBlocked` from `result.tender_complete`/`matching_blocked` and passes it to `ReconciliationPanel` |
| `"Requirement needs BOQ association review"` | `tender_bundle.py:902` | unresolved/ambiguous spec | No; now a note |
| `"No product requirement recovered"` | `tender_bundle.py:915`, `reconcile.py:866` | item has no linked spec | No; now a note |
| `tenderComplete` prop | `frontend/src/components/MatchMatrix.jsx:110/126/155/158/244` | caller never passes it (defaults `true`) | Latent dead gate; must be removed |
| `matchingBlocked`/`tenderComplete` badges | `frontend/src/components/ReconciliationPanel.jsx:55/87-91` | derived flags | UI-only; must be removed |

**Conclusion:** the hard gate has been reduced to the intended one (zero
requirements), but the *schema, API payload and UI still carry the obsolete
fields*, and the UI still has a latent blocking branch. Contradicts plan
Phase 1 → migrate by deleting the fields from `ExtractionResult` and all
readers, keeping `.get()` tolerance for old files.

---

## 2. Scores / statuses assigned without evidence

| Where | Problem |
|---|---|
| `matching.py:449-452` `_llm_judge` prompt | Explicitly instructs the model to return `partial_match` when the catalogue is silent. Silence must be `no_evidence`. |
| `matching.py:485-509` `_heuristic_judge` | Returns `partial_match` (score 50) on 0.75 keyword overlap with `capability="NOT_FOUND"`; no page verification. Runs whenever the LLM call fails. |
| `matching.py:512-538` `_validate_quotes` | Uses fuzzy `partial_ratio >= 60` (fabricated text passes) and does not demote `no_match`/other statuses; leaves `capability` from the LLM even when evidence is dropped. |
| `matching.py:625` | `verdict.setdefault("score", STATUS_WEIGHTS[...]*100)` — a status-derived score when no evidence path set one. |
| `matching.py:672` `aggregate()` | `compatibility = weight_sum / total` includes `no_evidence` in the denominator. Plan requires `score / decided` with `no_evidence` excluded, plus separate coverage. |
| `matching.py:659-662` | Inflates `missing_capability` by copying `no_evidence` into it "for backwards compatibility with tests". |
| `matching.py:818-856` | `tender_status = fully_matched` when every manufacturer ≥ 70 — a synthetic tender-level success label. |
| `matching.py:853` | If no manufacturer evaluated → `needs_review` (not an error). |
| `matching.py:264-301` | Category guessing by hardcoded keyword regexes; `"general"` requirements retrieve from a non-existent `cat_general` and silently become `no_evidence`. |
| `matching.py:851` | Dead branch references undefined `blocking_reasons` (`_partial_mode` is always `False`). |

---

## 3. Category determination

* Manufacturer category: `scripts/ingest_manufacturers.py:81` (`doc.category`
  → `manifest.category` → `"general"`). `matching.py:249-254` reads
  `manifest.category` with a per-document fallback. This is manifest-driven and
  correct in principle; `registry.json` is empty and unused.
* Requirement category: `matching.py:264-277` hardcoded `motors|pumps|valves`
  regexes (now with added synonyms), plus `_infer_tender_category`
  (`matching.py:280-301`) bag-of-words counts over filename/items/specs.
  **Violates "no keyword-based category guessing".**
* Item type: `tender_bundle.py:641-660` `classify_item_type` with hardcoded
  product keywords and spares/tools regexes; `"review"` items are silently
  excluded.
* Frontend: `MatchMatrix.jsx:17-41` hardcoded `CATEGORY_VENDORS` list and
  `MatchMatrix.jsx:273` hardcoded `['all','valves','motors','pumps']`. This is
  the direct cause of "same manufacturers on every tender". There is an
  unused `/manufacturers` API that returns manifest-derived data.

---

## 4. Slugs and indexed content

* Qdrant payload `manufacturer`, chunk-id prefix and `load_manufacturers`
  slug all use the **folder name** (`sub.name`), so they agree.
* `sources.json` `display_name` is only a label.
* Collections (from `qdrant_storage/_hybrid_manifest.json`):
  `cat_motors` 22,809 · `cat_pumps` 23,076 · `cat_valves` 25,311.
  Per manufacturer (prior measurement, unchanged corpus): motors — abb 17,173,
  bharat-bijlee 4,948, crompton 688; pumps — ebara 2,123, ksb 425, sulzer 228,
  xylem-godwin 553, xylem-goulds 19,747; valves — avk 422, bray 11,227,
  circor 96, delval-flow 3,455, duncan-engineering 788, emerson-fisher 6,653,
  flowserve 2,158, intervalve-poonawalla 406, valmatic 106.
* There is **no `cat_general` collection**. A requirement categorised
  `"general"` therefore has no chunks; `has_corpus` is still true (documents
  exist), so it is reported with all-zero scores **without** the
  "no indexed catalogue content" note. Every `"general"` path is a silent
  ingestion/classification failure.
* `_CORPUS_CACHE` (`matching.py:304`) is never invalidated; a filtered call and
  an unfiltered call use different keys, but edits to the corpus persist for
  the process lifetime.
* `collect(root, category_filter=...)` is now passed from `generate_match_report`
  via a `try/except TypeError` shim — fragile and hides signature errors.

---

## 5. Match storage / caching

* Extraction: `outputs/{job_id}.json`; match: `outputs/{job_id}_match.json`
  (`api/main.py:212-218`). Every job id is distinct, so different tenders do
  **not** share result files.
* Redis read cache key: `match:{job_id}:{file_mtime}` (`api/main.py:834`) —
  includes job id and report mtime. Correct.
* **No LLM answer cache** exists. Re-running the same match re-calls the model
  with non-deterministic output, so identical inputs can yield different
  reports. There is no per-tender LLM call cap.
* Why different tenders historically showed *identical manufacturers*: not the
  result store — the hardcoded `CATEGORY_VENDORS` UI list (3.3) bypassed the
  API's manifest-derived manufacturer list entirely.
* `score_tender_match` (Company Fit) is stored separately as `match_score` /
  `match_data`; it is a different concept from the manufacturer report.

---

## 6. Re-extraction flow

1. `Dashboard.jsx:101-110` renders Re-extract; `App.jsx:76-88` calls
   `POST /jobs/{id}/rerun`, sets a **global** `rerunning` flag, shows
   `rerunError` inline, and on success selects the new job.
2. API (`api/main.py:542-584`): validates confirmation (400), original status
   (409), duplicate queued/running rerun (409), source file (409), copies the
   upload + attachments, sets `original.superseded_by`, enqueues, returns 201.
3. `GET /jobs/{id}/status` returns `stage`/`processing`, but the stage is only
   `extracting` or `matching` (pct ≥ 95); `queued` only at creation.
4. `App.jsx` polls `GET /jobs` every 4 s, so the new job appears. But:
   * no per-job loading state (all Re-extract buttons disable together);
   * no toast; error only renders above the dashboard and disappears on
     navigation;
   * the old card shows "Superseded" text but is not linked to the new run;
   * no test covers success / in-progress rejection / server error in the UI.

---

## 7. Dead code, duplication, tests encoding old behaviour

* `_partial_mode`/`blocking_reasons` dead branch (`matching.py:849-851`).
* `matching.py` keeps legacy statuses `missing_capability` and
  `unsupported_requirement` in the public `MatchStatus` union, against the
  four-status contract.
* `frontend/src/components/JobCard.jsx` is no longer used by `App.jsx`
  (Dashboard has its own card) — duplicated card logic.
* `CostTokenWidget` is imported but not rendered in `ResultViewer`.
* `ReviewNotesPanel` classifies free-text notes by substring, and explicitly
  strips "incomplete"/"blocked" wording — cosmetic, not structural.
* `apply_unblock_all.py`, `check_conflict.py`, `debug_param.py`, root
  `test_*.py`, `scripts/test_new_association.py` — untracked scratch scripts
  from the previous attempt; not part of the product.
* Tests encoding old behaviour:
  * `tests/test_api.py:75` stale-result hard block; `:479` incomplete-tender
    hard block.
  * `tests/test_tender_rerun_regressions.py:98` asserts
    `matching_blocked and not tender_complete`.
  * `tests/test_generic_matching.py:124-125` asserts the obsolete flags.
  * `tests/test_matching.py:190-209` expects `partial_match` after all quotes
    are invalidated (current code returns `no_evidence`); `:370` expects the
    heuristic 50 % score.
  * `tests/test_reconcile.py:229-230` still constructs `matching_blocked`.

---

## Minimal-change proposal

1. Delete `tender_complete` / `matching_blocked` from `ExtractionResult`,
   reconciliation output, `_public_job`, `_tender_incomplete`, UI props; keep
   readers tolerant of old JSON. Add structured `review_notes` with explicit
   types instead of string sniffing.
2. Replace keyword category guessing with a manifest-derived category
   vocabulary (`categories.py`): category names + document titles/subtypes from
   `sources.json`, scored by name/parameter overlap; never hardcode product
   names or manufacturer lists. Item category is stored on the item and
   inherited by requirements; UI gets categories from `/manufacturers`.
3. Rewrite the judge contract: the LLM returns only `capability` + `quotes`;
   code verifies quotes verbatim against the cited page text and applies
   operators. Four statuses only (+ `not_applicable`). Cache by
   `(prompt_version, model, prompt)` and cap calls per tender.
4. Recompute `compatibility = score/decided` and `coverage = decided/scorable`;
   rank low coverage last; keep technical/compliance/eligibility separate.
5. Remove the hardcoded vendor UI; fetch `/manufacturers?category=`, send the
   selection to `POST /match`, surface coverage/rank and per-manufacturer state.
6. Harden rerun UX: per-job loading, visible toast/inline error, link old →
   new, add API + frontend tests.
7. Rewrite the old blocking tests and add generic synthetic regressions.
