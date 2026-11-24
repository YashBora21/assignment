"""reconcile.py — Rule-based cross-document reconciliation.

Inputs are the normalised extraction payload produced for one tender:

    technical_specifications : product requirements extracted from the SP/PDF
    procurement_items        : item name + quantity + unit from the BOQ/wrapper
    scope_of_work            : wrapper/ATC bid conditions
    attachments              : source completeness (specification / BOQ)
    quantity_check           : wrapper total vs item sum

The module compares those sources *before* manufacturer matching and emits:

    canonical_requirements   : one consolidated requirement per parameter
    reconciliation           : status table, conflicts, coverage, blockers
    compliance_requirements  : ATC technical/compliance clauses
    vendor_eligibility       : bidder eligibility clauses (kept separate)

Design rules:
  * Comparison is deterministic. Units are normalised, operators are honoured,
    and a tolerance is explicit. 50 HP is ~37.3 kW, so it is consistent with
    37 kW; 45 kW is not. ">= 37 kW" is satisfied by "37 kW".
  * Conflicts preserve both assertions and their citations. The canonical
    value stays unresolved (None). Nothing is silently picked by an LLM.
  * Only critical conflicts block matching. Category, quantity and rating
    conflicts are critical; wording differences are not.
  * A missing BOQ does not fail the tender when the wrapper carries an
    unambiguous item/quantity list. A *referenced* BOQ that could not be
    retrieved does fail it — the wrapper is never silently substituted.
"""
from __future__ import annotations

import math
import os
import re
from collections import OrderedDict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from rapidfuzz import fuzz

from tender_extraction.categories import (
    build_vocabulary,
    classify_text,
    classify_tender,
)

STATUSES = ("consistent", "conflict", "refines", "additional", "unmapped")
SEVERITIES = ("critical", "major", "minor", "info")


def _manufacturers_dir() -> Path:
    """Corpus location used only to build the category vocabulary."""
    raw = os.getenv("MANUFACTURERS_DIR")
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parent.parent / "data" / "manufacturers"

# Canonical dimension -> {unit spelling: factor to the base unit}.
_UNIT_FACTORS: Dict[str, Dict[str, float]] = {
    "pressure": {"bar": 1.0, "kpa": 0.01, "mpa": 10.0, "psi": 0.0689476, "kg/cm2": 0.980665, "pa": 1e-5},
    "length": {"m": 1.0, "cm": 0.01, "mm": 0.001, "inch": 0.0254, "in": 0.0254, "ft": 0.3048},
    "flow": {"m3/h": 1.0, "m3/hr": 1.0, "lps": 3.6, "l/s": 3.6, "l/min": 0.06, "gpm": 0.2271247, "lph": 0.001},
    "power": {"kw": 1.0, "w": 0.001, "hp": 0.7457, "mw": 1000.0},
    "temperature": {"°c": 1.0, "c": 1.0, "degc": 1.0, "celsius": 1.0},
    "voltage": {"v": 1.0, "kv": 1000.0},
    "frequency": {"hz": 1.0, "khz": 1000.0},
    "speed": {"rpm": 1.0},
    "mass": {"kg": 1.0, "g": 0.001, "tonne": 1000.0, "t": 1000.0},
    "percentage": {"%": 1.0},
}
_UNIT_LOOKUP: Dict[str, Tuple[str, float]] = {}
for _dim, _units in _UNIT_FACTORS.items():
    for _unit, _factor in _units.items():
        _UNIT_LOOKUP[_unit] = (_dim, _factor)

_BASE_UNIT = {
    "pressure": "bar", "length": "m", "flow": "m3/h", "power": "kW",
    "temperature": "°C", "voltage": "V", "frequency": "Hz", "speed": "rpm",
    "mass": "kg", "percentage": "%",
}

_PARAM_BY_DIMENSION = {
    "power": "rated_power",
    "voltage": "voltage",
    "frequency": "frequency",
    "speed": "speed",
    "pressure": "pressure",
    "flow": "flow_rate",
    "length": "dimension",
    "mass": "mass",
    "temperature": "temperature",
    "percentage": "percentage",
}
_PARAM_DIMENSION = {v: k for k, v in _PARAM_BY_DIMENSION.items()}

_NUM_UNIT_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*"
    r"(kg/cm2|m3/hr|m3/h|m³/h|l/min|l/s|°c|degc|kpa|mpa|psi|bar|lps|gpm|lph|rpm|khz|hz|kw|mw|hp|kv|mm|cm|inch|in|ft|kg|pa|w|v|t|g|%)"
    r"(?![a-z0-9])",
    re.IGNORECASE,
)
_MIN_RE = re.compile(r"(?:≥|>=|>|min(?:imum)?\.?|not less than|at ?least)\s*", re.IGNORECASE)
_MAX_RE = re.compile(r"(?:≤|<=|<|max(?:imum)?\.?|not more than|not exceeding|up ?to)\s*", re.IGNORECASE)
_RANGE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(\d+(?:\.\d+)?)"
    r"|between\s+(\d+(?:\.\d+)?)\s+and\s+(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_IP_RE = re.compile(r"\bIP\s*(\d{2}[A-Z]?)\b", re.IGNORECASE)
_POLES_RE = re.compile(r"\b(\d{1,2})\s*(?:pole|poles)\b", re.IGNORECASE)
_IE_RE = re.compile(r"\bIE\s*([1-4])\b", re.IGNORECASE)

_EXACT_TOLERANCE = 0.02  # 2%; 50 HP = 37.285 kW vs 37 kW is 0.77%.
_TEXT_EQUIVALENT = 85.0
_STOP_WORDS = {
    "for", "and", "of", "the", "a", "an", "with", "to", "in", "on",
    "right", "left", "as", "per", "type", "item", "items",
}

