# scripts/docs_sections/part1_overview_and_flow.py

SECTION_1 = """# Tender Matching: Comprehensive Codebase & Technical Defense Guide

> **Project Mission**: Automated Government e-Marketplace (GeM) tender processing, multi-document attachment reconciliation, and evidence-grounded manufacturer catalogue matching across industrial categories (Motors, Pumps, Valves).

---

## 1. One-Page Overview

### 1.1 The Problem in Three Sentences
Public procurement tenders on portals like the Indian Government e-Marketplace (GeM) are published as fragmented PDF bundles where commercial terms, bills of quantities (BOQ), and technical requirements reside in separate, often contradictory files or scanned attachments. Evaluating vendor compliance currently requires domain engineers to manually pore over hundreds of pages of equipment catalogues to cross-reference operating parameters, pressure classes, and dimensions against vague or conflicting tender clauses. This manual review is slow, prone to human error, and lacks an auditable chain of evidence proving why a vendor was accepted or disqualified.

### 1.2 The 30-Second Elevator Pitch
**Tender Matching (TenderExtractPro)** is a production-ready, evidence-grounded AI system that transforms raw GeM tender PDF bundles into an actionable, auditable compliance matrix in seconds. It automatically discovers and downloads referenced GeM attachments, extracts authoritative BOQ line items and technical specifications, reconciles cross-document conflicts into a canonical requirement set, and retrieves matching equipment from indexed manufacturer catalogues using hybrid dense-vector and BM25 search in Qdrant. Crucially, the system enforces a strict zero-hallucination policy: an LLM is only permitted to extract candidate numbers and verbatim quotes, while deterministic Python code verifies that quotes exist character-for-character in the datasheet and evaluates compliance rules.

### 1.3 The 2-Minute Technical Explanation
When a user uploads a tender (such as a 5-page GeM summary document), the pipeline does not treat it as a single static text. First, `tender_bundle.py:discover_attachments()` parses the PDF structure and regex-identifies official GeM attachment hyperlinks (specifications, BOQs, and Additional Terms and Conditions), downloading and caching them securely. Second, `tender_bundle.py:load_bundle_sources()` routes documents by role: BOQ tables are parsed via `pdfplumber` to establish authoritative item IDs and quantities; technical specifications are ingested with PyMuPDF (or PaddleOCR fallback) and parsed into structured attribute tables. 

Third, `reconcile.py:reconcile_result()` analyzes the raw specifications against BOQ items, categorizing relationships as `consistent`, `conflict`, `refines`, `additional`, or `unmapped`, producing an unambiguous list of canonical requirements. Fourth, `categories.py:classify_tender()` scans tender descriptions against a dynamic vocabulary extracted from manufacturer manifests (`data/manufacturers/*/sources.json`) to scope the tender to its proper category (e.g., `valves`, `motors`, or `pumps`). 

Fifth, when matching is triggered, `matching.py:generate_match_report()` queries Qdrant collections (`cat_valves`, `cat_motors`, `cat_pumps`) using `BAAI/bge-m3` dense embeddings fused with BM25 keyword rankings via Reciprocal Rank Fusion (RRF). Each requirement is evaluated against retrieved catalogue chunks using a hierarchical decision engine: deterministic unit conversions and numerical ranges run first; verbatim substring checks run second; and an LLM judge (`_llm_extract` via Groq) is consulted only for descriptive parameters. The LLM's cited quote is then string-verified against the source chunk text. Finally, each manufacturer receives a two-dimensional score: **Compatibility** (percentage of verified requirements passed) and **Coverage** (percentage of tender requirements addressed in the catalogue). The result is streamed to a responsive React/Vite web interface with interactive evidence inspection.

### 1.4 System Architecture Diagram

```
+--------------------------------------------------------------------------------------------------+
|                                    TENDEREXTRACTPRO ARCHITECTURE                                  |
+--------------------------------------------------------------------------------------------------+

   [ User / Web UI ] <--- HTTP/REST ---> [ FastAPI Backend (api/main.py) ]
                                                      |
                   +----------------------------------+----------------------------------+
                   |                                                                     |
         [ Ingestion & Bundle Pipeline ]                                [ Matching Engine Pipeline ]
                   |                                                                     |
         1. discover_attachments()                                      6. classify_tender()
            (PDF Link & URL Detection)                                     (Manifest Vocabulary)
                   |                                                                     |
         2. load_bundle_sources()                                       7. HybridRetriever
            - PyMuPDF (fitz) text                                          - Dense: BAAI/bge-m3 (1024d)
            - pdfplumber table extraction                                  - Sparse: BM25Okapi
            - PaddleOCR (CPU fallback)                                     - Reciprocal Rank Fusion
                   |                                                                     |
         3. extract_specifications()                                    8. Hierarchical Decision Engine
            - LLM Parameter Extraction (Groq)                              - 1st: _compare_numeric()
            - Source Grounding Check (Rapidfuzz)                           - 2nd: _find_verbatim_value()
                   |                                                                     |
         4. read_wrapper_items() & read_boq()                           9. LLM Quote Extraction (Judge)
            - Classify item types (spares, main)                           - extract capability & quote
            - BOQ Quantity Reconciliation                                  - _verify_quotes() verbatim check
                   |                                                                     |
         5. reconcile_result()                                         10. Scoring & Ranking
            - Canonical requirement generation                             - Compatibility: passed / decided
            - Conflict & refines detection                                 - Coverage: decided / scorable
                   |                                                                     |
                   v                                                                     v
          [ outputs/<id>.json ]                                              [ outputs/<id>_match.json ]
                   ^                                                                     ^
                   +-----------------------------+---------------------------------------+
                                                 |
                                     [ Qdrant Vector Server ]
                                      - cat_motors (11,534 pts)
                                      - cat_pumps  (12,441 pts)
                                      - cat_valves (12,932 pts)
```
"""

