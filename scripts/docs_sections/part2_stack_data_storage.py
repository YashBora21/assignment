# scripts/docs_sections/part2_stack_data_storage.py

SECTION_3 = """
## 3. Technology Stack: Decisions, Alternatives & Trade-offs

Every technology choice in this repository was made to balance offline self-containment, evaluation speed, and mathematical verification.

| Technology | Role in Codebase | Why Chosen | Main Alternatives | Why Not The Alternatives | Engineering Trade-off |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Python 3.13** | Backend & Core ML Pipeline | Rich ecosystem for PDF parsing, text extraction, vector math, and ML libraries. | Go, Rust, Node.js | Go and Rust lack mature, battle-tested PDF extraction (PyMuPDF) and sentence transformer bindings. | Higher memory footprint and slower CPU raw loop execution than compiled languages. |
| **FastAPI** | REST API Server (`api/main.py`) | Async/sync endpoint flexibility, automatic OpenAPI schema generation, Pydantic type validation. | Flask, Django, Express | Flask lacks native async and Pydantic validation; Django is too bloated for a specialized AI pipeline. | Background queue management requires explicit threading/concurrency coordination. |
| **Qdrant Vector DB** | Catalogue Vector Search (`retrieval.py`) | Native RocksDB storage, HNSW indexing, rich metadata payload filtering, Docker server support. | ChromaDB, pgvector, FAISS | ChromaDB local mode has severe SQLite concurrency lockouts; FAISS lacks metadata filtering; pgvector requires heavy Postgres setup. | Running Qdrant as a separate Docker service adds a container dependency to deployment. |
| **BAAI/bge-m3** | Embedding Model (`model_runtime.py`) | 1024-dim dense vectors, state-of-the-art multi-lingual retrieval, handles technical engineering vocabulary. | all-MiniLM-L6-v2, OpenAI text-embedding-3-small | MiniLM (384d) performs poorly on complex industrial codes; OpenAI requires cloud calls and recurring cost. | Heavy local model (2.27 GB disk/RAM); CPU embedding takes ~20ms per query without GPU. |
| **Groq (OpenAI API)** | Fast LLM Inference (`extraction.py`, `matching.py`) | Ultra-low latency LPUs (~300 tokens/sec), structured JSON mode, OpenAI SDK compatibility. | Local Llama 3 (llama-cpp), Anthropic Claude, OpenAI | Local GGUF models on CPU are 20x too slow for real-time tender evaluation; Claude/OpenAI are 5-10x higher latency. | Requires internet connectivity and an active `GROQ_API_KEY`. |
| **Rank-BM25** | Sparse Lexical Retrieval (`matching.py`) | Exact part number, code, and model keyword retrieval (e.g. `HT-00211`, `SS316`, `PN16`). | Elasticsearch, Meilisearch | External search servers are massive overkill for a 36k-chunk catalogue corpus; Rank-BM25 runs purely in-memory. | In-memory BM25 index must be built or cached per category in RAM. |
| **PyMuPDF (fitz)** | Primary PDF Parsing (`ingestion.py`, `tender_bundle.py`) | Fastest C-level PDF parsing engine, robust hyperlink and page annotation extraction. | PyPDF2, pypdf, PDFMiner | PyPDF2 is slow and frequently fails on complex multi-column government PDF text streams. | Does not do OCR internally; requires external OCR routing for scanned pages. |
| **pdfplumber** | Table Detection (`table_extraction.py`) | Precise visual table cell bounding-box extraction, column alignment, merged cell detection. | Camelot, Tabula | Tabula requires a Java JRE runtime; Camelot requires Ghostscript and OpenCV C-libraries. | High CPU latency on complex pages with 50+ table rows. |
| **PaddleOCR** | Optical Character Recognition (`ingestion.py`) | Superior text line detection and recognition for low-resolution, photocopied government scans. | Tesseract OCR, EasyOCR | Tesseract has high error rates on faded text and segfaults on Windows under certain diacritics; EasyOCR is slow on CPU. | Large model weights (`PP-OCRv6`); required oneDNN/PIR fallback configuration on Windows CPU. |
| **React 18 + Vite** | Frontend Dashboard & Matrix (`frontend/src`) | Lightning-fast HMR dev build, component reusability, seamless state management for multi-page tables. | Next.js, Vue, Plain HTML/JS | Next.js adds unnecessary SSR complexity for an internal desktop/enterprise workflow tool. | Single-page application bundle must be served via dev server or static build. |
| **Docker & Compose** | Container Orchestration (`docker-compose.yml`) | One-command reproducibility across evaluators' machines; isolates Qdrant and dependencies. | Kubernetes, Bare Metal VM | Kubernetes is vastly overengineered for single-server or laptop evaluation. | Evaluator must have Docker Desktop installed to run the pre-configured server stack. |
"""