_CRITICAL_PARAMS = {"item_category", "quantity", "rated_power", "capacity"}
_ELECTRICAL_PARAMS = {"voltage", "frequency", "poles", "efficiency_class"}
_MOTOR_RE = re.compile(r"\b(motors?|pumps?|blower|compressor|fan)\b", re.IGNORECASE)

_PARAM_ALIASES = {
    "rated_power": {"rated_power", "power", "rating", "motor_power", "output", "kw", "hp"},
    "voltage": {"voltage", "volt", "rated_voltage", "supply_voltage", "power_supply"},
    "frequency": {"frequency", "freq", "supply_frequency"},
    "speed": {"speed", "rated_speed", "rpm"},
    "poles": {"poles", "pole", "number_of_poles"},
    "protection": {"protection", "ip_rating", "enclosure", "degree_of_protection"},
    "material": {"material", "body_material", "construction", "moc"},
    "efficiency": {"efficiency", "efficiency_class", "ie_class"},
    "capacity": {"capacity", "flow", "flow_rate", "discharge", "head"},
    "quantity": {"quantity", "qty"},
    "dimension": {"dimension", "size", "length", "width", "height"},
    "pressure": {"pressure", "working_pressure", "design_pressure"},
    "temperature": {"temperature", "temp", "temp_range", "operating_temperature"},
    "mass": {"mass", "weight"},
}
_GENERIC_ALIASES = {"power", "rating", "capacity", "flow", "head"}

# ATC / wrapper clauses. The first matching rule wins; vendor-only clauses are
# kept out of the technical compliance list.
_COMPLIANCE_RULES: List[Tuple[str, re.Pattern, str]] = [
    ("oem_authorization", re.compile(r"OEM\s+Authori[sz]ation|Manufacturer\s+Authorization|Authorisation\s+Form", re.I), "critical"),
    ("test_certificate", re.compile(r"type\s+test|test\s+certificate|BIS\s+licen[cs]e|ISO\s*9001|approval\s+certificate", re.I), "major"),
    ("warranty", re.compile(r"\bwarranty\b|\bguarantee\b", re.I), "major"),
    ("make_restriction", re.compile(r"\bmake\b|\bbrand\b|only\s+OEMs?|OEM\s+of\s+the\s+offered", re.I), "critical"),
    ("inspection", re.compile(r"pre-?dispatch|post[ -]?receipt\s+inspection|inspection\s+agency", re.I), "major"),
    ("sample_approval", re.compile(r"advance\s+sample|sample.*approv", re.I), "minor"),
    ("service_support", re.compile(r"service\s+cent(?:re|er)|toll[ -]?free|telephone\s+support", re.I), "minor"),
    ("packing", re.compile(r"packing\s+material|original\s+packing", re.I), "minor"),
]
_VENDOR_RULES: List[Tuple[str, re.Pattern]] = [
    ("turnover", re.compile(r"turn\s*over|turnover", re.I)),
    ("experience", re.compile(r"years?\s+of\s+(?:past\s+)?experience|experience\s+criteria|past\s+performance", re.I)),
    ("mse", re.compile(r"\bMSE\b|MSME|NSIC|Udyam", re.I)),
    ("startup", re.compile(r"startup", re.I)),
    ("local_content", re.compile(r"local\s+content|make\s+in\s+india|MII\b|Class\s*[12]\s+local", re.I)),
    ("net_worth", re.compile(r"net\s+worth", re.I)),
    ("tax", re.compile(r"\bGST\b|\bPAN\b", re.I)),
]


# ── Small helpers ─────────────────────────────────────────────────────────


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _normalise_unit(unit: Any) -> str:
    value = _clean(unit).lower().replace("³", "3").replace("²", "2").replace("°", "")
    value = value.replace(" ", "")
    return value


def _dimension(unit: Any) -> Optional[str]:
    entry = _UNIT_LOOKUP.get(_normalise_unit(unit))
    return entry[0] if entry else None


def _base(number: float, unit: Any) -> Optional[Tuple[str, float]]:
    entry = _UNIT_LOOKUP.get(_normalise_unit(unit))
    if entry is None:
        return None
    dim, factor = entry
    return dim, number * factor


def _display_unit(unit: Any, dimension: Optional[str]) -> str:
    raw = _clean(unit)
    if raw and raw.upper() != "NOT_FOUND":
        return raw
    return _BASE_UNIT.get(dimension or "", "")


def _words(text: Any) -> set:
    return set(re.findall(r"[a-z0-9]+", _clean(text).lower())) - _STOP_WORDS


def _citation(source: Any, document: str = "") -> Dict[str, Any]:
    source = source if isinstance(source, dict) else {}
    return {
        "document": source.get("document") or document,
        "page": _page(source.get("page")),
        "row": source.get("row"),
        "clause": source.get("clause") or "",
        "exact_text": source.get("exact_text") or "",
        "chunk_id": source.get("chunk_id") or "",
    }


def _page(value: Any) -> int:
    match = re.search(r"\d+", str(value or ""))
    return int(match.group(0)) if match else 0


def _decimal(value: Any) -> Optional[Decimal]:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return number if number.is_finite() else None


def _canonical_param(key: Any, value: Any) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", _clean(key).lower()).strip("_")
    for canonical, aliases in _PARAM_ALIASES.items():
        if normalized in aliases:
            if canonical in _GENERIC_ALIASES:
                break
            return canonical
    for match in _NUM_UNIT_RE.finditer(_clean(value)):
        dim = _dimension(match.group(2))
        if dim:
            return _PARAM_BY_DIMENSION.get(dim, "value")
    for canonical, aliases in _PARAM_ALIASES.items():
        if normalized in aliases:
            return canonical
    return normalized or "value"


def _direction(text: str) -> str:
    if _MIN_RE.search(text):
        return "min"
    if _MAX_RE.search(text):
        return "max"
    return "exact"