SECTION_2 = """
## 2. End-to-End Flow: The Journey of One Tender

This walkthrough traces the exact lifecycle of tender **`GEM/2026/B/7997788`** (`GEM_9848272_Valve.pdf`), an actual valve tender processed in this project.

### Step 1: Upload and Queueing
* **Responsible**: `api/main.py:upload_file()` -> `_resolve_upload_path()` -> `job_queue.put()`
* **Execution**: The user drops `GEM_9848272_Valve.pdf` (163 KB) onto the Dashboard. FastAPI streams the file to `uploads/{job_id}.pdf`, calculates file size and extension validations (`.pdf`, `.docx`, `.png`), initializes an in-memory job state record with status `queued`, and puts `(job_id, path)` into a background FIFO `queue.Queue`.
* **Sample Data**:
  ```json
  {"job_id": "eb13265a3fe9", "filename": "GEM_9848272_Valve.pdf", "status": "queued", "progress": 0}
  ```

### Step 2: Attachment Discovery & Bundle Construction
* **Responsible**: `tender_extraction/tender_bundle.py:discover_attachments()`
* **Execution**: Worker thread picks up the job. It opens `uploads/eb13265a3fe9.pdf` with `pymupdf` (fitz). It searches page annotations (`page.get_links()`) and runs regex scanning for GeM URL patterns (`https://mkp.gem.gov.in/...` and `https://bidplus.gem.gov.in/...`). It discovers links categorized by role: `specification`, `boq`, and `atc` (Additional Terms & Conditions).
* **Sample Output**: Discovered 4 attachments, including `spec_1617_aft_17.pdf` and `spec_1617_aft_47.pdf`.

### Step 3: Secure Attachment Download
* **Responsible**: `tender_extraction/tender_bundle.py:download_attachment()`
* **Execution**: Validates each URL against an allowlist of official GeM hostnames (`mkp.gem.gov.in`, `bidplus.gem.gov.in`), rejects internal IP ranges (SSRF protection), and downloads files to `outputs/eb13265a3fe9_attachments/` using a browser user-agent header.

### Step 4: Multi-Source Ingestion & Table Extraction
* **Responsible**: `tender_extraction/tender_bundle.py:load_bundle_sources()` -> `tender_extraction/ingestion.py:ingest_document()` and `table_extraction.py:extract_tables()`
* **Execution**:
  - The wrapper PDF pages are parsed for high-level bid schedules.
  - Specification attachments are parsed with `pymupdf` to extract text blocks.
  - `table_extraction.py:extract_tables()` identifies specification tables, strips header artifacts, flattens merged column spans, and maps column names (`Parameter`, `Value`, `Unit`).
  - If a page has $< 100$ characters of text, `ingestion.py:_ocr_with_paddle()` triggers OCR on CPU.

### Step 5: Item Classification & BOQ Reading
* **Responsible**: `tender_extraction/tender_bundle.py:classify_item_type()` and `read_wrapper_items()`
* **Execution**: Each line item is classified into: `main_product`, `spares`, `tools`, `consumables`, or `review`.
  - Description: *"MAIN INLET VALVE DOUBLE ACTING SERVOMOTOR (HYDRAULIC CYLINDER)"* -> Classified as `main_product`.
  - Description: *"Tools as per clause 1.6"* -> Classified as `tools`.
  - Item quantities are extracted with decimal precision (`Decimal("1")`, `Decimal("6")`).

### Step 6: Requirement Extraction & Hallucination Guard
* **Responsible**: `tender_extraction/extraction.py:extract_specifications()` -> `tender_extraction/validation.py:validate_extractions()`
* **Execution**: Specification chunks are batched and sent to Groq (`openai/gpt-oss-120b`). The LLM returns structured JSON `{component, parameter, value, unit, source}`.
* **Grounding Check**: `validation.py:verify_grounding()` verifies that the cited `exact_text` exists in the referenced chunk with RapidFuzz ratio $\ge 80\%$. Ungrounded claims are discarded or flagged.

### Step 7: Cross-Document Reconciliation
* **Responsible**: `tender_extraction/reconcile.py:reconcile_result()`
* **Execution**: Compares requirements extracted from specifications against the BOQ items.
  - If a spec parameter matches the BOQ item exactly -> Status: `consistent`.
  - If a spec narrows down a general BOQ description -> Status: `refines`.
  - If specification provides a detail not in BOQ -> Status: `additional`.
  - If specification conflicts with BOQ -> Status: `conflict`.
  - Produces `canonical_requirements` (110 requirements for this valve tender).

### Step 8: Dynamic Category Scoping
* **Responsible**: `tender_extraction/categories.py:classify_tender()` -> `matching.py:_resolve_categories()`
* **Execution**: Scans the tender text against dynamic vocabularies extracted from `data/manufacturers/*/sources.json`. Detects keyword frequencies: `valve`, `servomotor`, `hydraulic cylinder` -> Scoped to primary category: **`valves`**.

### Step 9: Hybrid Catalogue Retrieval in Qdrant
* **Responsible**: `tender_extraction/matching.py:retrieve()` -> `tender_extraction/retrieval.py:HybridRetriever`
* **Execution**: For each requirement and manufacturer:
  - Query: *"operating pressure 16 bar Main Inlet Valve"*
  - Dense: Encoded into 1024-dim vector using `BAAI/bge-m3` and queried against Qdrant collection `cat_valves` with filter `manufacturer = "duncan-engineering"`.
  - Sparse: Tokenized and scored against in-memory `BM25Okapi` index over valve catalogue chunks.
  - Fusion: RRF fuses ranks: $RRF(d) = \sum \frac{1}{60 + \text{rank}}$. Top 6 chunks selected.

### Step 10: Hierarchical Decision & Quote Verification
* **Responsible**: `tender_extraction/matching.py:match_requirement()`
  1. `_compare_numeric()`: Requirement is min 16 bar. Chunk states *"Design Pressure: 25 bar"*. Condition `25 >= 16` passes -> **`full_match`**.
  2. If non-numeric: `_find_verbatim_value()` checks for literal string match (e.g. *"SS316"*).
  3. If text requires interpretation: `_llm_extract()` asks Groq for capability + quote. `_verify_quotes()` ensures quote appears verbatim in chunk text.

### Step 11: Score Aggregation & UI Presentation
* **Responsible**: `tender_extraction/matching.py:aggregate()` -> `frontend/src/components/MatchMatrix.jsx`
* **Execution**:
  - Scores computed: Compatibility = 100.0%, Coverage = 9.1%.
  - Saved to `outputs/eb13265a3fe9_match.json`.
  - Frontend renders ranked cards, compliance badges, and evidence drawer with verbatim page references.
"""