SECTION_4 = """
## 4. Data Layout and Storage Architecture

### 4.1 Catalogue Corpus Layout (`data/manufacturers/`)
Manufacturer catalogues are stored in a strict, file-driven hierarchy that separates raw documents from machine-readable manifests:

```
data/manufacturers/
├── abb/
│   ├── sources.json              <-- Authoritative manifest
│   └── 3-phase-motors-low-voltage-cat.pdf
├── avk/
│   ├── sources.json
│   ├── 756-100-001-gate-valves.pdf
│   └── 756-102-001-butterfly-valves.pdf
├── duncan-engineering/
│   ├── sources.json
│   ├── hydraulic-cylinder-double-acting-servomotor.pdf
│   └── solenoid-valves-catalogue.pdf
├── ksb/
│   ├── sources.json
│   └── meganorm-centrifugal-pumps.pdf
└── ... (17 manufacturers total)
```

#### Manifest Schema (`sources.json`)
Every manufacturer folder contains a `sources.json`. The codebase **never hardcodes manufacturer names or product lines in Python code**; everything is driven by these manifests:

```json
{
  "display_name": "Duncan Engineering",
  "category": "valves",
  "categories": ["valves"],
  "documents": [
    {
      "file": "hydraulic-cylinder-double-acting-servomotor.pdf",
      "category": "valves",
      "display_name": "Hydraulic Cylinder & MIV Servomotor Series"
    }
  ]
}
```

### 4.2 Qdrant Vector Collections
The vector database organizes embeddings into distinct collections partitioned by product family:

* **`cat_motors`**: 11,534 points (after deduplication)
* **`cat_pumps`**: 12,441 points (after deduplication)
* **`cat_valves`**: 12,932 points (after deduplication)
* **Total Points**: 36,907 vectors (1024 dimensions, cosine distance metric)

#### Point Payload Schema
Each point in Qdrant represents a semantic passage or structured table row:

```json
{
  "id": 1042,
  "vector": [0.0142, -0.0521, 0.0891, "...", 1024 dims],
  "payload": {
    "manufacturer": "duncan-engineering",
    "category": "valves",
    "subtype": "servomotor",
    "document": "hydraulic-cylinder-double-acting-servomotor.pdf",
    "page": 14,
    "table_id": "table_002",
    "parent_id": "duncan:servomotor:p14",
    "text": "Model DA-150: Bore Diameter 150 mm, Stroke 450 mm, Working Pressure 25 bar, Test Pressure 37.5 bar, High Tensile Steel Piston Rod."
  }
}
```

#### Native Payload Indexes
To guarantee sub-second filtered search, native RocksDB indexes are maintained on:
- `manufacturer` (`keyword`)
- `category` (`keyword`)
- `subtype` (`keyword`)
- `table_id` (`keyword`)
- `page` (`integer`)

### 4.3 Saved Job & Output Schemas (`outputs/`)
Tender processing produces standardized JSON artifacts persisted on disk:

1. **Extraction Artifact (`outputs/<job_id>.json`)**:
   - `procurement_items`: Authoritative BOQ items with IDs and decimal quantities.
   - `technical_specifications`: Raw specifications extracted from attachments.
   - `canonical_requirements`: Reconciled, de-conflicted requirement objects.
   - `accuracy_score`: Grounding verification percentage.
   - `tender_status`: Evaluated pipeline stage status.
   - `reconciliation`: Summary counts (`consistent`, `conflict`, `refines`, `additional`, `unmapped`).

2. **Match Report Artifact (`outputs/<job_id>_match.json`)**:
   - `tender_id`: Hash of the processed tender.
   - `requirements`: Array of all evaluated canonical requirement objects.
   - `manufacturers`: Array of `ManufacturerResult` objects ranked by compatibility and coverage.
   - `evidence_coverage`: Summary of requirements supported by verified catalogue citations.
"""