def _unit_after(text: str, position: int) -> str:
    tail = text[position:].strip().lstrip(":").strip()
    match = re.match(r"(kg/cm2|m3/hr|m3/h|m³/h|l/min|l/s|°c|degc|[a-zA-Zµ°/³²%]+)", tail)
    if not match:
        return ""
    unit = match.group(1)
    return _display_unit(unit, _dimension(unit))


def _assertion(
    source_type: str,
    parameter: str,
    value: Any,
    unit: Any,
    direction: str,
    raw_value: Any,
    source: Dict[str, Any],
    *,
    dimension: Optional[str] = None,
    base_value: Optional[float] = None,
    base_low: Optional[float] = None,
    base_high: Optional[float] = None,
) -> Dict[str, Any]:
    return {
        "source_type": source_type,
        "parameter": parameter,
        "value": str(value),
        "unit": _display_unit(unit, dimension),
        "direction": direction,
        "raw_value": _clean(raw_value),
        "dimension": dimension,
        "base_value": base_value,
        "base_low": base_low,
        "base_high": base_high,
        "source": dict(source),
    }


def _numeric_assertions(raw: Any, declared_key: Any = "") -> List[Dict[str, Any]]:
    text = _clean(raw)
    if not text or text.upper() == "NOT_FOUND":
        return []
    declared = _canonical_param(declared_key, text) if declared_key else ""
    direction = _direction(text)

    range_match = _RANGE_RE.search(text)
    if range_match:
        low = range_match.group(1) or range_match.group(3)
        high = range_match.group(2) or range_match.group(4)
        unit = _unit_after(text, range_match.end())
        dim = _dimension(unit)
        parameter = declared if declared and _PARAM_DIMENSION.get(declared) == dim else (
            _PARAM_BY_DIMENSION.get(dim or "", declared or "value")
        )
        low_base = _base(float(low), unit)
        high_base = _base(float(high), unit)
        return [_assertion(
            "", parameter, f"{low}-{high}", unit, "range", text, {},
            dimension=dim,
            base_low=low_base[1] if low_base else None,
            base_high=high_base[1] if high_base else None,
        )]

    assertions: List[Dict[str, Any]] = []
    for match in _NUM_UNIT_RE.finditer(text):
        number = float(match.group(1))
        unit = _display_unit(match.group(2), _dimension(match.group(2)))
        dim = _dimension(unit)
        parameter = declared if declared and _PARAM_DIMENSION.get(declared) == dim else (
            _PARAM_BY_DIMENSION.get(dim or "", declared or "value")
        )
        base = _base(number, unit)
        assertions.append(_assertion(
            "", parameter, f"{number:g}", unit, direction, text, {},
            dimension=dim,
            base_value=base[1] if base else None,
        ))

    # Declared numeric parameter with no explicit unit, e.g. voltage: "415".
    if not assertions and declared in _PARAM_DIMENSION:
        bare = re.fullmatch(r"\s*(?:≥|>=|≤|<=|min\.?|max\.?)?\s*(\d+(?:\.\d+)?)\s*", text, re.I)
        if bare:
            dim = _PARAM_DIMENSION[declared]
            unit = _BASE_UNIT.get(dim, "")
            base = _base(float(bare.group(1)), unit)
            assertions.append(_assertion(
                "", declared, bare.group(1), unit, direction, text, {},
                dimension=dim,
                base_value=base[1] if base else None,
            ))

    ip = _IP_RE.search(text)
    if ip:
        assertions.append(_assertion(
            "", "protection", f"IP{ip.group(1)}", "", "exact", text, {},
        ))
    poles = _POLES_RE.search(text)
    if poles and not any(a["parameter"] == "poles" for a in assertions):
        assertions.append(_assertion(
            "", "poles", poles.group(1), "poles", "exact", text, {}, dimension="count",
        ))
    efficiency = _IE_RE.search(text)
    if efficiency:
        assertions.append(_assertion(
            "", "efficiency_class", f"IE{efficiency.group(1)}", "", "exact", text, {},
        ))

    if not assertions:
        # Text requirement (material grade, standard, make, etc.).
        assertions.append(_assertion(
            "", declared or "value", text, "", "any", text, {},
        ))
    return assertions


def _spec_assertions(spec: Dict[str, Any], document: str) -> List[Dict[str, Any]]:
    source = _citation(spec.get("source"), document)
    assertions: List[Dict[str, Any]] = []
    specs_map = spec.get("specs") or {}
    if not isinstance(specs_map, dict):
        specs_map = {"specification": specs_map}
    for key, raw in specs_map.items():
        for assertion in _numeric_assertions(raw, key):
            assertion["source_type"] = "specification"
            assertion["source"] = dict(source)
            assertion["declared_parameter"] = str(key)
            assertions.append(assertion)
    return assertions


def _item_assertions(item: Dict[str, Any], document: str = "") -> List[Dict[str, Any]]:
    source = item.get("source") or {}
    citation = _citation(source, document)
    source_type = item.get("source_type") or (
        "boq" if str(source.get("document", "")).lower().endswith((".csv", ".tsv")) else "wrapper"
    )
    assertions = [_assertion(
        source_type, "item_category", item.get("description", ""), "", "any",
        item.get("description", ""), citation,
    )]
    quantity = _decimal(item.get("quantity"))
    if quantity is not None:
        assertions.append(_assertion(
            source_type, "quantity", format(quantity, "f"), item.get("unit", ""),
            "exact", f"{item.get('quantity')} {item.get('unit', '')}".strip(), citation,
        ))
    for assertion in _numeric_assertions(item.get("description", "")):
        # A description with no measurable attribute is already represented by
        # the item_category assertion; do not turn it into a fake requirement.
        if assertion["parameter"] == "value" and assertion.get("dimension") is None:
            continue
        assertion["source_type"] = source_type
        assertion["source"] = dict(citation)
        assertions.append(assertion)
    return assertions


