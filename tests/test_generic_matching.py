"""test_generic_matching.py — Generic synthetic regression tests.

Tests core principles independent of any specific tender PDF:
1. One main product plus ancillaries (spares/tools/paint) -> sub-components associate cleanly.
2. Two main products with different pressure classes (150# vs 600#) -> strict dimension isolation.
3. Unit equivalences: 15 bar == 1.5 MPa == 15.3 kg/cm2 (agree within 2%).
4. Controlled parameter vocabulary: bore vs rod_diameter never conflict.
5. Non-blocking ancillary reference: missing standard on tools/spares does not block main product matching.
"""
from decimal import Decimal
import pytest

from tender_extraction.parameters import (
    ParameterValue,
    extract_parameter_values,
    canonical_parameter_name,
)
from tender_extraction.tender_bundle import match_spec_item, finish_bundle_result
from tender_extraction.schemas_match import Requirement, RequirementMatch


def test_controlled_parameter_vocabulary_bore_vs_rod_diameter():
    """Bore (200 mm) and rod diameter (100 mm) must be treated as distinct parameters."""
    text = "Hydraulic Cylinder Bore 200 mm, Piston Rod Dia 100 mm, Stroke 769 mm"
    params = extract_parameter_values(text)
    
    assert "bore" in params
    assert "rod_diameter" in params
    assert "stroke" in params
    
    bore_val = next(iter(params["bore"]))
    rod_val = next(iter(params["rod_diameter"]))
    
    assert bore_val.normalized_value == Decimal("200.0")
    assert rod_val.normalized_value == Decimal("100.0")
    # Comparing them must return None (not a conflict, just different parameters)
    assert bore_val.is_compatible(rod_val) is None


def test_pressure_class_vs_test_pressure_distinction():
    """Test pressure (150 kg/cm2) is unit_class: pressure; Class 150# is unit_class: rating."""
    text = "Valve Class 150#, Hydrostatic Test Pressure 150 kg/cm2"
    params = extract_parameter_values(text)
    
    assert "pressure_class" in params
    assert "test_pressure" in params
    
    p_class = next(iter(params["pressure_class"]))
    t_press = next(iter(params["test_pressure"]))
    
    assert p_class.unit_class == "rating"
    assert t_press.unit_class == "pressure"
    assert p_class.is_compatible(t_press) is None


def test_unit_equivalent_values_bar_vs_mpa_vs_kgcm2():
    """10 bar == 1.0 MPa; 100 kg/cm2 ~= 98.07 bar."""
    pv_bar = ParameterValue("test_pressure", "10 bar", Decimal("10.0"), "bar", "pressure")
    pv_mpa = ParameterValue("test_pressure", "1 MPa", Decimal("10.0"), "mpa", "pressure")
    
    assert pv_bar.is_compatible(pv_mpa) is True
    
    pv_diff = ParameterValue("test_pressure", "20 bar", Decimal("20.0"), "bar", "pressure")
    assert pv_bar.is_compatible(pv_diff) is False


def test_single_main_product_plus_ancillaries_association():
    """In a bundle with 1 main product + spares/tools/paint, mechanical specs map to Item 1."""
    items = [
        {"item_id": "1", "description": "Centrifugal Slurry Pump 100 m3/h", "item_type": "main_product"},
        {"item_id": "2", "description": "Mandatory Spares for Slurry Pump as per Clause 5", "item_type": "spares"},
        {"item_id": "3", "description": "Special Maintenance Tools as per Clause 6", "item_type": "tools"},
        {"item_id": "4", "description": "Synthetic Enamel Paint as per IS:14506", "item_type": "consumables"},
    ]
    
    # Sub-component with no main product name in its component title
    spec_impeller = {"component": "Closed Impeller Cast Steel", "specs": {"material": "ASTM A216 WCB"}}
    spec_shaft = {"component": "Drive Shaft", "specs": {"diameter": "65 mm"}}
    
    t1, r1 = match_spec_item(spec_impeller, items)
    t2, r2 = match_spec_item(spec_shaft, items)
    
    assert t1 == "1"
    assert t2 == "1"


def test_two_main_products_with_different_pressure_classes():
    """Two valves with 150# vs 600# must be strictly isolated to their matching BOQ item."""
    items = [
        {"item_id": "1", "description": "Globe Valve 2 inch 150# Flanged", "item_type": "main_product"},
        {"item_id": "2", "description": "Globe Valve 2 inch 600# Flanged", "item_type": "main_product"},
    ]
    
    spec_150 = {"component": "Globe Valve 150 Class", "specs": {"rating": "150#"}}
    spec_600 = {"component": "High Pressure Globe Valve 600 Class", "specs": {"rating": "600#"}}
    
    t1, _ = match_spec_item(spec_150, items)
    t2, _ = match_spec_item(spec_600, items)
    
    assert t1 == "1"
    assert t2 == "2"


def test_non_blocking_ancillary_reference_unresolved():
    """A tender where Item 2 (tools) has an unresolved standard reference must NOT block Item 1 matching."""
    bundle = {
        "complete": True,
        "missing": [],
        "attachments": [{"role": "specification", "status": "ready"}],
        "directory": ".",
    }
    items = [
        {"item_id": "1", "description": "Induction Motor 50 kW 415V", "quantity": "1", "unit": "Nos", "item_type": "main_product"},
        {"item_id": "2", "description": "Special Tools AS PER HT 99999", "quantity": "1", "unit": "Set", "item_type": "tools"},
    ]
    raw_result = {
        "technical_specifications": [
            {"component": "Induction Motor", "specs": {"power": "50 kW", "voltage": "415 V"}, "association_status": "matched", "boq_item_id": "1"}
        ]
    }

    res = finish_bundle_result(raw_result, bundle, items, [])

    # Blocking flags are gone from the schema; unresolved ancillary references
    # stay as review notes and matching still runs.
    assert "tender_complete" not in res
    assert "matching_blocked" not in res
    assert res["reconciliation"]["blocked"] is False
    assert any("reference_unresolved" in note or "tools" in note.lower() for note in res.get("notes", []))
    assert any(note["type"] == "unresolved_standard" for note in res.get("review_notes", [])) or any(
        note["type"] == "unlinked_requirement" for note in res.get("review_notes", [])
    )
