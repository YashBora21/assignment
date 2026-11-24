"""parameters.py — Controlled parameter vocabulary and dimension-aware normalization.

Design rules:
1. Every technical requirement or spec is normalized into a controlled ParameterValue:
   - parameter: controlled name (e.g. 'bore', 'rod_diameter', 'stroke', 'test_pressure', 'pressure_class', 'rated_power', 'voltage')
   - raw_value: original string (e.g. '200 mm', '150 kg/cm²', '600#')
   - numeric_value: normalized Decimal in the parameter's base unit (or None if categorical)
   - unit: normalized unit string
   - unit_class: physical dimension ('length', 'pressure', 'rating', 'power', 'voltage', 'speed', 'flow', 'none')
2. Two values can ONLY be compared if they have the SAME parameter and the SAME unit_class.
   - test_pressure (unit_class: pressure) is never compared with pressure_class (unit_class: rating).
   - bore (unit_class: length) is never compared with rod_diameter (unit_class: length).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional, Set, Tuple

# Physical dimension classes and their conversion factor to the base unit
UNIT_CLASSES: Dict[str, Dict[str, Decimal]] = {
    "length": {
        # base: mm
        "mm": Decimal("1.0"),
        "cm": Decimal("10.0"),
        "m": Decimal("1000.0"),
        "meter": Decimal("1000.0"),
        "meters": Decimal("1000.0"),
        "inch": Decimal("25.4"),
        "inches": Decimal("25.4"),
        "in": Decimal("25.4"),
        '"': Decimal("25.4"),
        "ft": Decimal("304.8"),
    },
    "pressure": {
        # base: bar
        "bar": Decimal("1.0"),
        "barg": Decimal("1.0"),
        "kg/cm2": Decimal("0.980665"),
        "kg/cm²": Decimal("0.980665"),
        "kg/sqcm": Decimal("0.980665"),
        "mpa": Decimal("10.0"),
        "kpa": Decimal("0.01"),
        "pa": Decimal("0.00001"),
        "psi": Decimal("0.0689476"),
    },
    "rating": {
        # base: standard ASME / PN rating number
        "#": Decimal("1.0"),
        "lbs": Decimal("1.0"),
        "class": Decimal("1.0"),
        "rating": Decimal("1.0"),
        "pn": Decimal("1.0"),
        "ibr": Decimal("1.0"),
    },
    "power": {
        # base: kW
        "kw": Decimal("1.0"),
        "w": Decimal("0.001"),
        "hp": Decimal("0.7457"),
        "mw": Decimal("1000.0"),
    },
    "voltage": {
        # base: V
        "v": Decimal("1.0"),
        "volt": Decimal("1.0"),
        "volts": Decimal("1.0"),
        "kv": Decimal("1000.0"),
    },
    "speed": {
        # base: rpm
        "rpm": Decimal("1.0"),
    },
    "flow": {
        # base: m3/h
        "m3/h": Decimal("1.0"),
        "m3/hr": Decimal("1.0"),
        "lps": Decimal("3.6"),
        "l/s": Decimal("3.6"),
        "l/min": Decimal("0.06"),
        "gpm": Decimal("0.2271247"),
    },
}

# Parameter canonical definitions: param_name -> default unit_class
PARAM_CATALOG: Dict[str, str] = {
    "bore": "length",
    "diameter": "length",
    "rod_diameter": "length",
    "piston_rod_diameter": "length",
    "stroke": "length",
    "size": "length",
    "test_pressure": "pressure",
    "design_pressure": "pressure",
    "working_pressure": "pressure",
    "system_oil_pressure": "pressure",
    "pressure_class": "rating",
    "rating": "rating",
    "rated_power": "power",
    "power": "power",
    "voltage": "voltage",
    "speed": "speed",
    "flow_rate": "flow",
}

# Map alias keys to canonical parameter names
PARAM_ALIASES: Dict[str, str] = {
    "bore": "bore",
    "bore_dia": "bore",
    "bore_diameter": "bore",
    "cylinder_bore": "bore",
    "diameter": "diameter",
    "size": "size",
    "rod_dia": "rod_diameter",
    "rod_diameter": "rod_diameter",
    "piston_rod_dia": "rod_diameter",
    "piston_rod_diameter": "rod_diameter",
    "piston_rod_dia_minimum": "rod_diameter",
    "stroke": "stroke",
    "test_pressure": "test_pressure",
    "hydrostatic_test_pressure": "test_pressure",
    "design_pressure": "design_pressure",
    "working_pressure": "working_pressure",
    "operating_pressure": "working_pressure",
    "system_oil_pressure": "system_oil_pressure",
    "pressure_class": "pressure_class",
    "rating": "pressure_class",
    "class": "pressure_class",
    "rated_power": "rated_power",
    "power": "rated_power",
    "motor_rating": "rated_power",
    "kw": "rated_power",
    "voltage": "voltage",
    "speed": "speed",
    "rpm": "speed",
    "flow": "flow_rate",
    "discharge": "flow_rate",
}


@dataclass(frozen=True)
class ParameterValue:
    parameter: str
    raw_value: str
    normalized_value: Optional[Decimal]
    unit: str
    unit_class: str

    def is_compatible(self, other: ParameterValue, tolerance_pct: Decimal = Decimal("2.0")) -> Optional[bool]:
        """Check compatibility between two values of the same parameter.
        
        Returns:
            True: values agree within tolerance
            False: conflict (values contradict)
            None: cannot be numerically compared (different unit classes or categorical)
        """
        if self.parameter != other.parameter:
            return None
        if self.unit_class != other.unit_class or self.unit_class == "none":
            return None
        if self.normalized_value is None or other.normalized_value is None:
            return None
        
        # Pressure rating units: ASME (#) cannot be directly compared numerically to DIN (pn)
        if self.parameter == "pressure_class" and self.unit != other.unit:
            return None

        # Exact or tolerance match
        if self.normalized_value == other.normalized_value:
            return True
        diff = abs(self.normalized_value - other.normalized_value)
        ref = max(abs(self.normalized_value), abs(other.normalized_value))
        if ref == 0:
            return True
        return (diff / ref * 100) <= tolerance_pct


def normalize_unit(unit_str: str) -> Tuple[str, str, Decimal]:
    """Return (canonical_unit, unit_class, factor_to_base)."""
    clean = unit_str.strip().lower()
    clean = clean.replace(" ", "")
    for unit_class, table in UNIT_CLASSES.items():
        if clean in table:
            return clean, unit_class, table[clean]
    return unit_str, "none", Decimal("1.0")


def canonical_parameter_name(param_key: str) -> str:
    norm = param_key.strip().lower().replace("-", "_").replace(" ", "_")
    return PARAM_ALIASES.get(norm, norm)


def extract_parameter_values(text: str, specs_dict: Optional[Dict[str, Any]] = None) -> Dict[str, Set[ParameterValue]]:
    """Extract parameter values from text and structured specs with controlled vocabulary."""
    found: Dict[str, Set[ParameterValue]] = {}

    def _add_val(param: str, raw: str, num: Optional[Decimal], unit: str, uclass: str):
        can_param = canonical_parameter_name(param)
        found.setdefault(can_param, set()).add(
            ParameterValue(
                parameter=can_param,
                raw_value=raw,
                normalized_value=num,
                unit=unit,
                unit_class=uclass
            )
        )

    # 1. Process explicit specs dictionary if provided
    if specs_dict:
        for k, v in specs_dict.items():
            if not v or not isinstance(v, (str, int, float)):
                continue
            v_str = str(v).strip()
            param_name = canonical_parameter_name(k)
            uclass = PARAM_CATALOG.get(param_name, "none")
            
            # Match numeric + unit
            m = re.search(r"(\d+(?:\.\d+)?)\s*([a-zA-Z°#/²µ%]+)?", v_str)
            if m:
                num_str, u_str = m.group(1), m.group(2) or ""
                canon_u, found_uclass, factor = normalize_unit(u_str)
                actual_uclass = found_uclass if found_uclass != "none" else uclass
                if actual_uclass in UNIT_CLASSES and canon_u in UNIT_CLASSES[actual_uclass]:
                    factor = UNIT_CLASSES[actual_uclass][canon_u]
                    try:
                        norm_val = Decimal(num_str) * factor
                        _add_val(param_name, v_str, norm_val, canon_u, actual_uclass)
                        continue
                    except InvalidOperation:
                        pass
            # Avoid categorizing general pressure ratings like PA 46 or PN 40 as bare parameter without numeric
            _add_val(param_name, v_str, None, "", "none")

    # 2. Extract from free-form text using controlled regex patterns
    # Piston Rod Diameter (explicitly separate from main bore)
    for m in re.finditer(r"\bpiston\s+rod\s+(?:diameter|dia)[^:= -]*[:= -]*\s*(\d+(?:\.\d+)?)\s*(mm|cm|inches|inch|in)?\b", text, re.I):
        val, unit = m.group(1), m.group(2) or "mm"
        canon_u, uclass, factor = normalize_unit(unit)
        try:
            _add_val("rod_diameter", f"{val} {unit}", Decimal(val) * factor, canon_u, "length")
        except InvalidOperation:
            pass

    # Cylinder Bore / Diameter (not preceded by piston rod)
    clean_bore_text = re.sub(r"\bpiston\s+rod\s+(?:diameter|dia)[^;,\n]*", "", text, flags=re.I)
    for m in re.finditer(r"\b(?:bore|diameter)\s*(?:mm\s*)?[:= -]*\s*(\d+(?:\.\d+)?)\s*(mm|cm|inches|inch|in)?\b", clean_bore_text, re.I):
        val, unit = m.group(1), m.group(2) or "mm"
        canon_u, uclass, factor = normalize_unit(unit)
        try:
            _add_val("bore", f"{val} {unit}", Decimal(val) * factor, canon_u, "length")
        except InvalidOperation:
            pass

    # Stroke
    for m in re.finditer(r"\bstroke\s*(?:mm\s*)?[:= -]*\s*(\d+(?:\.\d+)?)\s*(mm|cm|inches|inch|in)?\b", text, re.I):
        val, unit = m.group(1), m.group(2) or "mm"
        canon_u, uclass, factor = normalize_unit(unit)
        try:
            _add_val("stroke", f"{val} {unit}", Decimal(val) * factor, canon_u, "length")
        except InvalidOperation:
            pass

    # Size (e.g., 2in, 50mm, 2")
    for m in re.finditer(r"\b(\d+(?:\.\d+)?)\s*(inches|inch|in|\")\b", text, re.I):
        val, unit = m.group(1), m.group(2)
        canon_u, uclass, factor = normalize_unit("in")
        try:
            _add_val("size", f"{val} {unit}", Decimal(val) * factor, canon_u, "length")
        except InvalidOperation:
            pass

    # Test Pressure (kg/cm², bar, MPa)
    for m in re.finditer(r"\btest\s+pressure\s*[:= -]*\s*(\d+(?:\.\d+)?)\s*(kg/cm2|kg/cm²|bar|mpa|psi)\b", text, re.I):
        val, unit = m.group(1), m.group(2)
        canon_u, uclass, factor = normalize_unit(unit)
        try:
            _add_val("test_pressure", f"{val} {unit}", Decimal(val) * factor, canon_u, "pressure")
        except InvalidOperation:
            pass

    # Pressure Class (150#, 300#, 600#, Class 150, PN16, etc.)
    for m in re.finditer(r"(?:\b(?:class|rating)\s*#?\s*(150|300|600|900|1500|2500)\b|\b(150|300|600|900|1500|2500)\s*(?:#|lbs\b|ibr\b|class\b|rating\b))", text, re.I):
        val = m.group(1) or m.group(2)
        if val:
            try:
                _add_val("pressure_class", f"{val}#", Decimal(val), "#", "rating")
            except InvalidOperation:
                pass
    for m in re.finditer(r"\bpn\s*(\d+)\b", text, re.I):
        val = m.group(1)
        try:
            _add_val("pressure_class", f"PN{val}", Decimal(val), "pn", "rating")
        except InvalidOperation:
            pass

    # Power (kW, HP)
    for m in re.finditer(r"\b(\d+(?:\.\d+)?)\s*(kw|hp|mw)\b", text, re.I):
        val, unit = m.group(1), m.group(2)
        canon_u, uclass, factor = normalize_unit(unit)
        try:
            _add_val("rated_power", f"{val} {unit}", Decimal(val) * factor, canon_u, "power")
        except InvalidOperation:
            pass

    # Voltage (V, kV)
    for m in re.finditer(r"\b(\d+(?:\.\d+)?)\s*(v|kv|volts?)\b", text, re.I):
        val, unit = m.group(1), m.group(2)
        canon_u, uclass, factor = normalize_unit(unit)
        try:
            _add_val("voltage", f"{val} {unit}", Decimal(val) * factor, canon_u, "voltage")
        except InvalidOperation:
            pass

    return found
