"""Manufacturer catalogue retrieval, matching engine, and RAG QA."""

from .matching import (
    generate_match_report,
    match_requirement,
    load_manufacturers,
    aggregate,
    split_requirements,
)
from .retrieval import (
    HybridRetriever,
    get_qdrant_client,
    ensure_payload_indexes,
    expand_query,
)
from .manufacturer_tables import normalize_product_table
from .qa import DocumentChatSession

__all__ = [
    "generate_match_report",
    "match_requirement",
    "load_manufacturers",
    "aggregate",
    "split_requirements",
    "HybridRetriever",
    "get_qdrant_client",
    "ensure_payload_indexes",
    "expand_query",
    "normalize_product_table",
    "DocumentChatSession",
]
