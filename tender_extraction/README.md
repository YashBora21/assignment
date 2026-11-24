# Tender Extraction Module (`tender_extraction/`)

Physical folder architecture and module reference for the **TenderExtractPro** extraction and manufacturer matching engine.

---

## 🏛️ Directory Structure

The `tender_extraction` directory is physically partitioned into four cohesive domain subpackages:

```
tender_extraction/
├── core/                    # Core configuration, models, runtime & cache
│   ├── config.py
│   ├── schemas.py
│   ├── schemas_match.py
│   ├── model_runtime.py
│   ├── cache.py
│   └── __init__.py
│
├── ingestion/               # Intake, OCR, table extraction & parameter parsing
│   ├── ingestion.py
│   ├── table_extraction.py
│   ├── chunking.py
│   ├── extraction.py
│   ├── validation.py
│   ├── parameters.py
│   ├── vision.py
│   └── __init__.py
│
├── reconciliation/          # GeM multi-doc bundles, item categorization & cross-checking
│   ├── tender_bundle.py
│   ├── reconcile.py
│   ├── categories.py
│   ├── scoring.py
│   └── __init__.py
│
├── matching/                # Qdrant vector retrieval, compliance matching & RAG QA
│   ├── matching.py
│   ├── retrieval.py
│   ├── manufacturer_tables.py
│   ├── qa.py
│   └── __init__.py
│
├── main.py                  # CLI pipeline orchestrator
├── __main__.py              # python -m tender_extraction entry point
├── __init__.py              # Package facade & backward-compatible module aliasing
└── README.md                # This architectural guide
```

---

## 📁 Subpackage Overview

### 1. `core/`
Fundamental settings, runtime hardware managers, and Pydantic schemas.
- **`config.py`**: Pydantic v2 application configuration, thresholds, device overrides, model names, and API keys.
- **`schemas.py`**: Core data schemas for document processing, procurement items, chunks, and citations.
- **`schemas_match.py`**: 4-status compliance models for manufacturer catalogue matching.
- **`model_runtime.py`**: Singleton hardware accelerator and model loader for `BAAI/bge-m3` embeddings.
- **`cache.py`**: Disk-backed cache for expensive intermediate extraction artifacts.

### 2. `ingestion/`
Raw document intake (PDF, DOCX, scanned images), OCR processing, chunking, and parameter extraction.
- **`ingestion.py`**: Multi-format document parser. Uses PyMuPDF with automatic CPU-fallback PaddleOCR for scanned pages.
- **`table_extraction.py`**: Extracts structured tables from PDFs, DOCX, and scanned documents into structured records.
- **`vision.py`**: Multimodal fallback (Qwen2-VL) for dense scanned tables and technical drawings.
- **`chunking.py`**: Section-aware and sliding-window chunker with metadata tracking (page, clause, source).
- **`extraction.py`**: Groq/LLM-powered parameter extraction and Scope of Work distillation with JSON auto-repair.
- **`validation.py`**: Schema verification, grounding score auditing, and hallucination prevention.
- **`parameters.py`**: Unit parsing, numerical range normalization, and specification value canonicalization.

### 3. `reconciliation/`
Specialized handling for multi-document GeM tender packages (Wrapper, BOQ, ATC, Technical Specs).
- **`tender_bundle.py`**: Orchestrates multi-attachment extraction, classification of files by role, and BOQ item mapping.
- **`reconcile.py`**: 3-way cross-document reconciliation (Wrapper vs. BOQ vs. Specs) producing non-blocking review notes.
- **`categories.py`**: Manifest-driven categorization and classification (`motors`, `pumps`, `valves`).
- **`scoring.py`**: Grounding score calculations and extraction quality metrics.

### 4. `matching/`
Vector retrieval (Qdrant), dense + sparse hybrid search, and evidence-grounded manufacturer compliance evaluation.
- **`retrieval.py`**: Qdrant client singleton, payload indexing, hybrid RRF (dense + BM25) search.
- **`matching.py`**: Optimized 4-status manufacturer matching engine with query pre-encoding and ThreadPool concurrency.
- **`manufacturer_tables.py`**: Canonical product datasheet schemas and tabular specs normalization.
- **`qa.py`**: RAG-based interactive question-answering session over indexed tender documents.

---

## 🔄 Backward Compatibility

All legacy imports (e.g., `from tender_extraction.schemas import Chunk` or `from tender_extraction.matching import generate_match_report`) are automatically and transparently routed via dynamic module resolution in `tender_extraction/__init__.py`. New code can use both the subpackage path and the root package facade interchangeably.