# ── Comparison ────────────────────────────────────────────────────────────


def _interval(assertion: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    dimension = assertion.get("dimension")
    if not dimension:
        return None
    if assertion.get("direction") == "range" and assertion.get("base_low") is not None:
        return (float(assertion["base_low"]), float(assertion["base_high"]))
    value = assertion.get("base_value")
    if value is None:
        return None
    value = float(value)
    if assertion.get("direction") == "min":
        return (value, math.inf)
    if assertion.get("direction") == "max":
        return (-math.inf, value)
    tolerance = _EXACT_TOLERANCE if dimension != "percentage" else 0.0
    return (value * (1 - tolerance), value * (1 + tolerance))


def _numeric_compatible(left: Dict[str, Any], right: Dict[str, Any]) -> Optional[bool]:
    if left.get("dimension") != right.get("dimension") or not left.get("dimension"):
        return None
    a, b = _interval(left), _interval(right)
    if a is None or b is None:
        return None
    return max(a[0], b[0]) <= min(a[1], b[1])


def _text_compatible(left: Dict[str, Any], right: Dict[str, Any]) -> Tuple[bool, bool]:
    """Return (compatible, only_wording_differs)."""
    a = _clean(left.get("value")).lower()
    b = _clean(right.get("value")).lower()
    if a == b:
        return True, False
    if fuzz.ratio(a, b) >= _TEXT_EQUIVALENT:
        return True, True
    return False, False


def _severity(parameter: str, status: str, component: str) -> str:
    if status == "unmapped":
        return "critical"
    if status != "conflict":
        return "info"
    if parameter in _CRITICAL_PARAMS:
        return "critical"
    if parameter in _ELECTRICAL_PARAMS and _MOTOR_RE.search(component):
        return "critical"
    return "major"


def _compare_group(
    parameter: str,
    assertions: List[Dict[str, Any]],
    component: str,
) -> Tuple[str, str, bool, str]:
    """Return (status, severity, blocking, reason) for one parameter group."""
    spec_side = [a for a in assertions if a["source_type"] == "specification"]
    other_side = [a for a in assertions if a["source_type"] != "specification"]

    if not spec_side and other_side:
        return "additional", "info", False, "Only the BOQ/wrapper states this parameter."
    if spec_side and not other_side:
        return "additional", "info", False, "Specification adds this requirement; the BOQ does not state it."
    if not spec_side and not other_side:
        return "additional", "info", False, ""

    reason = ""
    wording = False
    conflict = False
    for left in spec_side:
        for right in other_side:
            numeric = _numeric_compatible(left, right)
            if numeric is None:
                compatible, differs = _text_compatible(left, right)
                wording = wording or differs
                conflict = conflict or not compatible
                if not compatible:
                    reason = f"Specification says '{left['value']}' but the item source says '{right['value']}'."
            else:
                if not numeric:
                    conflict = True
                    reason = (
                        f"Normalised values do not agree: specification {left['value']} {left['unit']} "
                        f"vs item source {right['value']} {right['unit']}."
                    )
    if conflict:
        severity = _severity(parameter, "conflict", component)
        return "conflict", severity, severity == "critical", reason
    if wording:
        return "consistent", "minor", False, "Wording differs but the meaning is equivalent."
    return "consistent", "info", False, ""


def _category_status(component: str, description: str) -> Tuple[str, str, bool, str]:
    component_words = _words(component)
    description_words = _words(description)
    if not component_words or not description_words:
        return "additional", "info", False, "Item category could not be compared."
    if _clean(component).lower() == _clean(description).lower():
        return "consistent", "info", False, ""
    if component_words & description_words:
        return "refines", "info", False, "The product name is refined between the sources."
    # Category differences between spec component and item description are noted as major for review, but do not block technical evaluation
    return "conflict", "major", False, (
        f"Specification component '{component}' does not match item '{description}'."
    )


# ── Requirement assembly ──────────────────────────────────────────────────


def _unique_citations(assertions: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    citations = []
    for assertion in assertions:
        source = assertion.get("source") or {}
        key = (source.get("document"), source.get("page"), source.get("row"), source.get("chunk_id"))
        if key in seen:
            continue
        seen.add(key)
        citations.append(dict(source))
    return citations


def _make_requirement(
    component: str,
    boq_item_id: str,
    parameter: str,
    status: str,
    severity: str,
    blocking: bool,
    assertions: List[Dict[str, Any]],
    reason: str,
    *,
    resolution: Optional[str] = None,
) -> Dict[str, Any]:
    spec_side = [a for a in assertions if a["source_type"] == "specification"]
    canonical = (spec_side or assertions or [{}])[0]
    if status == "conflict":
        canonical_value = None
    else:
        canonical_value = canonical.get("value")

    value = canonical.get("value", "")
    unit = canonical.get("unit", "")
    direction = canonical.get("direction", "any")
    text_value = " ".join(part for part in (value, unit) if part and part != "NOT_FOUND").strip()
    source = (spec_side or assertions or [{}])[0].get("source", {})
    return {
        "requirement_id": "",
        "boq_item_id": boq_item_id,
        "component": component,
        "parameter": parameter,
        "text": f"{component}: {parameter} = {text_value}".strip(),
        "value": value,
        "unit": unit or "NOT_FOUND",
        "direction": direction,
        "kind": "technical",
        "status": status,
        "severity": severity,
        "blocking": blocking,
        "canonical_value": canonical_value,
        "resolution": resolution,
        "assertions": assertions,
        "source": dict(source),
        "citations": _unique_citations(assertions),
        "reason": reason,
    }


def _merge_assertions(assertions: List[Dict[str, Any]]) -> "OrderedDict[str, List[Dict[str, Any]]]":
    groups: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()
    for assertion in assertions:
        groups.setdefault(assertion["parameter"], []).append(assertion)
    return groups


def _reconcile_item(
    item: Dict[str, Any],
    specs: List[Dict[str, Any]],
    document: str,
) -> Tuple[List[Dict[str, Any]], bool]:
    item_id = str(item.get("item_id") or "")
    description = _clean(item.get("description"))
    components = [_clean(spec.get("component")) for spec in specs]
    component = next((value for value in components if value), description)
    requirements: List[Dict[str, Any]] = []
    blocking = False

    item_claims = _item_assertions(item, document)
    spec_claims: List[Dict[str, Any]] = []
    for spec in specs:
        spec_claims.extend(_spec_assertions(spec, document))

    # Product-category agreement (Motor vs Induction Motor, etc.).
    if components:
        category_assertions = [
            _assertion("specification", "item_category", value, "", "any", value, _citation(spec.get("source"), document))
            for spec, value in zip(specs, components)
        ]
        category_assertions += [a for a in item_claims if a["parameter"] == "item_category"]
        status, severity, is_blocking, reason = _category_status(component, description)
        requirements.append(_make_requirement(
            component, item_id, "item_category", status, severity, is_blocking,
            category_assertions, reason,
        ))
        blocking = blocking or is_blocking

    groups = _merge_assertions(spec_claims + item_claims)
    for parameter, assertions in groups.items():
        if parameter in {"item_category", "quantity"}:
            continue
        status, severity, is_blocking, reason = _compare_group(parameter, assertions, component)
        requirements.append(_make_requirement(
            component, item_id, parameter, status, severity, is_blocking,
            assertions, reason,
        ))
        blocking = blocking or is_blocking

    # Requirement-driven gaps: equipment with motors without electrical parameters
    # Note: Equipment explicitly marked "without motor" (e.g. pumps sold bare-shaft) do not require motor voltage/power.
    is_without_motor = bool(re.search(r"\bwithout\s+motors?\b", f"{component} {description}", re.I))
    if (_MOTOR_RE.search(component) or _MOTOR_RE.search(description)) and not is_without_motor:
        present = set(groups)
        for parameter, label in (
            ("voltage", "voltage"),
            ("rated_power", "rated power"),
        ):
            if parameter in present:
                continue
            severity = "minor"
            requirements.append(_make_requirement(
                component, item_id, parameter, "additional", severity, False, [],
                f"{component} has no {label} requirement in source documents; other parameters remain matchable.",
                resolution="Informational gap",
            ))
    return requirements, blocking


def _compliance_requirements(
    result: Dict[str, Any],
    wrapper_document: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    scope = result.get("scope_of_work") or {}
    clauses: List[str] = []
    for key in ("deliverables", "exclusions", "references"):
        for clause in scope.get(key) or []:
            text = _clean(clause)
            if text:
                clauses.append(text)

    compliance: List[Dict[str, Any]] = []
    eligibility: List[Dict[str, Any]] = []
    seen_compliance, seen_eligibility = set(), set()

    for clause in clauses:
        mandatory = bool(re.search(r"\bshall\b|\bmust\b|\brequired\b|\bmandatory\b", clause, re.I))
        source = _citation({"clause": "ATC / bid conditions", "exact_text": clause}, wrapper_document)
        for rule_id, pattern, base_severity in _COMPLIANCE_RULES:
            if not pattern.search(clause):
                continue
            key = (rule_id, clause.lower())
            if key in seen_compliance:
                break
            seen_compliance.add(key)
            severity = base_severity if mandatory else "major"
            compliance.append({
                "requirement_id": "",
                "category": rule_id,
                "kind": "compliance",
                "text": clause,
                "mandatory": mandatory,
                "severity": severity,
                "blocking": severity == "critical" and mandatory,
                "source": source,
            })
            break
        for rule_id, pattern in _VENDOR_RULES:
            if not pattern.search(clause):
                continue
            key = (rule_id, clause.lower())
            if key in seen_eligibility:
                continue
            seen_eligibility.add(key)
            eligibility.append({
                "requirement_id": "",
                "category": rule_id,
                "kind": "vendor_eligibility",
                "text": clause,
                "mandatory": mandatory,
                "source": source,
            })

    return compliance, eligibility


def _non_product_spec(spec: Dict[str, Any]) -> Optional[str]:
    blob = " ".join([_clean(spec.get("component")), " ".join(map(str, (spec.get("specs") or {}).keys()))])
    if re.search(r"local\s+content|make\s+in\s+india|MSE|MSME|turnover|experience|warranty|authori[sz]ation|certificate|inspection|sample|payment|EMD|PBG|GST", blob, re.I):
        return "compliance"
    return None


_GENERIC_ATTRIBUTE_RE = re.compile(
    r"\b(material|specification|certification|certified|test|testing|inspection|"
    r"marking|painting|coating|packing|gasket|supplementary\s+requirement|"
    r"hardness|finish|treatment)\b",
    re.I,
)

_VOCABULARY_CACHE: Optional[Any] = None


def _category_vocabulary():
    """Manifest-derived vocabulary, cached for the process."""
    global _VOCABULARY_CACHE
    if _VOCABULARY_CACHE is None:
        _VOCABULARY_CACHE = build_vocabulary(_manufacturers_dir())
    return _VOCABULARY_CACHE


def is_generic_spec(spec: Dict[str, Any]) -> bool:
    """True for attribute/test rows that apply tender-wide, not to one item.

    "SEAT MATERIAL" or "HYDROSTATIC TEST" describes a property of the product
    family; it is not a separate line item, so failing to associate it with one
    BOQ row must not block matching. A component that names a catalogued product
    family stays item-level and unresolved.
    """
    component = _clean(spec.get("component"))
    if not component:
        return False
    if classify_text(component, _category_vocabulary())[0]:
        return False
    return bool(_GENERIC_ATTRIBUTE_RE.search(component))



# ── Public entry point ────────────────────────────────────────────────────


def reconcile_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """Reconcile one extraction payload and return the rule-based table."""
    items = list(result.get("procurement_items") or [])
    specs = list(result.get("technical_specifications") or [])
    attachments = list(result.get("attachments") or [])
    wrapper_document = result.get("wrapper") or next(
        (a.get("file") for a in attachments if a.get("role") == "wrapper"), ""
    )

    notes: List[str] = []
    blockers: List[str] = []
    sources = {
        "specification": False,
        "boq": False,
        "wrapper": bool(wrapper_document or result.get("wrapper_sha256")),
        "boq_items_from": None,
    }

    spec_attachment_missing = any(
        a.get("role") == "specification" and a.get("status") not in (None, "ready")
        for a in attachments
    )
    boq_attachment_missing = any(
        a.get("role") == "boq" and a.get("status") not in (None, "ready")
        for a in attachments
    )
    boq_referenced = result.get("boq_referenced")
    if boq_referenced is None:
        boq_referenced = any(a.get("role") == "boq" for a in attachments)

    wrapper_mode = bool(wrapper_document or result.get("wrapper_sha256") or attachments)
    sources_blocking = False
    if specs and not spec_attachment_missing:
        sources["specification"] = True
    if spec_attachment_missing:
        blockers.append("Specification source is missing; product requirements cannot be verified.")
        notes.append("Specification attachment was referenced but could not be read.")
        sources_blocking = True
    elif wrapper_mode and not specs:
        blockers.append("Specification source is missing; product requirements cannot be verified.")
        notes.append("No specification requirements are available for this tender.")
        sources_blocking = True

    boq_items = [item for item in items if _item_source_type(item) == "boq"]
    wrapper_items = [item for item in items if _item_source_type(item) == "wrapper"]
    sources["boq"] = bool(boq_items) and not boq_attachment_missing
    sources["boq_items_from"] = "boq" if boq_items else ("wrapper" if wrapper_items else None)
    if boq_attachment_missing:
        notes.append("Referenced BOQ source is missing; quantities cannot be verified. Technical specifications remain matchable.")
        if not specs:
            blockers.append("Referenced BOQ source is missing and no specifications are available.")
            sources_blocking = True
    elif wrapper_mode and not items:
        notes.append("No BOQ or wrapper item list is available; quantities cannot be verified. Technical specifications remain matchable.")
        if not specs:
            blockers.append("No BOQ or wrapper item list is available; quantities cannot be verified.")
            sources_blocking = True
    elif not boq_items and wrapper_items:
        notes.append("BOQ not provided; item names and quantities were taken from the wrapper with page citations.")

    # Separate ATC technical/compliance clauses from product requirements.
    compliance_specs = [spec for spec in specs if _non_product_spec(spec)]
    product_specs = [spec for spec in specs if spec not in compliance_specs]
    compliance, eligibility = _compliance_requirements(result, wrapper_document)
    for spec in compliance_specs:
        clause = _clean(spec.get("component"))
        compliance.append({
            "requirement_id": "",
            "category": "spec_classified_compliance",
            "kind": "compliance",
            "text": clause or str(spec.get("specs") or {}),
            "mandatory": True,
            "severity": "major",
            "blocking": False,
            "source": _citation(spec.get("source"), wrapper_document),
        })

    requirements: List[Dict[str, Any]] = []
    blocking = sources_blocking
    tender_level_specs: List[Dict[str, Any]] = []

    # Category vocabulary comes exclusively from the manufacturer manifests.
    vocabulary = build_vocabulary(_manufacturers_dir())
    for item in items:
        if not item.get("category"):
            item["category"], _ = classify_text(item.get("description"), vocabulary)
    tender_category, _ = classify_tender(
        [(item.get("item_type"), item.get("description")) for item in items],
        [
            " ".join([str(spec.get("component") or "")] + [
                f"{key} {value}" for key, value in (spec.get("specs") or {}).items()
            ])
            for spec in product_specs
        ],
        vocabulary,
    )

    # Map every product spec to one item; unmatched specs are `unmapped`.
    item_by_id = {str(item.get("item_id")): item for item in items}
    mapped: Dict[str, List[Dict[str, Any]]] = {key: [] for key in item_by_id}
    unmapped: List[Dict[str, Any]] = []
    for spec in product_specs:
        item_id = str(spec.get("boq_item_id") or "")
        item = item_by_id.get(item_id)
        if spec.get("association_status") == "review":
            item = None
        elif item is None:
            item = _best_item(spec, items)
            item_id = str(item.get("item_id")) if item else ""
        if item is None:
            unmapped.append(spec)
            continue
        mapped.setdefault(str(item.get("item_id")), []).append(spec)

    if items and not product_specs:
        notes.append("No product requirements recovered from the specification; matching would be ungrounded.")

    if items:
        for item_id, item in item_by_id.items():
            item_specs = mapped.get(item_id, [])
            item_type = item.get("item_type", "main_product")
            processing_status = item.get("processing_status", "ready")
            if not item_specs:
                if product_specs:
                    # Ancillary items with reference_unresolved do NOT block matching
                    if item_type in ("spares", "tools", "consumables") and processing_status == "reference_unresolved":
                        # Add as a reference requirement (not a blocker)
                        std_str = "; ".join(item.get("referenced_standards", [])) or "unresolved"
                        requirements.append({
                            "requirement_id": f"ref_{item_id}",
                            "boq_item_id": item_id,
                            "component": _clean(item.get("description", "")),
                            "category": item.get("category", ""),
                            "kind": "reference",
                            "status": "additional",
                            "parameter": "referenced_standard",
                            "value": std_str,
                            "unit": "",
                            "direction": "any",
                            "text": f"Item {item_id} ({item.get('description', '')}) requirements are defined in an unresolved standard: {std_str}",
                            "is_mandatory": False,
                            "blocking": False,
                            "severity": "minor",
                            "canonical_value": std_str,
                            "resolution": "Reference requirement from BOQ item description.",
                            "assertions": [],
                            "citations": [],
                            "reason": "",
                            "source": {"document": "BOQ", "page": 0, "exact_text": item.get("description", "")},
                        })
                    else:
                        notes.append(f"No product requirement recovered for item {item_id}: {item.get('description')}")
                        if processing_status == "ready":
                            item["processing_status"] = "no_requirements"
                continue
            item_requirements, item_blocking = _reconcile_item(item, item_specs, wrapper_document)
            requirements.extend(item_requirements)
            blocking = blocking or item_blocking

        tender_level = [spec for spec in unmapped if is_generic_spec(spec) and spec.get("association_status") != "review"]
        unresolved = [spec for spec in unmapped if spec not in tender_level]
        for spec in tender_level:
            component = _clean(spec.get("component"))
            groups = _merge_assertions(_spec_assertions(spec, wrapper_document))
            for parameter, assertions in groups.items():
                requirements.append(_make_requirement(
                    component, "", parameter, "additional", "info", False, assertions,
                    "Tender-level requirement applies across items; no item association is required.",
                ))

        for spec in unresolved:
            claims = _spec_assertions(spec, wrapper_document)
            # Unmapped specs attach at tender-level with warning status, not blocking
            requirement = _make_requirement(
                _clean(spec.get("component")), "", "item_mapping", "unmapped", "minor", False,
                claims, "Specification could not be associated with any specific BOQ item; kept at tender level.",
            )
            requirements.append(requirement)
            notes.append(f"Unmapped specification (tender-level): {_clean(spec.get('component'))}")
        unmapped = unresolved
        tender_level_specs = tender_level
    else:
        for spec in product_specs:
            claims = _spec_assertions(spec, wrapper_document)
            component = _clean(spec.get("component"))
            groups = _merge_assertions(claims)
            for parameter, assertions in groups.items():
                requirements.append(_make_requirement(
                    component, "", parameter, "additional", "info", False, assertions,
                    "No BOQ or wrapper item list; specification treated as the requirement source.",
                ))

    # Quantity is tender-level: wrapper total vs the sum of line items.
    quantity_requirement = _quantity_requirement(result, items, wrapper_document)
    if quantity_requirement is not None:
        requirements.append(quantity_requirement)
        if quantity_requirement["blocking"]:
            blocking = True
            blockers.append(quantity_requirement["reason"])

    # Every blocking row must explain itself to the operator.
    for requirement in requirements:
        if requirement.get("blocking") and requirement.get("reason"):
            blockers.append(
                f"{requirement.get('parameter')} for {requirement.get('component') or 'item'}: {requirement['reason']}"
            )

    # Requirements inherit the category of their item, else the tender category.
    for requirement in requirements:
        item_id = str(requirement.get("boq_item_id") or "")
        item_category = (item_by_id.get(item_id) or {}).get("category") or ""
        requirement["category"] = item_category or tender_category or ""

    # Stable IDs after ordering.
    for index, requirement in enumerate(requirements, start=1):
        requirement["requirement_id"] = f"CR{index:03d}"
    for index, item in enumerate(compliance, start=1):
        item["requirement_id"] = f"ATC{index:03d}"
    for index, item in enumerate(eligibility, start=1):
        item["requirement_id"] = f"VE{index:03d}"

    if not specs and not requirements:
        notes.append("No technical specifications or requirements were extracted from source documents.")

    counts = {status: 0 for status in STATUSES}
    for requirement in requirements:
        counts[requirement["status"]] = counts.get(requirement["status"], 0) + 1

    conflicts = [requirement for requirement in requirements if requirement["status"] == "conflict"]
    coverage = {
        "items": [
            {
                "item_id": str(item.get("item_id")),
                "description": _clean(item.get("description")),
                "quantity": item.get("quantity"),
                "unit": item.get("unit"),
                "source_type": _item_source_type(item),
                "requirement_ids": [
                    requirement["requirement_id"] for requirement in requirements
                    if str(requirement.get("boq_item_id")) == str(item.get("item_id"))
                ],
            }
            for item in items
        ],
        "mapped_items": sum(1 for item in items if mapped.get(str(item.get("item_id")))),
        "unmapped_specifications": [
            {"component": _clean(spec.get("component")), "source": _citation(spec.get("source"), wrapper_document)}
            for spec in unmapped
        ],
        "tender_level_specifications": [
            {"component": _clean(spec.get("component")), "source": _citation(spec.get("source"), wrapper_document)}
            for spec in tender_level_specs
        ],
    }

    # ── Structured, non-blocking review notes ─────────────────────────────
    review_notes: List[Dict[str, Any]] = []
    seen_reviews = set()

    def add_review(kind: str, message: str, **extra: Any) -> None:
        key = (kind, message)
        if key in seen_reviews:
            return
        seen_reviews.add(key)
        review_notes.append({"type": kind, "message": message, **extra})

    for attachment in result.get("attachments") or []:
        status = str(attachment.get("status") or "ready")
        if status == "ready":
            continue
        role = attachment.get("role") or "attachment"
        add_review(
            "attachment_failure",
            f"{role}: {attachment.get('file') or 'source'} could not be read ({status}).",
            role=role,
            file=attachment.get("file"),
            status=status,
        )
    for reason in blockers:
        add_review("attachment_failure", reason)

    for requirement in requirements:
        kind = requirement.get("kind")
        status = requirement.get("status")
        component = requirement.get("component") or requirement.get("boq_item_id") or "requirement"
        parameter = requirement.get("parameter") or ""
        if kind == "reference":
            add_review(
                "unresolved_standard",
                f"{component}: referenced standard {requirement.get('value') or 'unknown'} is not available.",
                item_id=requirement.get("boq_item_id"),
                standard=requirement.get("value"),
            )
        elif status == "unmapped":
            add_review(
                "unlinked_requirement",
                f"{component}: no BOQ item could be identified; kept at tender level.",
                item_id=requirement.get("boq_item_id"),
            )
        elif status == "conflict":
            values = [
                {
                    "value": assertion.get("value"),
                    "unit": assertion.get("unit"),
                    "source_type": assertion.get("source_type"),
                    "document": (assertion.get("source") or {}).get("document"),
                    "page": (assertion.get("source") or {}).get("page"),
                    "exact_text": (assertion.get("source") or {}).get("exact_text"),
                }
                for assertion in requirement.get("assertions") or []
            ]
            add_review(
                "boq_mismatch",
                f"{component} {parameter}: sources disagree ({requirement.get('reason') or 'values differ'}).",
                item_id=requirement.get("boq_item_id"),
                parameter=parameter,
                values=values,
            )

    for entry in coverage.get("items") or []:
        if entry.get("requirement_ids"):
            continue
        item = item_by_id.get(str(entry.get("item_id"))) or {}
        if item.get("processing_status") == "reference_unresolved":
            continue
        add_review(
            "unlinked_requirement",
            f"Item {entry.get('item_id')} ({entry.get('description') or 'unnamed'}) has no linked requirement.",
            item_id=entry.get("item_id"),
        )

    # Wrapper item lists are shown for human review even when a referenced BOQ
    # failed, but they are never promoted to authoritative quantities.
    candidates = list(result.get("wrapper_item_candidates") or [])
    wrapper_candidates: Dict[str, Any] = {}
    if candidates:
        candidate_total = sum(
            (_decimal(item.get("quantity")) or Decimal(0)) for item in candidates
        )
        authoritative = not boq_referenced and sources["boq_items_from"] == "wrapper"
        wrapper_candidates = {
            "authoritative": authoritative,
            "items": candidates,
            "total_quantity": format(candidate_total, "f"),
            "reason": (
                "Used because the tender references no BOQ attachment."
                if authoritative
                else "Reference only: a BOQ was referenced, so these wrapper values are not authoritative."
            ),
        }
        notes.append(
            f"Wrapper lists {len(candidates)} item(s) totalling {format(candidate_total, 'f')}"
            + ("; used as the item source." if authoritative else "; the referenced BOQ is still required.")
        )

    technical_requirements = [
        r for r in requirements if r.get("kind") != "commercial" and r.get("parameter") != "quantity"
    ]
    has_content = bool(specs or technical_requirements)
    blocked = not has_content or spec_attachment_missing or (boq_referenced and boq_attachment_missing)
    status = "ready" if (has_content and not blocked) else "no_requirements"

    categories = sorted({
        str(requirement.get("category"))
        for requirement in requirements
        if requirement.get("category")
    } | ({tender_category} if tender_category else set()))

    return {
        "status": status,
        "blocked": blocked,
        "blocking_reasons": list(dict.fromkeys(blockers)),
        "category": tender_category or "",
        "categories": categories,
        "review_notes": review_notes,
        "sources": sources,
        "summary": counts,
        "canonical_requirements": requirements,
        "conflicts": conflicts,
        "coverage": coverage,
        "wrapper_candidates": wrapper_candidates,
        "manual_required": list(result.get("manual_attachments") or []),
        "compliance_requirements": compliance,
        "vendor_eligibility": eligibility,
        "notes": notes,
    }


def _item_source_type(item: Dict[str, Any]) -> str:
    if item.get("source_type"):
        return str(item["source_type"])
    document = str((item.get("source") or {}).get("document", "")).lower()
    return "boq" if document.endswith((".csv", ".tsv")) else "wrapper"


def _best_item(spec: Dict[str, Any], items: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    from tender_extraction.tender_bundle import match_spec_item

    item_id, _ = match_spec_item(spec, items)
    return next((item for item in items if str(item.get("item_id")) == item_id), None)


def _quantity_requirement(
    result: Dict[str, Any],
    items: List[Dict[str, Any]],
    wrapper_document: str,
) -> Optional[Dict[str, Any]]:
    check = result.get("quantity_check") or {}
    expected = _decimal(check.get("expected"))
    assertions = []
    total = Decimal(0)
    for item in items:
        quantity = _decimal(item.get("quantity"))
        if quantity is None:
            continue
        total += quantity
        assertions.append(_assertion(
            _item_source_type(item), "quantity", format(quantity, "f"), item.get("unit", ""),
            "exact", f"{item.get('quantity')} {item.get('unit', '')}".strip(),
            _citation(item.get("source"), wrapper_document),
        ))
    citation = result.get("quantity_citation") or {}
    if expected is not None:
        assertions.append(_assertion(
            "wrapper", "quantity", format(expected, "f"), "Nos", "exact",
            f"Total Quantity {format(expected, 'f')}",
            _citation(citation, wrapper_document),
        ))
    if not assertions:
        return None
    if expected is None:
        return None

    if items:
        matches = total == expected
        status = "consistent" if matches else "conflict"
    else:
        status = "additional"
    blocking = status == "conflict"
    reason = "" if status != "conflict" else (
        f"Item quantities total {format(total, 'f')}, but the wrapper states {format(expected, 'f')}."
    )
    requirement = _make_requirement(
        "All items", "", "quantity", status,
        "critical" if blocking else "info", blocking, assertions, reason,
    )
    requirement["kind"] = "commercial"
    return requirement
