# TenderExtractPro: implementation and validation draft

This draft records verified implementation results. It is not a completed
accuracy benchmark: real-tender extraction and matching results still need to
be measured against manually checked reference answers.

## Approach

The extraction pipeline reads PDF, DOCX, and image inputs, applies OCR when
page text is insufficient, extracts tables, creates section and table-row
chunks, retrieves evidence with BM25 and BGE-M3 embeddings, extracts structured
specifications, and checks their grounding. Outputs contain component names,
parameter/value pairs, source pages, source quotes, and confidence scores.

The manufacturer corpus stays manufacturer-first on disk. Each manufacturer's
`sources.json` records document provenance and category/subtype metadata.
Qdrant collections are category-scoped: `cat_motors`, `cat_pumps`, and
`cat_valves`. Requirement routing uses explicit component nouns. Unknown or
ambiguous components use `general` evidence, when available, with a maximum
score of 50 and a review flag. A valve requirement cannot borrow a motor
manufacturer's evidence merely because both mention the same number or material.

Within the chosen category, retrieval is filtered by manufacturer. Subtype
metadata is retained in point payloads; the current matching query does not
apply a subtype filter or boost.

## Structured manufacturer tables

Manufacturer ingestion reuses the existing PDF/DOCX/image table extractors.
PDF cell geometry is retained to identify merged cells. Confirmed vertical
spans are forward-filled, multi-row headers are flattened, and unit spellings
are standardized while original numeric values are preserved. Unknown units
are retained rather than guessed.

Each accepted product row becomes one chunk and one Qdrant point, with headers
repeated. Its payload includes manufacturer, category, subtype, document hash,
PDF page, table ID, model, and specifications. Missing/ambiguous model columns,
blank model rows, duplicate flattened headers, and inconsistent column counts
are rejected. Raw tables and rejection reasons remain available for review.

Prose/OCR page chunks are also indexed. Rejecting a structured row does not
remove that page's text from retrieval, so model-specific capability claims
still require evidence review.

## Evidence and scoring policy

Numeric matching compares supported units and requirement directions such as
minimum, maximum, exact value, and range before consulting an LLM. Non-numeric
requirements may use textual evidence or the LLM judge. Manufacturer evidence
includes document name, page, chunk ID, and quote. Fabricated judge quotes are
dropped, and missing evidence is flagged rather than treated as a full match.
Commercial/legal requirements cannot be certified from product datasheets.

Extraction grounding checks quoted text and specification values against
source chunks. The displayed grounding score is an internal evidence-match
measure, not measured precision, recall, completeness, or product correctness.

## Verified corpus results

| Check | Observed result |
|---|---:|
| Manufacturer manifests validated | 17 |
| Documents ingested | 81 |
| Detected motor table candidates | 835 |
| Detected pump table candidates | 1,895 |
| Detected valve table candidates | 1,237 |
| Accepted structured product rows | 887 |
| Exported review samples | 75: 25 per category |

Table candidates include rejected and non-product detections. These counts
are not counts of accurate product tables. Review sampling uses reservoir
sampling with seed 42 across detected tables, including failures, rather than
sampling only successful rows. All 75 sample PDF page references were checked
against the corresponding document's page count.

Review artifacts:

- [Motors](outputs/table_review/motors.html)
- [Pumps](outputs/table_review/pumps.html)
- [Valves](outputs/table_review/valves.html)

Each HTML report has a matching JSON file containing raw cells, normalized
rows, product fields, and rejection reasons.

## Validation and remaining evidence

- Combined targeted matching, attachment, structured-table, index, and API
  regression suite: 74 passed. Checks include
  a generated PDF with merged model cells, unit subheaders, category isolation,
  a manufacturer serving separate pump and valve requirements,
  payload checks, and sample-size checks. Embedding models are faked in these tests.
- API tests ran outside the Windows sandbox. The sandbox's
  socket-pair initialization prevented TestClient from starting correctly.
- The API tests include an HTTP-to-real-engine integration check with fake
  retrieval and an assertion that numeric matching does not invoke the LLM.
- Frontend production build passed. Lint has no errors and one existing chat
  history effect dependency warning.
- Browser automation could not initialize in this session; the new UI was
  verified by build/lint and source inspection, not a browser visual check.
- Docker Compose configuration validates, and the Docker Linux daemon is
  reachable. Images have not been built, smoke-tested, or published in this work.
- PaddleOCR used the RTX 3050 with CUDA. BGE-M3 weights were downloaded to the
  D: cache and their SHA-256 verified. Motors (22,809 points) and pumps (23,076
  points) are confirmed saved; the valve collection is still encoding.

## Hard cases and limits

Scanned pages need the optional table-vision extractor for structured cells;
PaddleOCR text alone does not restore a reliable table grid. Broken columns,
unrecognized model headers, model names outside the table, and ambiguous
component categories can cause rejection or review flags. Category inference
currently recognizes pump, valve, and motor nouns; it is deliberately
conservative about ambiguous combinations.

The current Docker backend is CPU-only, even though local ingestion uses CUDA.
The local Qdrant client warned about collections above 20,000 points. Neither
GPU throughput nor end-to-end latency has been benchmarked. Encoding currently
lacks batch progress logging, so activity readings cannot establish percentage
completion or a reliable ETA. The matching collector also reprocesses source
documents before reusing vectors with matching chunk fingerprints.

## Benchmark still required

The dataset directory contains five PDFs. Two evaluation documents and their
reference requirements must be fixed before reporting accuracy. For each,
record extracted requirements, correctly recovered requirements, missed
requirements, incorrect values/units, valid citations, manufacturer match
statuses, review flags, and runtime. Calculate precision and recall only from
those checked counts. Report failures and uncertain cases alongside successes.

No aggregate accuracy benchmark is claimed here. Final deployment evidence and
the final write-up depend on the planned benchmark and the completed index.

## Additional user-provided GeM case

GeM bid GEM/2026/B/8220268 was processed after the user authorized sending its
text to Groq. The 17-page PDF was ingested locally. An initial CPU RAG attempt
was stopped during vector encoding because it was too slow alongside the GPU
manufacturer index. A full-text route then used the existing Groq extraction
and grounding functions without semantic/vector retrieval. That extraction and
validation step took 11.8 seconds, excluding ingestion and the stopped attempt.

The automated output retained one specification and 20 deliverables with a
72.54 grounding score, but failed manual quality checks: quantities were shifted
onto following item headings, a tank line was omitted, and unsupported civil-work
exclusions survived grounding. The retained specification was a local-content
eligibility condition rather than a product operating rating. This is evidence
that passing unit tests and source-text grounding do not establish correct
item/value associations.

The source-checked quantities are 18, 5, 14, 3, and 160, totaling 200. Detailed
specifications and the BOQ are linked attachments and were not processed.
Manufacturer compatibility was not evaluated. The unchanged automated output
and the separate manual corrections are retained under
`outputs/GeM-Bidding-10103860/`, with a detailed `quality_review.md`.

The system now resolves GeM specification/BOQ attachments and supports locally
provided copies when automated downloads fail. BOQ quantities come from CSV rows,
not wrapper table-heading inference. Source filenames/pages/rows and BOQ item
associations are retained. Incomplete bundles are prevented from generating
manufacturer or company-fit scores or product Q&A answers.

The real attachment-aware rerun is incomplete: both official URLs returned HTML
"Request Rejected" responses instead of files. Its `bundle_result.json` records
the missing files and suppresses matching. The real specification/BOQ contents
have not been evaluated; the complete-file path is covered by isolated fixtures.
