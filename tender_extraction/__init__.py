"""
TenderExtractPro — RAG-based Tender Document Extraction & Matching System
========================================================================

Physical Subfolder Architecture:
  1. core/           : Configuration, hardware runtime, disk cache, and base schemas
  2. ingestion/      : Multi-format parsing, OCR, chunking, and parameter extraction
  3. reconciliation/ : GeM multi-document bundle parsing, categorization, and cross-checking
  4. matching/       : Qdrant retrieval, RRF hybrid search, 4-status matching, and QA
"""

import importlib
import importlib.abc
import importlib.util
import sys
from typing import Any

__version__ = "1.0.0"
PIPELINE_VERSION = "2026-11-19.1"
__author__ = "TenderExtractPro"

# Backward compatibility map: allows all legacy `from tender_extraction.<mod> import ...`
# to work transparently while keeping physical files neatly grouped in subfolders.
MODULE_ALIASES = {
    # Layer 1: Core
    "tender_extraction.config": "tender_extraction.core.config",
    "tender_extraction.schemas": "tender_extraction.core.schemas",
    "tender_extraction.schemas_match": "tender_extraction.core.schemas_match",
    "tender_extraction.model_runtime": "tender_extraction.core.model_runtime",
    "tender_extraction.cache": "tender_extraction.core.cache",
    # Layer 2: Ingestion & Extraction
    "tender_extraction.chunking": "tender_extraction.ingestion.chunking",
    "tender_extraction.extraction": "tender_extraction.ingestion.extraction",
    "tender_extraction.parameters": "tender_extraction.ingestion.parameters",
    "tender_extraction.table_extraction": "tender_extraction.ingestion.table_extraction",
    "tender_extraction.validation": "tender_extraction.ingestion.validation",
    "tender_extraction.vision": "tender_extraction.ingestion.vision",
    # Layer 3: Reconciliation & Bundles
    "tender_extraction.categories": "tender_extraction.reconciliation.categories",
    "tender_extraction.reconcile": "tender_extraction.reconciliation.reconcile",
    "tender_extraction.scoring": "tender_extraction.reconciliation.scoring",
    "tender_extraction.tender_bundle": "tender_extraction.reconciliation.tender_bundle",
    # Layer 4: Matching & Retrieval
    "tender_extraction.manufacturer_tables": "tender_extraction.matching.manufacturer_tables",
    "tender_extraction.qa": "tender_extraction.matching.qa",
    "tender_extraction.retrieval": "tender_extraction.matching.retrieval",
}


class _BackwardCompatFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname: str, path: Any, target: Any = None) -> Any:
        if fullname in MODULE_ALIASES:
            target_name = MODULE_ALIASES[fullname]
            real_mod = importlib.import_module(target_name)
            sys.modules[fullname] = real_mod
            short_name = fullname.split(".")[-1]
            parent_mod = sys.modules.get("tender_extraction")
            if parent_mod:
                setattr(parent_mod, short_name, real_mod)
            return importlib.util.find_spec(target_name)
        return None


if not any(isinstance(f, _BackwardCompatFinder) for f in sys.meta_path):
    sys.meta_path.insert(0, _BackwardCompatFinder())


# Lazy convenience exports from subpackages
_LAZY_EXPORTS = {
    # Layer 1
    "config": ("tender_extraction.core.config", "config"),
    "Chunk": ("tender_extraction.core.schemas", "Chunk"),
    "ChunkMetadata": ("tender_extraction.core.schemas", "ChunkMetadata"),
    "ExtractionResult": ("tender_extraction.core.schemas", "ExtractionResult"),
    "MatchReport": ("tender_extraction.core.schemas_match", "MatchReport"),
    # Layer 2
    "ingest_document": ("tender_extraction.ingestion", "ingest_document"),
    "create_chunks": ("tender_extraction.ingestion", "create_chunks"),
    "extract_tables": ("tender_extraction.ingestion", "extract_tables"),
    # Layer 3
    "extract_bundle": ("tender_extraction.reconciliation", "extract_bundle"),
    "reconcile_result": ("tender_extraction.reconciliation", "reconcile_result"),
    # Layer 4
    "TenderExtractionPipeline": ("tender_extraction.main", "TenderExtractionPipeline"),
    "HybridRetriever": ("tender_extraction.matching", "HybridRetriever"),
    "get_qdrant_client": ("tender_extraction.matching", "get_qdrant_client"),
    "generate_match_report": ("tender_extraction.matching", "generate_match_report"),
    "load_manufacturers": ("tender_extraction.matching", "load_manufacturers"),
}


def __getattr__(name: str) -> Any:
    if name in _LAZY_EXPORTS:
        module_path, attr_name = _LAZY_EXPORTS[name]
        mod = importlib.import_module(module_path)
        val = getattr(mod, attr_name)
        globals()[name] = val
        return val
    if f"tender_extraction.{name}" in MODULE_ALIASES:
        target = MODULE_ALIASES[f"tender_extraction.{name}"]
        mod = importlib.import_module(target)
        globals()[name] = mod
        return mod
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "__version__",
    "PIPELINE_VERSION",
    "__author__",
    "config",
    "Chunk",
    "ChunkMetadata",
    "ExtractionResult",
    "MatchReport",
    "ingest_document",
    "create_chunks",
    "extract_tables",
    "extract_bundle",
    "reconcile_result",
    "TenderExtractionPipeline",
    "HybridRetriever",
    "get_qdrant_client",
    "generate_match_report",
    "load_manufacturers",
]
