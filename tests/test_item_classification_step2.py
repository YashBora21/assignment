"""
test_item_classification_step2.py — Step 2: Item classification, standard reference detection,
and per-item blocking behavior.

All test data is synthetic and labelled as such.
"""
import pytest
from decimal import Decimal
from tender_extraction.tender_bundle import (
    classify_item_type,
    detect_standard_references,
    _association_parameters,
)


# ---------------------------------------------------------------------------
# classify_item_type — strong patterns only
# ---------------------------------------------------------------------------

def test_classify_spares_as_per():
    item_type, reason = classify_item_type("SPARES AS PER ANNEXURE-II AND CLAUSE 1.2 OF PRODUCT STANDARD HT 00211")
    assert item_type == "spares", f"Expected spares, got {item_type}"

def test_classify_tools_as_per():
    item_type, reason = classify_item_type("TOOLS AS PER CLAUSE 1.6 OF PRODUCT STANDARD HT 00211")
    assert item_type == "tools", f"Expected tools, got {item_type}"

def test_classify_paint_as_per():
    item_type, reason = classify_item_type("PAINT AS PER CLAUSE 1.7 OF PRODUCT STANDARD HT00211")
    assert item_type == "consumables", f"Expected consumables, got {item_type}"

def test_classify_paint_standalone():
    item_type, reason = classify_item_type("PAINT: 2 LITRES RED OXIDE")
    assert item_type == "consumables"

def test_classify_primer():
    item_type, reason = classify_item_type("PRIMER AS PER IS 101")
    assert item_type == "consumables"

def test_classify_main_product_valve():
    item_type, reason = classify_item_type("INTERMITTENT BLOWDOWN VALVE 2IN 150 IBR")
    assert item_type == "main_product"

def test_classify_main_product_cylinder():
    item_type, reason = classify_item_type("MAIN INLET VALVE DOUBLE ACTING SERVOMOTOR DIAMETER 200 MM, STROKE-769 MM")
    assert item_type == "main_product"

def test_classify_unknown_boq_line_defaults_to_main_product():
    # Unknown lines are still matchable product lines; only activity-only lines
    # become review items.
    item_type, reason = classify_item_type("MISCELLANEOUS ITEMS AS REQUIRED BY SITE ENGINEER")
    assert item_type == "main_product", f"Expected main_product, got {item_type}"
    item_type, reason = classify_item_type("Supply and installation of equipment")
    assert item_type == "review", f"Expected review, got {item_type}"


# ---------------------------------------------------------------------------
# detect_standard_references
# ---------------------------------------------------------------------------

def test_detect_ht_00211():
    desc = "SPARES AS PER ANNEXURE-II AND CLAUSE - 1.2 OF PRODUCT STANDARD HT 00211"
    refs = detect_standard_references(desc)
    assert any("HT" in r and "00211" in r.replace(" ", "") for r in refs), f"HT 00211 not found in {refs}"

def test_detect_ht_00211_no_space():
    desc = "PAINT AS PER CLAUSE - 1.7 OF PRODUCT STANDARD HT00211"
    refs = detect_standard_references(desc)
    assert any("HT" in r for r in refs), f"HT not found in {refs}"

def test_detect_no_standard_reference():
    desc = "INTERMITTENT BLOWDOWN VALVE 2IN 150 IBR, QTY 8"
    refs = detect_standard_references(desc)
    assert refs == [], f"Expected no refs, got {refs}"


# ---------------------------------------------------------------------------
# Pressure class in _association_parameters — 150 vs 600 disambiguation
# ---------------------------------------------------------------------------

def test_pressure_class_150_extracted():
    params = _association_parameters("INTERMITTENT BLOWDOWN VALVE 2IN 150 IBR")
    assert "pressure_class" in params
    assert Decimal("150") in params["pressure_class"]

def test_pressure_class_600_extracted():
    params = _association_parameters("CONTINUOUS BLOWDOWN VALVE 2IN 600 IBR")
    assert "pressure_class" in params
    assert Decimal("600") in params["pressure_class"]

def test_pressure_class_150_vs_600_distinct():
    params_150 = _association_parameters("2IN 150 IBR")
    params_600 = _association_parameters("2IN 600 IBR")
    assert params_150["pressure_class"] != params_600["pressure_class"]


# ---------------------------------------------------------------------------
# Per-item blocking — ancillary items do NOT block matching
# ---------------------------------------------------------------------------

def test_ancillary_reference_unresolved_does_not_block():
    """
    SYNTHETIC TEST: A tender with 1 main_product item (has specs) and 1 spares item
    citing an unresolved standard. Only the main product should proceed.
    """
    from tender_extraction.reconcile import reconcile_result
    
    items = [
        {
            "item_id": "1",
            "description": "SYNTHETIC VALVE MAIN PRODUCT DN150 PN16",
            "quantity": "2",
            "unit": "Nos",
            "item_type": "main_product",
            "processing_status": "ready",
            "referenced_standards": [],
        },
        {
            "item_id": "2",
            "description": "SYNTHETIC SPARES AS PER CLAUSE 1.2 OF STANDARD HT 00211",
            "quantity": "1",
            "unit": "Set",
            "item_type": "spares",
            "processing_status": "reference_unresolved",
            "referenced_standards": ["HT 00211"],
        },
    ]
    
    result = {
        "technical_specifications": [
            {
                "component": "SYNTHETIC Gate Valve",
                "boq_item_id": "1",
                "association_status": "matched",
                "association_reason": "unique item name/parameter match",
                "specs": {"size": "DN150", "pressure": "PN16"},
                "source": {"document": "SYNTHETIC_spec.pdf", "page": 1, "exact_text": "DN150 PN16 SYNTHETIC"},
                "confidence": 0.9,
            }
        ],
        "scope_of_work": {"summary": "Synthetic test scope", "deliverables": [], "exclusions": [], "locations": [], "references": []},
        "accuracy_score": 0.9,
        "notes": [],
        "attachments": [],
        "procurement_items": items,
        "wrapper_item_candidates": [],
        "manual_attachments": [],
        "quantity_check": {},
        "wrapper": "",
        "wrapper_sha256": "",
        "boq_referenced": True,
        "quantity_citation": {},
        "reconciliation": {},
        "canonical_requirements": [],
        "compliance_requirements": [],
        "vendor_eligibility": [],
        "blocking_reasons": [],
        "per_item_status": {
            "1": {"item_type": "main_product", "processing_status": "ready", "referenced_standards": [], "has_requirements": True},
            "2": {"item_type": "spares", "processing_status": "reference_unresolved", "referenced_standards": ["HT 00211"], "has_requirements": False},
        },
    }
    
    reconciliation = reconcile_result(result)
    
    assert not reconciliation["blocked"], (
        f"Ancillary reference_unresolved item should not block matching. "
        f"Blockers: {reconciliation['blocking_reasons']}"
    )
    assert len(reconciliation["canonical_requirements"]) > 0, "Should have requirements from main product"
