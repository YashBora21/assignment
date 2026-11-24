"""Conservative product-row normalization; raw cells remain available for review."""

import re

_MODEL = re.compile(r"^(?:(?:(?:pump|valve|motor)\s+)?(?:model|type|series)(?:\s*(?:no\.?|number|code))?|designation|product\s*(?:code|name)|catalog(?:ue)?\s*(?:no\.?|number))\s*(?:[\[(].*)?$", re.I)
_UNITS = {
    "kw": "kW", "w": "W", "mw": "MW", "v": "V", "kv": "kV",
    "hz": "Hz", "khz": "kHz", "mpa": "MPa", "kpa": "kPa", "pa": "Pa",
    "bar": "bar", "rpm": "rpm", "mm": "mm", "cm": "cm", "kg": "kg",
    "m3/h": "m³/h", "m³/h": "m³/h", "l/min": "L/min", "l/s": "L/s",
    "°c": "°C", "degc": "°C", "psi": "psi", "hp": "hp",
}
_UNIT_RE = re.compile(r"(?<![A-Za-z])(" + "|".join(re.escape(u) for u in sorted(_UNITS, key=len, reverse=True)) + r")(?![A-Za-z])", re.I)


def normalize_units(text):
    text = " ".join(str(text or "").split()).replace("m3/hr", "m³/h").replace("m³/hr", "m³/h")
    return _UNIT_RE.sub(lambda m: _UNITS[m.group().lower()], text)


def normalize_product_table(table):
    """Return normalized rows plus rejection reasons; never infer a model from prose."""
    raw = table.get("raw") or [table.get("headers", [])] + table.get("rows", [])
    result = {**table, "headers": [], "rows": [], "products": [], "rejected": []}
    if len(raw) < 2:
        return result
    width = len(raw[0])
    header_start = 0
    model_columns = []
    for index, header in enumerate(raw[:min(3, len(raw) - 1)]):
        model_columns = [i for i, value in enumerate(header) if _MODEL.fullmatch(" ".join(str(value or "").split()))]
        if model_columns:
            header_start = index
            break
    # shortcut: only explicit product/model columns are trusted; extend with reviewed layouts when needed.
    if len(model_columns) != 1:
        result["rejected"] = [{"reason": "missing or ambiguous model column", "row": row} for row in raw[1:]]
        return result
    model_col = model_columns[0]
    if any(len(row) != width for row in raw[:header_start + 1]):
        result["rejected"] = [{"reason": "misaligned header columns", "row": row} for row in raw]
        return result
    header_count = header_start + 1
    while header_count < min(4, len(raw) - 1):
        candidate = raw[header_count]
        if len(candidate) != width or candidate[model_col] not in (None, ""):
            break
        # Subheaders contain labels/units; numeric specification rows belong to the body.
        if any(re.search(r"\d", normalize_units(cell)) for cell in candidate):
            break
        header_count += 1
    cells = table.get("cells", [])
    headers = []
    for col in range(width):
        parts = []
        for header_index, row in enumerate(raw[:header_count]):
            value = row[col]
            if value is None and header_index < len(cells):
                target = next((geometry[col] for geometry in cells[header_count:] if len(geometry) > col and geometry[col]), None)
                if target:
                    for previous in range(col - 1, -1, -1):
                        box = cells[header_index][previous]
                        if box and box[2] >= target[2] - 0.5:
                            value = row[previous]
                            break
            value = normalize_units(value)
            if value and value not in parts:
                parts.append(value)
        headers.append(" / ".join(parts))
    result["headers"] = headers
    if any(not h for h in headers) or len(set(headers)) != width:
        result["rejected"] = [{"reason": "empty or duplicate flattened headers", "row": row} for row in raw[header_count:]]
        return result
    for row_number, source in enumerate(raw[header_count:], start=header_count):
        reason = ""
        if len(source) != width:
            reason = "misaligned column count"
        row = [normalize_units(cell) for cell in source]
        # Only fill a vertical span proven by PDF cell geometry, never an ordinary blank.
        if not reason and row_number < len(cells):
            for col, value in enumerate(source):
                if value is not None or cells[row_number][col] is not None:
                    continue
                for previous in range(row_number - 1, header_count - 1, -1):
                    box = cells[previous][col]
                    row_boxes = [b for b in cells[row_number] if b]
                    if box and row_boxes and box[3] >= min(b[3] for b in row_boxes) - 0.5:
                        row[col] = normalize_units(raw[previous][col])
                        break
        model = row[model_col] if not reason else ""
        if not reason and (not model or model.upper() in {"N/A", "NOT_FOUND", "-"} or _MODEL.fullmatch(model)):
            reason = "missing model"
        if not reason and (len(model) > 100 or not any(c.isalnum() for c in model)):
            reason = "invalid model"
        specs = {h: value for i, (h, value) in enumerate(zip(headers, row)) if i != model_col and value}
        if not reason and not specs:
            reason = "no specifications"
        if reason:
            result["rejected"].append({"row_number": row_number + 1, "reason": reason, "row": source})
            continue
        # Units in a header also belong in the machine-readable value.
        for header, value in list(specs.items()):
            unit = _UNIT_RE.search(header)
            if unit and re.fullmatch(r"[\d\s.,+−–—/<>≤≥%\-]+", value):
                specs[header] = f"{value} {normalize_units(unit.group())}"
        normalized = [model if i == model_col else specs.get(h, "") for i, h in enumerate(headers)]
        result["rows"].append(normalized)
        result["products"].append({"model": model, "specs": specs, "source_row": row_number + 1})
    return result
