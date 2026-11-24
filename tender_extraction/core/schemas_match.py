"""schemas_match.py — Shared contract for the manufacturer-matching report.

Per-requirement statuses are exactly:
  full_match, partial_match, no_match, no_evidence
plus ``not_applicable`` for buyer/procedure lines (excluded from scores).

``no_evidence`` means nothing relevant was retrieved, or the cited quote could
not be found verbatim on the cited page.  It is never a failure.
``no_match`` is only used when evidence exists and fails the requirement.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from .schemas import SourceCitation

MatchStatus = Literal[
    "full_match",
    "partial_match",
    "no_match",
    "no_evidence",
    "not_applicable",
]

# Only decided statuses contribute to compatibility.
STATUS_WEIGHTS: Dict[str, float] = {
    "full_match": 1.0,
    "partial_match": 0.5,
    "no_match": 0.0,
}

DECIDED_STATUSES = frozenset(STATUS_WEIGHTS)
SCORABLE_STATUSES = frozenset({"full_match", "partial_match", "no_match", "no_evidence"})


class EvidenceRef(BaseModel):
    document: str = "NOT_FOUND"
    page: int = 0
    quote: str = "NOT_FOUND"
    chunk_id: str = "NOT_FOUND"


class AuditTrail(BaseModel):
    """Provenance for no_evidence or reviewable claims."""

    searched_queries: List[str] = Field(default_factory=list)
    searched_pages: List[int] = Field(default_factory=list)
    searched_documents: List[str] = Field(default_factory=list)
    searched_chunks: List[str] = Field(default_factory=list)


class Requirement(BaseModel):
    requirement_id: str
    boq_item_id: str = ""
    category: str = ""
    text: str
    parameter: str = "NOT_FOUND"
    value: str = "NOT_FOUND"
    unit: str = "NOT_FOUND"
    direction: Literal["exact", "min", "max", "range", "any"] = "any"
    kind: Literal["technical", "commercial", "legal", "compliance", "vague", "reference"] = "technical"
    is_mandatory: bool = False
    source: Any = Field(default_factory=SourceCitation)


class RequirementMatch(BaseModel):
    requirement_id: str
    boq_item_id: str = ""
    manufacturer: str
    capability: str = "NOT_FOUND"
    status: MatchStatus
    score: float = Field(default=0.0, ge=0.0, le=100.0)
    evidence: List[EvidenceRef] = Field(default_factory=list)
    audit: Optional[AuditTrail] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    is_mandatory: bool = False
    needs_review: bool = False
    reason: str = ""


class ItemMatchResult(BaseModel):
    """Aggregated match result for one BOQ item."""

    item_id: str
    description: str = ""
    item_type: str = "main_product"
    category: str = ""
    processing_status: str = "ready"
    compatibility: float = Field(default=0.0, ge=0.0, le=100.0)
    coverage: float = Field(default=0.0, ge=0.0, le=100.0)
    evidence_confidence: float = Field(default=0.0, ge=0.0, le=100.0)
    status_counts: Dict[str, int] = Field(default_factory=dict)
    mandatory_counts: Dict[str, int] = Field(default_factory=dict)
    matches: List[RequirementMatch] = Field(default_factory=list)
    review_reasons: List[str] = Field(default_factory=list)


class ManufacturerResult(BaseModel):
    manufacturer: str
    slug: str = ""
    category: str = ""
    overall_compatibility: float = Field(default=0.0, ge=0.0, le=100.0)
    coverage: float = Field(default=0.0, ge=0.0, le=100.0)
    evidence_confidence: float = Field(default=0.0, ge=0.0, le=100.0)
    low_coverage: bool = False
    rank: int = 0
    # evaluated | low_coverage | unindexed | not_evaluated
    tender_match_status: str = "evaluated"
    status_counts: Dict[str, int] = Field(default_factory=dict)
    mandatory_counts: Dict[str, int] = Field(default_factory=dict)
    item_results: List[ItemMatchResult] = Field(default_factory=list)
    matches: List[RequirementMatch] = Field(default_factory=list)


class MatchReport(BaseModel):
    tender_file: str
    tender_status: str = "evaluated"  # evaluated | no_manufacturers
    category: str = ""
    categories: List[str] = Field(default_factory=list)
    requirements: List[Requirement] = Field(default_factory=list)
    manufacturers: List[ManufacturerResult] = Field(default_factory=list)
    skipped_manufacturers: List[Dict[str, Any]] = Field(default_factory=list)
    item_summaries: List[Dict[str, Any]] = Field(default_factory=list)
    generated_at: float = 0.0
    notes: List[str] = Field(default_factory=list)
    # The four outputs stay separate: technical compliance, vendor
    # eligibility, evidence coverage and reconciliation observations.
    reconciliation: Dict[str, Any] = Field(default_factory=dict)
    technical_compliance: List[Dict[str, Any]] = Field(default_factory=list)
    vendor_eligibility: List[Dict[str, Any]] = Field(default_factory=list)
    evidence_coverage: Dict[str, Any] = Field(default_factory=dict)
    llm_calls: int = 0
    llm_cache_hits: int = 0
