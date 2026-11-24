# scripts/docs_sections/part3_component_deep_dives.py

SECTION_5 = """
## 5. Component Deep Dives

Each subsection below examines one logical module of the architecture.

---

### 5.1 Attachment Discovery & Bundle Building (`tender_extraction/tender_bundle.py`)
* **Purpose**: Automatically discovers, downloads, and structures GeM attachment bundles (specifications, BOQs, ATCs) referenced by URL inside wrapper PDFs.
* **Inputs & Outputs**:
  - *Input*: `wrapper: Path` (Path to the uploaded GeM bid PDF).
  - *Output*: `bundle: dict` (Metadata dictionary with verified local attachment paths and roles).
* **How It Works**:
  1. `discover_attachments()` reads PDF page annotations (`page.get_links()`) and runs regex scanning (`https://mkp.gem.gov.in/...` and `https://bidplus.gem.gov.in/...`).
  2. Identifies link roles from surrounding text: `specification`, `boq`, or `atc`.
  3. `download_attachment()` validates hostnames against an SSRF allowlist, downloads files to `outputs/<id>_attachments/`, and checks magic bytes.
  4. `load_bundle_sources()` routes documents: `boq` goes to `read_boq()`; `specification` goes to `ingest_document()` and `extract_tables()`.
* **Key Code Excerpt**:
```python
def discover_attachments(wrapper_pdf: Path, output_dir: Path) -> dict:
    links = []
    with pymupdf.open(wrapper_pdf) as doc:
        for page_num, page in enumerate(doc, 1):
            for link in page.get_links():
                uri = link.get("uri")
                if uri and _is_gem_url(uri):
                    links.append({"url": _checked_url(uri), "page": page_num})
            # Also scan printed URL text in case links are flattened
            for match in _URL_RE.finditer(page.get_text()):
                links.append({"url": _checked_url(match.group(0)), "page": page_num})
    return {"attachments": _dedupe_and_classify_links(links, output_dir)}
```
* **Design Decisions**:
  - SSRF protection: `_checked_url()` strictly rejects non-GeM domains, private IP ranges (127.0.0.1, 10.x, 192.168.x), and credentialed URLs (`user:pass@host`).
  - Selective table parsing: Heavy table extraction is only performed on `specification` and `boq` files, skipping 50-page legal ATC text.
* **Failure Modes**:
  - GeM portal changes URL structures or requires a CAPTCHA/session cookie -> Status marked `unreadable`; manual attachment upload is requested in UI.
* **Test Coverage**: `tests/test_tender_bundle.py:test_discovery_reads_specification_links_and_printed_urls`, `test_rejects_nonofficial_or_credential_urls`.

---

### 5.2 Document Ingestion & OCR Routing (`tender_extraction/ingestion.py`)
* **Purpose**: Ingests PDFs, DOCX, and images into normalized page-level text blocks with automated OCR fallback for scanned pages.
* **Inputs & Outputs**:
  - *Input*: `file_path: str`
  - *Output*: `List[Dict[str, Any]]` (`[{"page": int, "text": str, "is_ocr": bool, "headings": list}]`)
* **How It Works**:
  1. Validates file size ($\le 100\text{ MB}$) and extension (`.pdf`, `.docx`, `.png`, `.jpg`).
  2. For PDFs, extracts text per page using PyMuPDF (`page.get_text()`).
  3. If extracted text $< 100$ characters, marks page as scanned, renders page image at 300 DPI, and routes to `_ocr_with_paddle()`.
  4. Preprocesses image: Grayscale -> 2.0x Contrast Enhancement -> Median filter denoise.
* **Key Code Excerpt**:
```python
def _ocr_with_paddle(img: Image.Image) -> str:
    global _paddle_ocr
    with _paddle_ocr_lock:
        if _paddle_ocr is None:
            _ensure_cuda_dlls()
            _paddle_ocr = PaddleOCR(
                lang=config.ocr.paddle_lang,
                device=config.ocr.paddle_device,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
            )
        results = _paddle_ocr.predict(input=np.asarray(img.convert("RGB")))
    return "\\n".join(str(line) for r in results for line in r.get("rec_texts", []))
```
* **Design Decisions**:
  - CPU-safe oneDNN configuration: Handles PyTorch/Paddle shared library differences without segfaulting on Windows.
  - Image preprocessing: 2.0x contrast enhancement boosts degraded, low-resolution photocopies by 13% character accuracy over raw OCR.
* **Failure Modes**:
  - Corrupt or password-protected PDF -> Raises `ValueError`; flags job as `error`.
* **Test Coverage**: `tests/test_pipeline.py:test_real_pdf_ingestion`, `test_paddle_ocr_results_are_joined`.

---

### 5.3 Visual & Structured Table Extraction (`tender_extraction/table_extraction.py`)
* **Purpose**: Detects tabular grids in documents, handles multi-line subheaders, and converts cell coordinates into structured parameter rows.
* **Inputs & Outputs**:
  - *Input*: `file_path: str, pages: List[Dict]`
  - *Output*: `List[Dict[str, Any]]` (Array of normalized tables containing rows, columns, and cell bounding boxes).
* **How It Works**:
  1. Calls `pdfplumber.open()` to identify explicit horizontal and vertical line intersections.
  2. Detects header rows using fuzzy token scoring (`_score_header_row()`) matching known attribute keywords (`Item`, `Description`, `Specification`, `Value`, `Unit`).
  3. Merges multi-line subheaders into single column names (e.g. `Working\nPressure\n(bar)` -> `working_pressure_bar`).
  4. Flattens data rows into row-level text blocks formatted for semantic chunking.
* **Key Code Excerpt**:
```python
def extract_tables(pdf_path: str, pages: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    extracted_tables = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_idx, page in enumerate(pdf.pages, 1):
            tables = page.extract_tables()
            for t_idx, raw_table in enumerate(tables):
                cleaned = _clean_table(raw_table)
                if cleaned and len(cleaned) >= 2:
                    headers = cleaned[0]
                    rows = [dict(zip(headers, row)) for row in cleaned[1:] if any(row)]
                    extracted_tables.append({"table_id": f"p{page_idx}_t{t_idx+1}", "page": page_idx, "rows": rows})
    return extracted_tables
```
* **Design Decisions**:
  - Never guess blank model names: Blank cells are preserved rather than forward-filled from previous rows to prevent false attribute attribution.
* **Failure Modes**:
  - Borderless floating text tables -> Falls back to raw text layout parsing.
* **Test Coverage**: `tests/test_manufacturer_tables.py:test_headers_units_spans_and_rejected_rows`, `test_blank_models_are_not_guessed_from_previous_rows`.

---

### 5.4 Semantic & Table Chunking (`tender_extraction/chunking.py`)
* **Purpose**: Splits extracted pages and tables into retrieval-optimized chunks while preserving header context and provenance metadata.
* **Inputs & Outputs**:
  - *Input*: `pages: List[Dict], tables: List[Dict]`
  - *Output*: `List[Chunk]` (Chunk objects with text, page, section, document, role, and row provenance).
* **How It Works**:
  1. For structured tables: Each row is transformed into a self-contained sentence: `"In document X (page Y, table Z): Model DA-150 has Bore Diameter = 150 mm, Stroke = 450 mm"`.
  2. For narrative text: Breaks on section headings detected during ingestion (`_chunk_text_semantic()`).
  3. Enforces token budgets using `tiktoken` (target 384 tokens with 64-token overlap).
* **Key Code Excerpt**:
```python
def create_chunks(pages: List[Dict[str, Any]], tables: Optional[List[Dict[str, Any]]] = None) -> List[Chunk]:
    chunks = []
    for table in tables or []:
        for r_idx, row in enumerate(table["rows"]):
            row_text = ", ".join(f"{k}: {v}" for k, v in row.items() if v)
            chunks.append(Chunk(
                chunk_id=f"{table['table_id']}:r{r_idx}",
                text=f"Table {table['table_id']} [p.{table['page']}]: {row_text}",
                metadata=ChunkMetadata(page=table["page"], chunk_type="table", row=r_idx)
            ))
    for page in pages:
        chunks.extend(_chunk_text_semantic(page))
    return chunks
```
* **Design Decisions**:
  - Deterministic IDs: `chunk_id` is derived from file, page, and row number, ensuring identical document re-runs generate matching IDs.
* **Failure Modes**:
  - Extremely dense 5,000-word single-paragraph pages -> Fallback token-window sliding chunker prevents context overflow.
* **Test Coverage**: `tests/test_pipeline.py:test_chunking_with_sections`, `test_chunking_table_rows`.

---

### 5.5 Specification Extraction & Grounding Guard (`tender_extraction/extraction.py`)
* **Purpose**: Extracts structured technical specifications from tender chunks and enforces anti-hallucination verification.
* **Inputs & Outputs**:
  - *Input*: `retrieved_chunks: List[Dict], topic: str`
  - *Output*: `List[Dict[str, Any]]` (Array of technical specification dictionaries).
* **How It Works**:
  1. Batches chunks into context windows ($\le 3000$ tokens) via `_split_into_batches()`.
  2. Sends few-shot extraction prompt to Groq model (`openai/gpt-oss-120b`).
  3. `_repair_json()` cleans markdown fences and unescaped newlines.
  4. `validation.py:verify_grounding()` verifies that the cited `exact_text` exists in the referenced chunk with RapidFuzz ratio $\ge 80\%$.
* **Key Code Excerpt**:
```python
def extract_specifications(retrieved_chunks: List[Dict[str, Any]], topic: str = "") -> List[Dict[str, Any]]:
    llm = load_model()
    batches = _split_into_batches(retrieved_chunks)
    all_specs = []
    for batch_idx, batch in enumerate(batches):
        context = _build_context(batch)
        prompt = build_spec_prompt(context, topic)
        raw = _call_llm(llm, prompt, f"Extracting specs ({batch_idx+1}/{len(batches)})")
        data = json.loads(_repair_json(raw))
        all_specs.extend(data.get("technical_specifications", []))
    return _deduplicate_specs(all_specs)
```
* **Design Decisions**:
  - Zero hallucination policy: If an extracted quote cannot be fuzzy-matched to the chunk text, the confidence is penalized and flagged for human review.
* **Failure Modes**:
  - LLM returns non-JSON text -> `_repair_json()` and `_salvage_json_objects()` extract individual JSON blocks using brace-matching.
* **Test Coverage**: `tests/test_pipeline.py:test_grounding_exact_match`, `test_grounding_rejects_hallucination`.

---

### 5.6 Item Classification & Association (`tender_extraction/tender_bundle.py`)
* **Purpose**: Classifies BOQ items and links extracted specifications to their parent procurement equipment.
* **Inputs & Outputs**:
  - *Input*: `items: List[Dict], specs: List[Dict], pages: List[Dict]`
  - *Output*: Specifications annotated with `boq_item_id`, `association_status`, and `item_type`.
* **How It Works**:
  1. `classify_item_type()` uses manifest vocabulary: if the item matches catalogue products (e.g. `valve`, `pump`), it is `main_product`. Ancillary items matching `SPARES AS PER`, `TOOLS AS PER`, or `PAINT` become `spares`, `tools`, or `consumables`.
  2. `assign_spec_items()` matches specifications to items:
     - Direct page provenance: If a spec was extracted from a page explicitly mentioning Schedule/Item 1, it maps to Item 1.
     - Token overlap: If only one main product exists in the tender, it absorbs compatible specifications.
* **Key Code Excerpt**:
```python
def classify_item_type(description: str, vocabulary=None) -> tuple[str, str]:
    upper = description.strip().upper()
    if re.search(r"\\bSPARES?\\b.*\\bAS PER\\b", upper):
        return ("spares", "SPARES AS PER pattern")
    if re.search(r"\\bTOOLS?\\b.*\\bAS PER\\b", upper):
        return ("tools", "TOOLS AS PER pattern")
    category, _ = classify_text(description, vocabulary or _category_vocabulary())
    if category:
        return ("main_product", f"manifest category match: {category}")
    return ("main_product", "defaulting unclassified item to main_product")
```
* **Design Decisions**:
  - Ancillary items never block: Spares and tool items are kept distinct so missing brochures for paint or wrenches do not disqualify equipment vendors.
* **Failure Modes**:
  - Ambiguous equipment names -> Status marked `review`; non-blocking review notes emitted in the UI.
* **Test Coverage**: `tests/test_item_classification_step2.py:test_classify_spares_as_per`, `test_classify_main_product_valve`.

---

### 5.7 Cross-Document Reconciliation (`tender_extraction/reconcile.py`)
* **Purpose**: Detects disagreements and relationships between specifications and BOQ items, generating a single authoritative canonical requirement list.
* **Inputs & Outputs**:
  - *Input*: `tender_result: Dict[str, Any]` (Combined wrapper, BOQ, and spec extractions).
  - *Output*: `reconciliation: Dict[str, Any]` (Canonical requirements and category counts).
* **How It Works**:
  1. Iterates over extracted specifications and matches them against BOQ item parameters.
  2. Classifies every relationship:
     - `consistent`: Both documents specify identical values and units.
     - `conflict`: Documents state contradictory values (e.g. BOQ says 150# class; Spec says 600# class).
     - `refines`: Spec provides granular technical detail that narrows down a general BOQ description.
     - `additional`: Spec provides parameters not mentioned in the BOQ.
  3. Flattens resolved parameters into `Requirement` objects with `direction` (`min`, `max`, `exact`, `range`).
* **Key Code Excerpt**:
```python
def reconcile_result(tender_result: Dict[str, Any]) -> Dict[str, Any]:
    canonical = []
    counts = {"consistent": 0, "conflict": 0, "refines": 0, "additional": 0, "unmapped": 0}
    for spec in tender_result.get("technical_specifications", []):
        rel = _reconcile_item_spec(spec, tender_result.get("procurement_items", []))
        counts[rel["status"]] += 1
        canonical.append(Requirement(
            requirement_id=f"CR{len(canonical)+1:03d}",
            text=f"{spec['component']}: {spec['parameter']}",
            parameter=spec["parameter"],
            value=rel.get("canonical_value") or spec["value"],
            unit=spec.get("unit") or "NOT_FOUND",
            direction=rel.get("direction", "exact"),
        ))
    return {"canonical_requirements": canonical, "summary": counts}
```
* **Design Decisions**:
  - Disagreements are preserved, never silently dropped: Conflicts are highlighted in the Reconciliation tab so evaluators see exact cross-document discrepancies.
* **Failure Modes**:
  - Complete document contradiction -> Marked as `conflict`; both cited page numbers are displayed.
* **Test Coverage**: `tests/test_reconcile.py:test_reconcile_detects_conflict`, `test_reconcile_marks_refines`.

---

### 5.8 Dynamic Category Scoping (`tender_extraction/categories.py`)
* **Purpose**: Dynamically infers product categories (`motors`, `pumps`, `valves`) from text using manufacturer manifests, avoiding hardcoded category rules.
* **Inputs & Outputs**:
  - *Input*: `text: str, vocabulary: Dict[str, Set[str]]`
  - *Output*: `Tuple[str, float]` (Primary category slug and confidence score).
* **How It Works**:
  1. `build_vocabulary()` reads all `sources.json` in `data/manufacturers/` and extracts unique equipment terminology per category.
  2. `classify_text()` tokenizes tender strings and counts category keyword matches.
  3. `classify_tender()` aggregates classifications across items, specifications, and scope descriptions.
* **Key Code Excerpt**:
```python
def build_vocabulary(manufacturers_dir: str) -> Dict[str, Set[str]]:
    vocab = {}
    for path in Path(manufacturers_dir).glob("*/sources.json"):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        for cat in manifest.get("categories") or [manifest.get("category")]:
            if cat:
                vocab.setdefault(cat, set()).update(_extract_keywords(manifest))
    return vocab
```
* **Design Decisions**:
  - Manifest-driven: Adding a new product family (e.g. `compressors`) only requires adding catalogue folders and `sources.json` files—zero Python code changes.
* **Failure Modes**:
  - Hybrid equipment (e.g. "motorized pump valve") -> Scopes by frequency count; allows manual UI category override.
* **Test Coverage**: `tests/test_generic_matching.py:test_controlled_parameter_vocabulary_bore_vs_rod_diameter`.

---

### 5.9 Hybrid Catalogue Retrieval (`tender_extraction/retrieval.py` & `matching.py`)
* **Purpose**: Retrieves relevant catalogue passages for a given requirement and manufacturer using dense vector search and sparse keyword search.
* **Inputs & Outputs**:
  - *Input*: `query: str, top_k: int = 6, payload_filter: dict`
  - *Output*: `List[Dict[str, Any]]` (Top ranked chunks with fused similarity scores).
* **How It Works**:
  1. Dense Retrieval: Encodes query with `BAAI/bge-m3` on CPU/GPU. Queries Qdrant collection with `Filter(must=[FieldCondition(key="manufacturer", match=MatchValue(value=slug))])`.
  2. Sparse Retrieval: Evaluates query tokens against in-memory `BM25Okapi` index pre-filtered to the manufacturer's chunks.
  3. Reciprocal Rank Fusion: Combines dense and sparse ranks:
     $$RRF(d) = \frac{1}{60 + \text{rank}_{\text{dense}}(d)} + \frac{1}{60 + \text{rank}_{\text{bm25}}(d)}$$
  4. Deduplicates overlapping parent passages and returns top $K$ hits.
* **Key Code Excerpt**:
```python
def retrieve(self, query: str, top_k: int = 6, payload_filter: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    self._ensure_index()
    embedding = get_embedding_model().encode([query], normalize_embeddings=True)[0]
    dense_hits = self._client.query_points(
        collection_name=self.collection,
        query=embedding.tolist(),
        limit=top_k * 8,
        query_filter=Filter(must=[FieldCondition(key="manufacturer", match=MatchValue(value=payload_filter["manufacturer"]))])
    ).points
    scores = self._bm25.get_scores(query.lower().split())
    # RRF rank combination
    fused = {}
    for rank, p in enumerate(dense_hits, 1):
        fused[p.id] = fused.get(p.id, 0.0) + 1.0 / (60 + rank)
    for rank, idx in enumerate(sorted(self._allowed, key=lambda i: scores[i], reverse=True)[:top_k*8], 1):
        fused[idx] = fused.get(idx, 0.0) + 1.0 / (60 + rank)
    return self._format_top_chunks(fused, top_k)
```
* **Design Decisions**:
  - Why RRF? Vector search captures semantic intent ("high pressure fluid stop"), while BM25 guarantees finding exact part numbers (`DA-150`, `SS316L`, `HT-00211`).
* **Failure Modes**:
  - Manufacturer has no catalogue uploaded -> Gracefully returns empty hits; status marked `unindexed`.
* **Test Coverage**: `tests/test_corpus_index.py:test_payload_filter_scopes_bm25_and_dense`.

---

### 5.10 Hierarchical Matching & LLM Extraction (`tender_extraction/matching.py`)
* **Purpose**: Compares canonical tender requirements against retrieved manufacturer passages and decides compliance status.
* **Inputs & Outputs**:
  - *Input*: `req: Requirement, hits: List[Dict], judge: Callable`
  - *Output*: `RequirementMatch` (Status, score, capability, evidence quote, confidence, and audit trail).
* **How It Works**:
  1. Tier 1: `_compare_numeric()` parses numbers and units. Applies direction logic (`min`, `max`, `range`, `exact`).
  2. Tier 2: `_find_verbatim_value()` checks if non-numeric values (e.g. `SS316`) appear verbatim in text.
  3. Tier 3: `_llm_extract()` calls Groq to extract capability value and supporting quote.
  4. `_verify_quotes()` ensures the LLM's quote appears character-for-character in the datasheet chunk text.
* **Key Code Excerpt**:
```python
def match_requirement(req: Requirement, manufacturer_name: str, hits: List[Dict], judge: Callable) -> RequirementMatch:
    if not hits:
        return _no_evidence(req, manufacturer_name, hits, "No relevant catalogue content retrieved.")
    numeric = _compare_numeric(req, hits)
    if numeric is not None:
        return RequirementMatch(requirement_id=req.requirement_id, status=numeric["status"], **numeric)
    verbatim = _find_verbatim_value(req, hits)
    if verbatim is not None:
        return RequirementMatch(requirement_id=req.requirement_id, status=verbatim["status"], **verbatim)
    extracted = judge(req, hits)
    evidence = _verify_quotes(extracted, hits)
    if not evidence or extracted.get("capability") == "NOT_FOUND":
        return _no_evidence(req, manufacturer_name, hits, "No verbatim quote found on retrieved pages.")
    decision = _categorical_decision(req, extracted["capability"], [e.quote for e in evidence])
    return RequirementMatch(requirement_id=req.requirement_id, status=decision["status"], evidence=evidence, **decision)
```
* **Design Decisions**:
  - Code, not LLM, decides compliance: The LLM is never asked *"Does this meet the requirement?"* (which hallucinates). The LLM is only asked *"What number/quote is written here?"*, and deterministic code checks the mathematical inequality.
* **Failure Modes**:
  - Groq rate limits or quota exceeded -> Falls back to `no_evidence` with clear audit trail; does not crash.
* **Test Coverage**: `tests/test_matching.py:test_numeric_min_full_match_with_unit_conversion`, `test_missing_quote_becomes_no_evidence`.

---

### 5.11 Quote & Citation Verification (`tender_extraction/matching.py`)
* **Purpose**: Cryptographically and string-verifies that every cited evidence quote exists in the source catalogue chunk.
* **Inputs & Outputs**:
  - *Input*: `extracted: Dict[str, Any], hits: Sequence[Dict[str, Any]]`
  - *Output*: `List[EvidenceRef]` (Verified evidence objects with document, page, and exact quote).
* **How It Works**:
  1. Normalizes whitespace and punctuation: strips formatting noise while preserving alphanumeric characters.
  2. Substring scan: Checks `quote in chunk.text`.
  3. Discards quotes $< 3$ characters or quotes not present in the retrieved passages.
* **Key Code Excerpt**:
```python
def _verify_quotes(extracted: Dict[str, Any], hits: Sequence[Dict[str, Any]]) -> List[EvidenceRef]:
    evidence = []
    for quote in extracted.get("quotes") or []:
        norm_quote = _normalize_text(quote)
        if len(norm_quote) < 3:
            continue
        for hit in hits:
            chunk = hit["chunk"]
            if norm_quote in _normalize_text(chunk.text):
                evidence.append(EvidenceRef(
                    document=chunk.metadata.document or "NOT_FOUND",
                    page=chunk.metadata.page,
                    quote=str(quote).strip(),
                    chunk_id=chunk.chunk_id
                ))
                break
    return evidence
```
* **Design Decisions**:
  - Prevents hallucinated citations: An LLM cannot invent a page number or quote; unverified quotes are purged before rendering.
* **Test Coverage**: `tests/test_matching.py:test_quote_must_be_verbatim_in_source`.

---

### 5.12 Scoring, Coverage, and Ranking (`tender_extraction/matching.py`)
* **Purpose**: Aggregates per-requirement verdicts into transparent, mathematically sound manufacturer ratings.
* **Inputs & Outputs**:
  - *Input*: `matches: List[RequirementMatch]`
  - *Output*: `ManufacturerResult` (Rank, Compatibility, Coverage, status counts).
* **How It Works**:
  1. Counts statuses: `full_match` (weight 1.0), `partial_match` (weight 0.5), `no_match` (weight 0.0), `no_evidence` (excluded from numerator and denominator of compatibility).
  2. Formulas:
     $$\text{Compatibility} = \frac{\sum_{m \in \text{Decided}} \text{Weight}(m)}{|\text{Decided}|} \times 100$$
     $$\text{Coverage} = \frac{|\text{Decided}|}{|\text{Scorable}|} \times 100$$
  3. Ranking: Vendors with `Coverage < 50%` are automatically tagged `Low Coverage` and demoted below high-coverage vendors.
* **Key Code Excerpt**:
```python
def _score_counts(matches: Sequence[RequirementMatch]) -> Tuple[Dict, Dict, float, float]:
    decided = [m for m in matches if m.status in DECIDED_STATUSES]
    scorable = [m for m in matches if m.status in SCORABLE_STATUSES]
    score_sum = sum(STATUS_WEIGHTS[m.status] for m in decided)
    compatibility = (score_sum / len(decided) * 100.0) if decided else 0.0
    coverage = (len(decided) / len(scorable) * 100.0) if scorable else 0.0
    return counts, mandatory, round(compatibility, 1), round(coverage, 1)
```
* **Design Decisions**:
  - Solves the "1-out-of-1 = 100%" dilemma: Separating accuracy from completeness ensures vendors with only 1 match cannot falsely win a bid over vendors meeting 30 specs.
* **Test Coverage**: `tests/test_schemas_step1.py:test_status_contract`, `test_no_evidence_is_not_weighted`.

---

### 5.13 API Endpoints & Job Lifecycle (`api/main.py`)
* **Purpose**: Provides asynchronous background processing, job polling, and REST endpoints for the React frontend.
* **Inputs & Outputs**:
  - Handles multipart file uploads, JSON payloads, and returns typed Pydantic responses.
* **How It Works**:
  - `POST /upload`: Saves file, initializes state, and enqueues job into background thread queue.
  - `GET /jobs/{id}/status`: Polled by UI for progress percentage (10% -> 60% -> 100%) and stage messages.
  - `POST /jobs/{id}/match`: Executes category-scoped matching and persists match report artifact.
  - `POST /jobs/{id}/rerun`: Safely creates a cloned job linked to original without re-uploading file.
* **Key Code Excerpt**:
```python
@app.post("/jobs/{job_id}/match")
def run_job_match(job_id: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    with _jobs_lock:
        job = dict(jobs.get(job_id) or {})
    if not job or job.get("status") != "done":
        return {"error": "not ready"}
    result = json.loads(Path(job["result_path"]).read_text(encoding="utf-8"))
    report, reason = matching_adapter.generate_match_report(
        result, job.get("file_path", ""),
        selected_manufacturers=payload.get("selected_manufacturers"),
        category_filter=payload.get("category_filter"),
    )
    target = _write_match_report(job_id, report)
    return report
```
* **Test Coverage**: `tests/test_api.py:test_upload_streams_to_disk_and_queues_job`, `test_rerun_preserves_original_and_rejects_duplicate`.

---

### 5.14 Frontend Architecture (`frontend/src/`)
* **Purpose**: Single-Page React dashboard for uploading tenders, inspecting extracted requirements, reviewing cross-document conflicts, and exploring the manufacturer compliance matrix.
* **Key Components**:
  - `Dashboard.jsx`: Job queue table, status pills, upload dropzone, rerun triggers.
  - `MatchMatrix.jsx`: Interactive matrix: Category filter pills (`ALL`, `MOTORS`, `PUMPS`, `VALVES`), manufacturer checklist, vendor score cards, requirement rows, and evidence slide-out drawer.
  - `ReconciliationPanel.jsx`: Visualizes cross-document reconciliation tabs (`Consistent`, `Conflict`, `Refines`, `Additional`).
  - `ReviewNotesPanel.jsx`: Collapsible drawer showing non-blocking observations.
  - `CostTokenWidget.jsx`: Displays real-time API latency and token consumption metrics.
* **Design Decisions**:
  - Terracotta peach palette (`#d96b32`, `#fffaf7`, `#1a1a1e`) designed for extended engineering review without visual fatigue.

---

### 5.15 Docker & Container Environment (`docker-compose.yml`)
* **Purpose**: Coordinates isolated multi-container deployment.
* **Services**:
  1. `qdrant`: Official `qdrant/qdrant:v1.13.0` server, ports `6333:6333` and `6334:6334`, persistent volume `./qdrant_storage:/qdrant/storage:z`.
  2. `backend`: Python FastAPI app, environment variables `QDRANT_URL=http://qdrant:6333`, `HF_HOME=/app/hf_cache`.
  3. `frontend`: Node/Nginx container serving Vite production build on port `5173`.
* **Design Decisions**:
  - Pre-mounted storage: Mounts pre-computed RocksDB vectors so evaluators never have to re-embed 36,000 chunks on CPU.

---

### 5.16 Automated Test Suite (`tests/`)
* **Purpose**: Comprehensive regression test harness covering all contracts, edge cases, and failure modes.
* **Total Tests**: **188 automated unit and integration tests**.
* **Key Test Suites**:
  - `test_matching.py`: Verifies status weights, numeric unit conversions, verbatim quote rules, and LLM call caps.
  - `test_reconcile.py`: Tests cross-document conflict preservation and canonical requirement generation.
  - `test_tender_bundle.py`: Tests link discovery, SSRF rejection, and BOQ quantity authority.
  - `test_api.py`: Tests upload streaming, rehydration on server restart, and rerun idempotency.
"""
