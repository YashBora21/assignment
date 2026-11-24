"""Rule-based cross-document reconciliation. No model or network calls."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tender_extraction import matching
from tender_extraction.reconcile import reconcile_result
from tender_extraction.tender_bundle import read_wrapper_items


def _item(description, quantity="1", unit="Nos", source_type="boq", item_id="1"):
    return {
        "item_id": item_id,
        "description": description,
        "quantity": quantity,
        "unit": unit,
        "source_type": source_type,
        "source": {
            "document": "boq.csv" if source_type == "boq" else "wrapper.pdf",
            "page": 0 if source_type == "boq" else 7,
            "row": 2 if source_type == "boq" else None,
            "exact_text": f"row: {description} {quantity} {unit}",
        },
    }


def _spec(component, specs, item_id="1", page=2):
    return {
        "component": component,
        "boq_item_id": item_id,
        "specs": specs,
        "source": {
            "document": "spec.pdf",
            "page": page,
            "row": None,
            "exact_text": " | ".join(f"{key}: {value}" for key, value in specs.items()),
        },
    }


def _reconcile(specs, items, *, quantity_expected="1", quantity_actual="1", **extra):
    result = {
        "technical_specifications": specs,
        "procurement_items": items,
        "quantity_check": {
            "expected": quantity_expected,
            "actual": quantity_actual,
            "matches": quantity_expected == quantity_actual,
        },
        "scope_of_work": {},
        "attachments": [],
    }
    result.update(extra)
    return reconcile_result(result)


def _requirement(report, parameter):
    return next(row for row in report["canonical_requirements"] if row["parameter"] == parameter)


MOTOR_ITEM = _item("Induction Motor 37 kW, 415 V, 50 Hz, 4 Pole, IP55")
MOTOR_SPEC = _spec("Induction Motor", {
    "rated_power": "37 kW", "voltage": "415 V", "frequency": "50 Hz",
    "poles": "4", "protection": "IP55",
})


@pytest.mark.parametrize("spec_value,expected", [
    ("37 kW", "consistent"),
    ("50 HP", "consistent"),          # 50 HP = 37.285 kW, within the explicit 2% tolerance
    (">= 37 kW", "consistent"),       # compatibility, not a wording mismatch
    ("45 kW", "conflict"),
])
def test_rating_comparison_normalises_units_and_operators(spec_value, expected):
    spec = json.loads(json.dumps(MOTOR_SPEC))
    spec["specs"]["rated_power"] = spec_value
    report = _reconcile([spec], [MOTOR_ITEM])
    row = _requirement(report, "rated_power")
    assert row["status"] == expected, row
    if expected == "conflict":
        assert row["status"] == "conflict"
        assert row["canonical_value"] is None
        values = {assertion["source_type"]: assertion["value"] for assertion in row["assertions"]}
        assert values == {"specification": "45", "boq": "37"}


def test_conflict_preserves_both_citations():
    spec = json.loads(json.dumps(MOTOR_SPEC))
    spec["specs"]["rated_power"] = "45 kW"
    report = _reconcile([spec], [MOTOR_ITEM])
    row = _requirement(report, "rated_power")
    documents = {(assertion["source"]["document"], assertion["source"]["page"]) for assertion in row["assertions"]}
    assert ("spec.pdf", 2) in documents
    assert ("boq.csv", 0) in documents
    assert len(row["citations"]) == 2


def test_quantity_conflict_is_critical_and_blocks():
    items = [_item("Drain Valve", quantity="18", item_id="1"),
             _item("Tyre Valve Core", quantity="183", item_id="2")]
    report = _reconcile([_spec("Drain Valve", {"material": "SS316"})], items,
                        quantity_expected="200", quantity_actual="201")
    row = _requirement(report, "quantity")
    assert row["status"] == "conflict" and row["severity"] == "critical"
    assert report["blocking_reasons"]


def test_additional_specification_is_not_a_conflict():
    spec = json.loads(json.dumps(MOTOR_SPEC))
    spec["specs"]["insulation"] = "Class F"
    report = _reconcile([spec], [MOTOR_ITEM])
    row = _requirement(report, "insulation")
    assert row["status"] == "additional"
    assert not row["blocking"]
    assert not report["blocked"]


def test_unmapped_specification_kept_at_tender_level_not_blocking():
    spec = _spec("Centrifugal Pump", {"capacity": "10 m3/h"}, item_id="")
    report = _reconcile([spec], [MOTOR_ITEM])
    row = _requirement(report, "item_mapping")
    assert row["status"] == "unmapped"
    assert not row.get("blocking", False)


def test_specification_refines_a_broad_boq_item_name():
    item = _item("Motor")
    spec = _spec("Induction Motor", {"rated_power": "37 kW", "voltage": "415 V", "frequency": "50 Hz"})
    report = _reconcile([spec], [item])
    assert _requirement(report, "item_category")["status"] == "refines"


def test_missing_voltage_for_a_motor_is_an_informational_gap():
    spec = _spec("Induction Motor", {"rated_power": "37 kW"})
    report = _reconcile([spec], [_item("Induction Motor 37 kW")])
    row = _requirement(report, "voltage")
    assert row["status"] == "additional"
    assert not row.get("blocking", False)


def test_atc_compliance_and_vendor_eligibility_stay_separate():
    scope = {
        "deliverables": [
            "OEM Authorisation Certificate shall be uploaded with the bid",
            "Type test certificate and ISO 9001 certification shall be submitted",
            "Warranty certificate as per prescribed format",
            "Minimum average annual turnover of 6 Lakh for the last 3 years",
            "MSE bidders must upload Udyam registration",
        ],
        "exclusions": [],
        "references": [],
    }
    report = _reconcile([MOTOR_SPEC], [MOTOR_ITEM], scope_of_work=scope)
    categories = {row["category"] for row in report["compliance_requirements"]}
    assert {"oem_authorization", "test_certificate", "warranty"} <= categories
    vendor = {row["category"] for row in report["vendor_eligibility"]}
    assert {"turnover", "mse"} <= vendor
    assert all(row["source"]["exact_text"] for row in report["compliance_requirements"])


def test_referenced_but_missing_boq_blocks_without_wrapper_substitution():
    report = _reconcile(
        [],
        [],
        tender_complete=False,
        boq_referenced=True,
        attachments=[
            {"role": "specification", "status": "missing", "file": "spec.pdf"},
            {"role": "boq", "status": "missing", "file": "boq.csv"},
        ],
    )
    assert report["status"] in ("incomplete", "no_requirements")
    assert report["blocked"]
    text = " ".join(report["blocking_reasons"]).lower()
    assert "specification" in text and "boq" in text
    assert any(note["type"] == "attachment_failure" for note in report["review_notes"])
    assert report["coverage"]["items"] == []


def test_wrapper_items_are_used_only_when_no_boq_was_referenced():
    pages = [
        {"page": 1, "document": "wrapper.pdf", "text": (
            "Total Quantity\n200\nItem Category\nTank for Drinking Water , Valve Drain\n"
            "GeMARPTS\n")},
        {"page": 7, "document": "wrapper.pdf", "text": (
            "Consignees/Reporting Officer and Quantity\nS.No\nConsignee\nAddress\nQuantity\nDelivery Days\n"
            "1\n***********\n***********Tiruvallur\n18\n30\n")},
        {"page": 8, "document": "wrapper.pdf", "text": (
            "Consignees/Reporting Officer and Quantity\n1\n***********\n***********Tiruvallur\n182\n30\n")},
    ]
    items = read_wrapper_items(pages)
    assert [item["description"] for item in items] == ["Tank for Drinking Water", "Valve Drain"]
    assert [item["quantity"] for item in items] == ["18", "182"]
    assert all(item["source_type"] == "wrapper" for item in items)
    assert {item["source"]["page"] for item in items} == {7, 8}

    # Ambiguity (sum != wrapper total) is rejected, never guessed.
    pages[2]["text"] = pages[2]["text"].replace("182", "181")
    assert read_wrapper_items(pages) == []


def test_matching_consumes_reconciled_requirements_only():
    report = _reconcile([MOTOR_SPEC], [MOTOR_ITEM])
    tender = {
        "technical_specifications": [MOTOR_SPEC],
        "canonical_requirements": report["canonical_requirements"],
        "reconciliation": report,
        "tender_complete": True,
    }
    requirements = matching.split_requirements(tender)
    parameters = {req.parameter for req in requirements}
    assert "rated_power" in parameters and "voltage" in parameters
    assert "quantity" not in parameters and "item_category" not in parameters


def test_matching_engine_runs_with_reconciliation_conflict_and_only_stops_on_zero_reqs():
    spec = json.loads(json.dumps(MOTOR_SPEC))
    spec["specs"]["rated_power"] = "45 kW"
    report = _reconcile([spec], [MOTOR_ITEM])
    # The only hard stop is zero requirements extracted
    with pytest.raises(ValueError, match="No requirements could be extracted"):
        matching.generate_match_report({
            "technical_specifications": [],
            "canonical_requirements": [],
        })


def test_generic_attribute_and_test_specs_are_tender_level_not_blocking():
    items = [_item("Blowdown Valve 2IN 150", item_id="1"), _item("Blowdown Valve 2IN 600", item_id="2")]
    specs = [
        _spec("INTERMITTENT BLOWDOWN VALVE 2IN 150", {"size": "2IN"}, item_id="1"),
        _spec("CONTINUOUS BLOWDOWN VALVE 2IN 600", {"size": "2IN"}, item_id="2"),
        _spec("SEAT MATERIAL", {"hardness": "13% CR + HARD FACE"}, item_id=""),
        _spec("IBR CERTIFICATION", {"status": "Certified"}, item_id=""),
        _spec("HYDROSTATIC TEST", {"test_pressure": "1.6 times working pressure"}, item_id=""),
    ]
    report = _reconcile(specs, items, quantity_expected="2", quantity_actual="2")
    assert report["status"] != "blocked"
    assert report["coverage"]["unmapped_specifications"] == []
    components = {entry["component"] for entry in report["coverage"]["tender_level_specifications"]}
    assert components == {"SEAT MATERIAL", "IBR CERTIFICATION", "HYDROSTATIC TEST"}
    assert not report["blocking_reasons"]


def test_page_level_assignment_overrides_name_token_guess():
    from tender_extraction.tender_bundle import assign_spec_items

    items = [
        _item("INTERMITTENT BLOWDOWN VALVE 2IN 150 IBR", item_id="1"),
        _item("CONTINUOUS BLOWDOWN VALVE 2IN 600 IBR", item_id="2"),
        _item("INTERMITTENT BLOWDOWN VALVE 2IN 600 IBR", item_id="3"),
        _item("VLV INTERMITTANT BD PA-47 1IN 600", item_id="4"),
    ]
    pages = [
        {"document": "spec.pdf", "page": 1, "role": "specification",
         "text": "INTERMITTENT BLOWDOWN VALVE 2IN 150 IBR"},
        {"document": "spec.pdf", "page": 2, "role": "specification",
         "text": "CONTINUOUS BLOWDOWN VALVE 2IN 600 IBR INTERMITTENT BLOWDOWN VALVE 2IN 600 IBR"},
        {"document": "spec.pdf", "page": 3, "role": "specification",
         "text": "INTERMITTANT BD VLV PA-47 1IN 600 Size 1 inch Class 600#"},
    ]
    specs = [
        _spec("INTERMITTENT BLOWDOWN VALVE", {}, item_id="", page=1),
        _spec("Intermittent Blowdown Valve 2IN 600 IBR", {}, item_id="3", page=3),
        _spec("UNKNOWN", {}, item_id="", page=2),
    ]
    assign_spec_items(specs, pages, items)
    assert specs[0]["boq_item_id"] == "1"
    assert specs[1]["boq_item_id"] == ""
    assert specs[1]["association_status"] == "review"
    assert specs[2]["boq_item_id"] == ""   # page 2 names two items: stay ambiguous


def test_unmapped_product_is_recorded_as_a_review_note_not_a_blocker():
    items = [_item("Blowdown Valve 2IN 150", item_id="1"), _item("Blowdown Valve 2IN 600", item_id="2")]
    specs = [
        _spec("Blowdown Valve 2IN 150", {"size": "2IN"}, item_id="1"),
        _spec("Blowdown Valve 2IN 600", {"size": "2IN"}, item_id="2"),
        _spec("Swing Check Valve", {"drain_boss": "0.5 inch"}, item_id=""),
        _spec("Radiography testing", {"standard": "ASME B16.34"}, item_id=""),
    ]
    report = _reconcile(specs, items, quantity_expected="2", quantity_actual="2")
    assert {entry["component"] for entry in report["coverage"]["unmapped_specifications"]} == {"Swing Check Valve"}
    components = {entry["component"] for entry in report["coverage"]["tender_level_specifications"]}
    assert components == {"Radiography testing"}
    # An unmapped product is surfaced for review, but it does not stop matching.
    assert not report["blocking_reasons"]
    assert any(
        note["type"] == "unlinked_requirement" and "Swing Check Valve" in note["message"]
        for note in report["review_notes"]
    )
