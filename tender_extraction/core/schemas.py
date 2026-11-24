"""
schemas.py — Pydantic v2 models with strict validation.

Output schema matches the recommended tender extraction format:
  technical_specifications: list of component + specs-dict + source + confidence
  scope_of_work: summary + deliverables + exclusions + locations + references
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

ItemType = Literal["main_product", "spares", "tools", "consumables", "review"]
ItemProcessingStatus = Literal["ready", "no_requirements", "reference_unresolved", "needs_review"]


class SourceCitation(BaseModel):
    """Where in the document an extraction came from."""
    chunk_id: str = Field(default="NOT_FOUND", description="ID of the source chunk")
    page: int = Field(default=0, description="1-based page number")
    document: str = ""
    row: Optional[int] = None
    clause: str = Field(default="NOT_FOUND", description="Clause or section number (e.g. '3.1.2')")
    exact_text: str = Field(
        default="NOT_FOUND",
        description="Verbatim quote from the source chunk",
    )


class ProcurementItem(BaseModel):
    """Authoritative procurement item from BOQ or schedule."""
    item_id: str
    description: str
    quantity: str = "1"
    unit: str = "Nos"
    item_type: ItemType = "main_product"
    processing_status: ItemProcessingStatus = "ready"
    # Category is derived from the manufacturer manifests (categories.py).
    category: str = ""
    referenced_standards: List[str] = Field(default_factory=list)
    review_reasons: List[str] = Field(default_factory=list)
    source_type: str = "boq"
    source: Dict[str, Any] = Field(default_factory=dict)
    synthetic: bool = False
    inferred_unit: bool = False


class TechnicalSpecification(BaseModel):
    """
    A single extracted technical specification.

    `component` is the name of the part/system/material.
    `specs` is a flat dict of parameter → value-with-units, e.g.
        {"capacity": "10 TR", "power_supply": "415 V 3-phase 50 Hz"}.
    `confidence` is a 0-1 float set by the grounding verifier.
    """
    component: str
    boq_item_id: str = ""
    category: str = ""
    association_status: str = ""
    association_reason: str = ""
    specs: Dict[str, str] = Field(default_factory=dict)
    source: SourceCitation = Field(default_factory=SourceCitation)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("component")
    @classmethod
    def component_must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("component cannot be empty or whitespace")
        return v.strip()


class ScopeOfWork(BaseModel):
    """
    Complete scope-of-work section.

    `summary`     — concise description (≤120 words).
    `deliverables`— list of deliverable strings.
    `exclusions`  — list of items explicitly NOT in scope.
    `locations`   — site/building locations mentioned.
    `references`  — clause/page references cited in the scope section.
    """
    summary: str = Field(default="NOT_FOUND")
    deliverables: List[str] = Field(default_factory=list)
    exclusions: List[str] = Field(default_factory=list)
    locations: List[str] = Field(default_factory=list)
    references: List[str] = Field(default_factory=list)


class ExtractionResult(BaseModel):
    """Top-level output of the entire pipeline."""
    pipeline_version: str = ""
    tender_status: str = "ready"  # ready, needs_review
    technical_specifications: List[TechnicalSpecification] = Field(default_factory=list)
    scope_of_work: ScopeOfWork = Field(default_factory=ScopeOfWork)
    accuracy_score: float = Field(default=0.0, description="Overall grounding score 0-100")
    notes: List[str] = Field(default_factory=list, description="Pipeline caveats, e.g. pages where OCR failed")
    # Structured, non-blocking observations.  Each entry is
    # {"type": unlinked_requirement|attachment_failure|unresolved_standard|boq_mismatch|other,
    #  "message": str, ...}.  Nothing here blocks matching.
    review_notes: List[dict] = Field(default_factory=list)
    # Primary product category derived from the manufacturer manifests.
    category: str = ""
    categories: List[str] = Field(default_factory=list)
    attachments: List[dict] = Field(default_factory=list)
    procurement_items: List[dict] = Field(default_factory=list)
    wrapper_item_candidates: List[dict] = Field(default_factory=list)
    manual_attachments: List[dict] = Field(default_factory=list)
    quantity_check: dict = Field(default_factory=dict)
    # Attachment identity, used as the citation anchor for wrapper conditions.
    wrapper: str = ""
    wrapper_sha256: str = ""
    boq_referenced: bool = False
    quantity_citation: dict = Field(default_factory=dict)
    # Cross-document reconciliation (see tender_extraction/reconcile.py).
    reconciliation: dict = Field(default_factory=dict)
    canonical_requirements: List[dict] = Field(default_factory=list)
    compliance_requirements: List[dict] = Field(default_factory=list)
    vendor_eligibility: List[dict] = Field(default_factory=list)
    blocking_reasons: List[str] = Field(default_factory=list)
    per_item_status: Dict[str, Any] = Field(default_factory=dict)


# ── Internal models ───────────────────────────────────────────────────────

class ChunkMetadata(BaseModel):
    """Metadata attached to every chunk."""
    section: str = Field(default="Unknown")
    parent_section: str = Field(default="Unknown")
    chunk_type: str = Field(default="paragraph")   # table | paragraph | list | image_ocr
    page: int = Field(default=0)
    document: str = ""
    role: str = ""
    row: Optional[int] = None
    table_id: Optional[str] = Field(default=None)
    bbox: Optional[List[float]] = Field(default=None)


class Chunk(BaseModel):
    """A single chunk produced by the chunking stage."""
    chunk_id: str
    text: str
    metadata: ChunkMetadata
