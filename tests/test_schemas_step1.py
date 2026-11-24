from __future__ import annotations

from typing import get_args

from tender_extraction.schemas import ItemProcessingStatus, ItemType, ProcurementItem
from tender_extraction.schemas_match import (
    DECIDED_STATUSES,
    SCORABLE_STATUSES,
    STATUS_WEIGHTS,
    AuditTrail,
    EvidenceRef,
    ItemMatchResult,
    ManufacturerResult,
    MatchReport,
    MatchStatus,
    Requirement,
    RequirementMatch,
)


def test_status_contract():
    assert set(get_args(MatchStatus)) == {
        "full_match", "partial_match", "no_match", "no_evidence", "not_applicable",
    }
    # Only decided statuses carry weight.
    assert STATUS_WEIGHTS == {"full_match": 1.0, "partial_match": 0.5, "no_match": 0.0}
    assert DECIDED_STATUSES == {"full_match", "partial_match", "no_match"}
    # no_evidence is scorable (it lowers coverage) but never a failure.
    assert SCORABLE_STATUSES == {"full_match", "partial_match", "no_match", "no_evidence"}
    assert "not_applicable" not in SCORABLE_STATUSES


def test_no_evidence_is_not_weighted():
    assert "no_evidence" not in STATUS_WEIGHTS
    assert "not_applicable" not in STATUS_WEIGHTS


def test_audit_trail_preserves_searched_evidence():
    match = RequirementMatch(
        requirement_id="R001",
        manufacturer="Flowserve",
        status="no_evidence",
        score=0.0,
        audit=AuditTrail(
            searched_queries=["valve pressure 150 bar", "flange dimensions"],
            searched_pages=[1, 2, 5],
            searched_documents=["cat1.pdf", "cat2.pdf"],
        ),
        reason="Catalogue is silent on required parameter.",
    )
    dumped = match.model_dump()
    assert dumped["status"] == "no_evidence"
    assert len(dumped["audit"]["searched_queries"]) == 2
    assert dumped["audit"]["searched_pages"] == [1, 2, 5]


def test_item_match_result_and_procurement_item():
    item = ProcurementItem(
        item_id="1",
        description="Main Inlet Valve 200 mm",
        quantity="6",
        unit="Nos",
        item_type="main_product",
        processing_status="ready",
    )
    assert item.item_type == "main_product"
    assert item.processing_status == "ready"
    assert set(get_args(ItemType)) == {
        "main_product", "spares", "tools", "consumables", "review",
    }
    assert set(get_args(ItemProcessingStatus)) == {
        "ready", "no_requirements", "reference_unresolved", "needs_review",
    }

    ancillary = ProcurementItem(
        item_id="2",
        description="Gem Schedule-2 SPARES AS PER CLAUSE 1.2",
        quantity="2",
        unit="Set",
        item_type="spares",
        processing_status="reference_unresolved",
        referenced_standards=["HT 00211"],
    )
    assert ancillary.item_type == "spares"
    assert ancillary.processing_status == "reference_unresolved"

    item_res = ItemMatchResult(
        item_id="1",
        description="Main Inlet Valve 200 mm",
        item_type="main_product",
        processing_status="ready",
        status_counts={"full_match": 4, "partial_match": 1, "no_match": 0, "no_evidence": 1},
        mandatory_counts={"passes": 2, "fails": 0},
    )
    assert item_res.status_counts["no_evidence"] == 1
    assert item_res.mandatory_counts["fails"] == 0


def test_manufacturer_and_report_defaults():
    manufacturer = ManufacturerResult(manufacturer="KSB")
    assert manufacturer.overall_compatibility == 0.0
    assert manufacturer.coverage == 0.0
    assert manufacturer.low_coverage is False
    assert manufacturer.tender_match_status == "evaluated"

    report = MatchReport(tender_file="tender.pdf")
    assert report.tender_status == "evaluated"
    assert report.skipped_manufacturers == []
    assert report.llm_calls == 0
    assert report.llm_cache_hits == 0


def test_requirement_carries_its_category():
    req = Requirement(requirement_id="R1", text="Pump flow", category="pumps")
    assert req.category == "pumps"
    assert req.source.chunk_id == "NOT_FOUND"
    assert EvidenceRef().document == "NOT_FOUND"
