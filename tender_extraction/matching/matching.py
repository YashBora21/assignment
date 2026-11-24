"""matching.py — Manufacturer matching engine.

Compares every tender requirement against each manufacturer's catalogue corpus
and produces a :class:`MatchReport` (see ``schemas_match.py``).

Contract
--------
* Four match statuses: ``full_match``, ``partial_match``, ``no_match`` and
  ``no_evidence``, plus ``not_applicable`` for buyer/procedure lines.
* ``no_evidence`` means nothing relevant was retrieved, or the cited quote is
  not verbatim on the cited page.  Absence of evidence is never ``no_match``.
* The LLM only extracts a capability value and a verbatim quote.  Code verifies
  the quote against the retrieved page text and applies the operators.
* Manufacturer categories come exclusively from ``data/manufacturers/*/sources.json``
  (see ``categories.py``).  No product family or manufacturer is hardcoded.

Entry point consumed by ``api/matching_adapter.py``::

    generate_match_report(tender_result, *, manufacturers_dir, tender_file)

``judge``, ``corpus_indexer`` and ``retriever_factory`` are injectable so tests
run without a model, a network call, or a Qdrant collection.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from tender_extraction.categories import (
    build_vocabulary,
    classify_text,
    classify_tender,
    normalize_category,
)
from tender_extraction.schemas import Chunk, ChunkMetadata, SourceCitation
from tender_extraction.schemas_match import (
    DECIDED_STATUSES,
    SCORABLE_STATUSES,
    STATUS_WEIGHTS,
    AuditTrail,
    EvidenceRef,
    ItemMatchResult,
    ManufacturerResult,
    MatchReport,
    Requirement,
    RequirementMatch,
)

logger = logging.getLogger(__name__)

_SUPPORTED_SUFFIXES = {".pdf", ".docx", ".jpg", ".jpeg", ".png"}
_NOT_APPLICABLE_KINDS = {"commercial", "legal", "compliance", "reference", "vague", "vendor_eligibility"}
_NOT_APPLICABLE_PARAMETERS = {"quantity", "item_category", "unit", "hsn_code", "gst"}

# ── Units ─────────────────────────────────────────────────────────────────

# dimension -> {unit spelling: factor to the dimension's base unit}
_UNIT_FACTORS: Dict[str, Dict[str, float]] = {
    "pressure": {"bar": 1.0, "barg": 1.0, "kg/cm2": 0.980665, "mpa": 10.0, "kpa": 0.01, "pa": 1e-5, "psi": 0.0689476},
    "length": {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4, "ft": 304.8},
    "flow": {"m3/h": 1.0, "l/s": 3.6, "l/min": 0.06, "gpm": 0.2271247, "lph": 0.001},
    "power": {"kw": 1.0, "w": 0.001, "hp": 0.7457, "mw": 1000.0},
    "temperature": {"c": 1.0},
    "voltage": {"v": 1.0, "kv": 1000.0},
    "frequency": {"hz": 1.0, "khz": 1000.0},
    "speed": {"rpm": 1.0},
    "mass": {"kg": 1.0, "g": 0.001, "t": 1000.0},
    "percent": {"%": 1.0},
    # ASME (#, lbs, class) and DIN (pn) ratings are separate families: 150#
    # and PN16 are not numerically comparable.
    "rating_asme": {"#": 1.0, "lbs": 1.0, "class": 1.0},
    "rating_pn": {"pn": 1.0},
}
_UNIT_LOOKUP: Dict[str, Tuple[str, float]] = {}
for _dim, _units in _UNIT_FACTORS.items():
    for _unit, _factor in _units.items():
        _UNIT_LOOKUP[_unit] = (_dim, _factor)

_UNIT_ALIASES = {
    "kg/cm²": "kg/cm2", "kg/cm^2": "kg/cm2", "kg/sqcm": "kg/cm2", "kg/sq.cm": "kg/cm2",
    "m³/h": "m3/h", "m3/hr": "m3/h", "m3h": "m3/h", "m³/hr": "m3/h",
    "lps": "l/s", "l/sec": "l/s", "lpm": "l/min", "l/m": "l/min",
    "°c": "c", "degc": "c", "degree c": "c", "deg c": "c", "celsius": "c",
    "inch": "in", "inches": "in", '"': "in", "meter": "m", "meters": "m",
    "ton": "t", "tonne": "t", "tonnes": "t", "volts": "v", "volt": "v",
    "lbs": "lbs", "class": "class", "rating": "class",
    "percent": "%", "pct": "%",
}

_NUM_UNIT_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*"
    r"(kg/cm2|kg/cm²|kg/sqcm|m3/hr|m3/h|m³/h|l/min|l/s|lps|gpm|lph|°c|degc|"
    r"kpa|mpa|psi|barg|bar|rpm|khz|hz|kw|mw|hp|kv|mm|cm|inch|inches|in|ft|"
    r"kg|pa|w|v|m|t|g|%|#|pn)?(?![a-zA-Z])",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")

_MIN_RE = re.compile(r"(?:≥|>=|min(?:imum)?\.?|not less than|at ?least)\s*", re.IGNORECASE)
_MAX_RE = re.compile(r"(?:≤|<=|max(?:imum)?\.?|not more than|not exceeding|up ?to)\s*", re.IGNORECASE)
_RANGE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(\d+(?:\.\d+)?)|between\s+(\d+(?:\.\d+)?)\s+and\s+(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_COMMERCIAL_RE = re.compile(
    r"(years?\s+(?:of\s+)?experience|turnover|financial|solvency|authoriz|authoris|"
    r"dealership|local office|registered office|gst|pan\b|company certifi|oem certificate)",
    re.IGNORECASE,
)


def _normalize_unit(unit: Any) -> str:
    clean = str(unit or "").strip().lower()
    clean = re.sub(r"\s+", "", clean)
    clean = clean.replace("²", "2") if clean not in {"kg/cm²", "m³/h"} else clean
    return _UNIT_ALIASES.get(clean, clean)


def _unit_entry(unit: Any) -> Optional[Tuple[str, float]]:
    return _UNIT_LOOKUP.get(_normalize_unit(unit))


def _canonical(number: float, unit: Any) -> Optional[Tuple[str, float]]:
    entry = _unit_entry(unit)
    if entry is None:
        return None
    dimension, factor = entry
    return dimension, number * factor


def _to_unit(number: float, from_unit: Any, to_unit: Any) -> Optional[float]:
    """Convert ``number`` between two units of the same dimension."""
    src = _unit_entry(from_unit)
    dst = _unit_entry(to_unit)
    if src is None or dst is None or src[0] != dst[0]:
        return None
    return number * src[1] / dst[1]


def _parse_requirement_number(req: Requirement) -> Optional[Tuple[float, float, str]]:
    """Return ``(low, high, direction)`` for a numeric requirement."""
    raw = str(req.value or "").strip()
    if not raw or raw.upper() == "NOT_FOUND":
        return None
    if req.direction == "range" or _RANGE_RE.search(raw):
        match = _RANGE_RE.search(raw)
        if match:
            low = float(match.group(1) or match.group(3))
            high = float(match.group(2) or match.group(4))
            return low, high, "range"

    # If direction is unspecified and unit is not_found, the value must be purely
    # numeric (e.g. "15", "-2.5") to qualify as a numeric requirement; alphanumeric
    # strings like "SS316" or "IP65" are categorical.
    unit = _normalize_unit(req.unit)
    is_pure_num = bool(re.fullmatch(r"[-+]?\d+(?:\.\d+)?", raw.replace(",", "")))
    if req.direction not in {"min", "max", "range"} and unit in {"not_found", ""} and not is_pure_num:
        return None

    match = re.search(r"-?\d+(?:\.\d+)?", raw.replace(",", ""))
    if not match:
        return None
    value = float(match.group(0))
    return value, value, req.direction if req.direction in {"min", "max", "exact"} else "exact"


def _parse_value_string(raw: str) -> Tuple[str, str, str]:
    """Return ``(value, unit, direction)`` from a spec value string."""
    text = str(raw or "").strip()
    if not text or text.upper() == "NOT_FOUND":
        return "NOT_FOUND", "NOT_FOUND", "any"
    direction = "any"
    body = text
    if _MIN_RE.search(text):
        direction = "min"
        body = _MIN_RE.sub("", text, count=1).strip()
    elif _MAX_RE.search(text):
        direction = "max"
        body = _MAX_RE.sub("", text, count=1).strip()
    range_match = _RANGE_RE.search(body)
    if range_match:
        low = range_match.group(1) or range_match.group(3)
        high = range_match.group(2) or range_match.group(4)
        unit = _unit_after(body, range_match.end())
        return f"{low}-{high}", unit, "range"
    number = re.match(r"\d+(?:\.\d+)?", body)
    if not number:
        return text, "NOT_FOUND", "any"
    return number.group(0), _unit_after(body, number.end()), direction if direction != "any" else "exact"


def _unit_after(text: str, position: int) -> str:
    tail = text[position:].strip().lstrip(":").strip()
    match = re.match(r"(kg/cm2|kg/cm²|m3/h|m³/h|l/min|l/s|°c|[a-zA-Zµ°/³²%#]+)", tail)
    if not match:
        return "NOT_FOUND"
    unit = _normalize_unit(match.group(1))
    return unit if _unit_entry(unit) else match.group(1)


# ── Requirement splitting ─────────────────────────────────────────────────


def _page_int(value: Any) -> int:
    match = re.search(r"\d+", str(value or ""))
    return int(match.group(0)) if match else 0


def _to_source_citation(raw_source: Any) -> SourceCitation:
    if isinstance(raw_source, SourceCitation):
        return raw_source
    if hasattr(raw_source, "model_dump"):
        raw_source = raw_source.model_dump()
    elif hasattr(raw_source, "dict"):
        raw_source = raw_source.dict()
    if not isinstance(raw_source, dict):
        return SourceCitation()
    return SourceCitation(
        chunk_id=str(raw_source.get("chunk_id") or "NOT_FOUND"),
        page=_page_int(raw_source.get("page")),
        document=str(raw_source.get("document") or ""),
        row=raw_source.get("row"),
        clause=str(raw_source.get("clause") or "NOT_FOUND"),
        exact_text=str(raw_source.get("exact_text") or "NOT_FOUND"),
    )


def split_requirements(tender_result: Dict[str, Any]) -> List[Requirement]:
    """One requirement per reconciled (component, parameter, value)."""
    canonical = tender_result.get("canonical_requirements") or []
    requirements: List[Requirement] = []
    if canonical:
        for item in canonical:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "technical")
            parameter = str(item.get("parameter") or "NOT_FOUND")
            if kind in {"commercial", "compliance", "legal"}:
                continue
            if parameter in _NOT_APPLICABLE_PARAMETERS:
                continue
            if item.get("status") == "conflict" or item.get("canonical_value") is None and item.get("value") in (None, ""):
                continue
            direction = item.get("direction") if item.get("direction") in {"exact", "min", "max", "range", "any"} else "any"
            requirements.append(Requirement(
                requirement_id=str(item.get("requirement_id") or f"CR{len(requirements) + 1:03d}"),
                boq_item_id=str(item.get("boq_item_id") or ""),
                category=normalize_category(item.get("category")),
                text=str(item.get("text") or f"{item.get('component') or ''}: {parameter}"),
                parameter=parameter,
                value=str(item.get("value") if item.get("value") is not None else "NOT_FOUND"),
                unit=str(item.get("unit") or "NOT_FOUND"),
                direction=direction,
                kind=kind if kind in {"technical", "commercial", "legal", "compliance", "vague", "reference"} else "technical",
                is_mandatory=bool(item.get("mandatory")),
                source=_to_source_citation(item.get("source")),
            ))
        return requirements

    specs = tender_result.get("technical_specifications") or []
    for index, spec in enumerate(specs, start=1):
        component = str(spec.get("component") or spec.get("item_name") or f"Spec {index}").strip()
        specs_map = spec.get("specs") or {}
        if not specs_map and spec.get("specification_text"):
            specs_map = {"specification": spec["specification_text"]}
        for param_index, (parameter, raw_value) in enumerate(specs_map.items(), start=1):
            value, unit, direction = _parse_value_string(str(raw_value))
            text = f"{component}: {parameter} = {raw_value}"
            kind = "commercial" if _COMMERCIAL_RE.search(text) else "technical"
            requirements.append(Requirement(
                requirement_id=f"R{index:03d}.{param_index}",
                boq_item_id=str(spec.get("boq_item_id") or ""),
                category=normalize_category(spec.get("category")),
                text=text,
                parameter=str(parameter),
                value=value,
                unit=unit,
                direction=direction,
                kind=kind,
                source=_to_source_citation(spec.get("source")),
            ))
    return requirements


# ── Manufacturer manifests ────────────────────────────────────────────────


def _manifest_category(manifest: Dict[str, Any]) -> str:
    category = normalize_category(manifest.get("category"))
    if category and category not in {"not-found", "not_found", "unknown", "none"}:
        return category
    for document in manifest.get("documents") or []:
        if not isinstance(document, dict):
            continue
        category = normalize_category(document.get("category"))
        if category and category not in {"not-found", "not_found", "unknown", "none"}:
            return category
    return ""


def load_manufacturers(manufacturers_dir: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Read one folder per manufacturer; return ``(manufacturers, notes)``."""
    root = Path(manufacturers_dir)
    notes: List[str] = []
    manufacturers: List[Dict[str, Any]] = []
    if not root.is_dir():
        return [], [f"Manufacturer corpus directory not found: {root}"]

    for sub in sorted(root.iterdir()):
        if not sub.is_dir() or sub.name.startswith(("_", ".")):
            continue
        manifest: Dict[str, Any] = {}
        manifest_path = sub / "sources.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001 - reported as a note
                notes.append(f"{sub.name}: sources.json unreadable ({exc}).")
        else:
            notes.append(f"{sub.name}: no sources.json — evidence provenance unverified.")

        docs_by_file = {d.get("file"): d for d in manifest.get("documents", []) if isinstance(d, dict)}
        files = [f for f in sorted(sub.iterdir()) if f.suffix.lower() in _SUPPORTED_SUFFIXES]
        documents = []
        categories: Set[str] = set()
        for file in files:
            meta = docs_by_file.get(file.name, {})
            category = normalize_category(meta.get("category"))
            if category:
                categories.add(category)
            documents.append({
                "file": file.name,
                "path": str(file),
                "title": meta.get("title") or file.name,
                "url": meta.get("url") or "NOT_FOUND",
                "category": category,
            })
        manifest_category = _manifest_category(manifest)
        if manifest_category:
            categories.add(manifest_category)
        if not documents:
            notes.append(f"{sub.name}: no catalogue documents found — skipped.")
            continue
        manufacturers.append({
            "slug": sub.name,
            "display_name": manifest.get("display_name") or sub.name,
            "category": manifest_category or (sorted(categories)[0] if categories else ""),
            "categories": sorted(categories),
            "documents": documents,
        })
    return manufacturers, notes


# ── Persisted Qdrant retrieval ────────────────────────────────────────────


class _PersistedCategoryRetriever:
    """Hybrid retrieval against an already-ingested ``cat_<category>`` collection.

    The corpus is ingested once by ``scripts/ingest_manufacturers.py``.  Matching
    must not re-parse every PDF on every run, so this retriever reads the
    persisted Qdrant collection directly.  Payloads carry document, page and
    manufacturer, which is all the evidence verifier needs.
    """

    def __init__(self, category: str, persist_dir: Optional[str] = None):
        from tender_extraction.retrieval import get_qdrant_client, ensure_payload_indexes

        self.category = category
        self.collection = f"cat_{category}"
        self._client = get_qdrant_client(persist_dir)
        existing = {item.name for item in self._client.get_collections().collections}
        if self.collection not in existing:
            raise RuntimeError(f"Qdrant collection '{self.collection}' is not indexed.")
        ensure_payload_indexes(self._client, self.collection)
        self._items: Optional[List[Dict[str, Any]]] = None
        self._bm25 = None
        self._by_manufacturer: Dict[str, List[int]] = {}

    def _ensure_index(self) -> None:
        if self._items is not None:
            return
        from rank_bm25 import BM25Okapi

        items: List[Dict[str, Any]] = []
        offset = None
        while True:
            points, offset = self._client.scroll(
                collection_name=self.collection,
                limit=1000,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for point in points:
                payload = dict(point.payload or {})
                items.append({"id": point.id, "text": str(payload.get("text") or ""), "payload": payload})
            if offset is None:
                break
        self._items = items
        self._bm25 = BM25Okapi([item["text"].lower().split() for item in items])
        for index, item in enumerate(items):
            slug = str(item["payload"].get("manufacturer") or "")
            self._by_manufacturer.setdefault(slug, []).append(index)

    def retrieve(
        self,
        query: str,
        top_k: int = 6,
        payload_filter: Optional[Dict[str, Any]] = None,
        embedding: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        self._ensure_index()
        assert self._items is not None and self._bm25 is not None
        manufacturer = str((payload_filter or {}).get("manufacturer") or "")
        allowed = self._by_manufacturer.get(manufacturer)
        if not allowed:
            return []

        from qdrant_client.models import FieldCondition, Filter, MatchValue

        if embedding is None:
            from tender_extraction.model_runtime import get_embedding_model
            embedding = get_embedding_model().encode([query], normalize_embeddings=True)[0]
        query_vector = embedding.tolist() if hasattr(embedding, "tolist") else list(embedding)

        must = [FieldCondition(key="manufacturer", match=MatchValue(value=manufacturer))]
        category = (payload_filter or {}).get("category")
        if category:
            must.append(FieldCondition(key="category", match=MatchValue(value=category)))
        dense_hits = self._client.query_points(
            collection_name=self.collection,
            query=query_vector,
            limit=top_k * 8,
            query_filter=Filter(must=must),
        ).points

        query_tokens = query.lower().split()
        scores = self._bm25.get_scores(query_tokens)
        bm25_ranked = sorted(allowed, key=lambda idx: float(scores[idx]), reverse=True)[: top_k * 8]

        rrf_k = 60
        fused: Dict[int, float] = {}
        for rank, point in enumerate(dense_hits, start=1):
            index = int(point.id)
            if index in allowed:
                fused[index] = fused.get(index, 0.0) + 1.0 / (rrf_k + rank)
        for rank, index in enumerate(bm25_ranked, start=1):
            fused[index] = fused.get(index, 0.0) + 1.0 / (rrf_k + rank)

        results: List[Dict[str, Any]] = []
        seen_parents: Set[str] = set()
        for index, score in sorted(fused.items(), key=lambda item: item[1], reverse=True):
            if index >= len(self._items):
                continue
            payload = self._items[index]["payload"]
            parent_id = str(payload.get("parent_id") or self._items[index]["id"])
            if parent_id in seen_parents:
                continue
            seen_parents.add(parent_id)
            chunk = Chunk(
                chunk_id=parent_id,
                text=self._items[index]["text"],
                metadata=ChunkMetadata(
                    section=str(payload.get("subtype") or "Unknown"),
                    chunk_type="table" if payload.get("table_id") else "paragraph",
                    page=int(payload.get("page") or 0),
                    document=str(payload.get("document") or ""),
                    table_id=payload.get("table_id"),
                ),
            )
            results.append({"chunk": chunk, "score": float(score)})
            if len(results) >= top_k:
                break
        return results

    def close(self) -> None:
        # Do not close the shared singleton client
        pass


def _default_corpus_indexer(manufacturers_dir: str, category_filter: Optional[str] = None):
    """Production default: manifests only, no PDF parsing.

    The indexed Qdrant collections are the corpus.  Tests inject a corpus
    indexer that returns chunks; production reads the persisted collections in
    :func:`_default_retriever_factory`.
    """
    return {}, {}, []


def _default_retriever_factory(category: str, chunks: List[Chunk], payloads: Dict[str, Dict[str, Any]]):
    if chunks:
        from tender_extraction.retrieval import HybridRetriever

        retriever = HybridRetriever(persist_dir=":memory:")
        retriever.build_index(chunks, collection_name=f"cat_{category}", payload_by_chunk=payloads)
        return retriever
    return _PersistedCategoryRetriever(category)


# ── Numeric comparison ────────────────────────────────────────────────────


def _parameter_words(req: Requirement) -> List[str]:
    parameter = str(req.parameter or "").lower().replace("_", " ")
    words = [word for word in re.split(r"\W+", parameter) if len(word) > 2]
    component = str(req.text or "").split(":", 1)[0]
    words.extend(word for word in re.split(r"\W+", component.lower()) if len(word) > 3)
    return list(dict.fromkeys(words))


def _quote_around(text: str, position: int, radius: int = 180) -> str:
    """Verbatim line (or window) containing ``position``."""
    start = text.rfind("\n", 0, position) + 1
    end = text.find("\n", position)
    if end < 0:
        end = len(text)
    line = text[start:end].strip()
    if 20 <= len(line) <= 600:
        return line
    return text[max(0, position - radius):position + radius].strip()


def _capability_numbers(text: str, req: Requirement) -> List[Tuple[float, str, int]]:
    requirement_unit = _normalize_unit(req.unit)
    requirement_dimension = _unit_entry(requirement_unit)
    found: List[Tuple[float, str, int]] = []
    for match in _NUM_UNIT_RE.finditer(text):
        unit = _normalize_unit(match.group(2) or "")
        if requirement_dimension is not None:
            entry = _unit_entry(unit)
            if entry is None or entry[0] != requirement_dimension[0]:
                continue
        elif unit and unit != requirement_unit:
            continue
        found.append((float(match.group(1)), unit, match.start()))
    return found


def _compare_numeric(req: Requirement, hits: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Decide a numeric requirement from verbatim datasheet numbers."""
    parsed = _parse_requirement_number(req)
    if parsed is None:
        return None
    requirement_low, requirement_high, direction = parsed
    requirement_unit = _normalize_unit(req.unit)
    parameter_words = _parameter_words(req)

    best: Optional[Dict[str, Any]] = None
    best_key: Optional[Tuple[int, int]] = None
    for hit in hits:
        chunk = hit["chunk"]
        text = chunk.text
        lowered = text.lower()
        keyword_positions = [lowered.find(word) for word in parameter_words if lowered.find(word) >= 0]
        if parameter_words and not keyword_positions:
            # The passage does not discuss this parameter at all.
            continue
        keyword_position = min(keyword_positions) if keyword_positions else -1
        for number, unit, position in _capability_numbers(text, req):
            if requirement_unit and unit:
                capability_value = _to_unit(number, unit, requirement_unit)
                if capability_value is None:
                    continue
            else:
                capability_value = number

            if direction == "min":
                satisfies = capability_value >= requirement_low
                partial = capability_value >= requirement_low * 0.8
            elif direction == "max":
                satisfies = capability_value <= requirement_high
                partial = capability_value <= requirement_high * 1.2
            elif direction == "range":
                satisfies = requirement_low <= capability_value <= requirement_high
                partial = capability_value >= requirement_low * 0.8 and capability_value <= requirement_high * 1.2
            else:
                reference = max(abs(requirement_low), 1e-9)
                satisfies = abs(capability_value - requirement_low) <= 0.02 * reference
                partial = abs(capability_value - requirement_low) <= 0.20 * reference

            if not satisfies and not partial:
                # Evidence exists but contradicts the requirement.
                status = "no_match"
            elif satisfies:
                status = "full_match"
            else:
                status = "partial_match"

            proximity = abs(position - keyword_position) if keyword_position >= 0 else 10_000 + position
            key = (2 if status == "full_match" else 1 if status == "partial_match" else 0, -proximity)
            if best_key is not None and key <= best_key:
                continue
            best_key = key
            best = {
                "status": status,
                "capability": f"{number:g} {unit}".strip(),
                "confidence": 0.9 if keyword_position >= 0 else 0.7,
                "needs_review": keyword_position < 0,
                "reason": (
                    f"Datasheet states {number:g} {unit or ''}; tender requires "
                    f"{direction} {req.value} {req.unit if req.unit != 'NOT_FOUND' else ''}."
                ).strip(),
                "evidence": [EvidenceRef(
                    document=chunk.metadata.document or "NOT_FOUND",
                    page=chunk.metadata.page,
                    quote=_quote_around(text, position),
                    chunk_id=chunk.chunk_id,
                )],
            }
    return best


# ── LLM extraction (value + verbatim quote only) ──────────────────────────

_LLM_CACHE: Dict[str, Dict[str, Any]] = {}
_LLM_CALLS = 0
_LLM_CACHE_HITS = 0
_LLM_LOCK = threading.Lock()
PROMPT_VERSION = "match-v3"
MAX_LLM_CALLS_PER_TENDER = int(os.getenv("MATCH_MAX_LLM_CALLS", "15"))


def _reset_llm_budget() -> None:
    global _LLM_CALLS, _LLM_CACHE_HITS
    with _LLM_LOCK:
        _LLM_CALLS = 0
        _LLM_CACHE_HITS = 0


def _llm_extract(req: Requirement, hits: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Ask the LLM for a capability value and a verbatim quote.

    The LLM never decides the status and never invents a quote.  A quote that
    is not found verbatim in the retrieved text is discarded by the caller.
    """
    global _LLM_CALLS, _LLM_CACHE_HITS

    excerpts = []
    for index, hit in enumerate(hits[:6], start=1):
        chunk = hit["chunk"]
        excerpts.append(
            f"[{index}] document={chunk.metadata.document or 'unknown'} page={chunk.metadata.page}\n"
            f"{chunk.text[:1500]}"
        )

    prompt = [
        {"role": "system", "content": (
            "You extract technical data from manufacturer datasheets. Return JSON only: "
            "{\"capability\": \"<value with units, or NOT_FOUND>\", "
            "\"quotes\": [\"<verbatim excerpt copied exactly from the text>\"], "
            "\"page\": <page number>, \"document\": \"<document name>\"}. "
            "Copy quotes character-for-character from the excerpts. "
            "If the excerpts do not state this parameter, return capability NOT_FOUND and quotes []."
        )},
        {"role": "user", "content": (
            f"Requirement: {req.text}\n"
            f"Parameter: {req.parameter} | Value: {req.value} | Unit: {req.unit} | Direction: {req.direction}\n\n"
            "Datasheet excerpts:\n" + "\n\n".join(excerpts) + "\n\n"
            "Extract the manufacturer's capability and the verbatim supporting quote:"
        )},
    ]

    from tender_extraction.config import config

    cache_key = hashlib.sha256(
        f"{PROMPT_VERSION}|{config.llm.model}|{json.dumps(prompt, sort_keys=True)}".encode("utf-8")
    ).hexdigest()

    with _LLM_LOCK:
        if cache_key in _LLM_CACHE:
            _LLM_CACHE_HITS += 1
            return dict(_LLM_CACHE[cache_key])

        if _LLM_CALLS >= MAX_LLM_CALLS_PER_TENDER:
            return {"capability": "NOT_FOUND", "quotes": [], "reason": "LLM call cap reached."}

        _LLM_CALLS += 1

    try:
        from tender_extraction.extraction import _call_llm, _repair_json, load_model

        llm = load_model()
        raw = _call_llm(llm, prompt, msg="Parameter extraction")
        data = json.loads(_repair_json(raw))
    except Exception as exc:  # noqa: BLE001 - recorded as no_evidence
        logger.warning("LLM extraction failed for %s: %s", req.requirement_id, exc)
        return {"capability": "NOT_FOUND", "quotes": [], "reason": f"LLM extraction failed: {exc}"}

    result = {
        "capability": str(data.get("capability") or "NOT_FOUND").strip(),
        "quotes": [str(quote) for quote in (data.get("quotes") or []) if str(quote).strip()],
        "page": data.get("page") or 0,
        "document": data.get("document") or "",
    }
    with _LLM_LOCK:
        _LLM_CACHE[cache_key] = result
    return dict(result)


# ── Evidence verification and decisions ───────────────────────────────────


def _normalize_text(text: Any) -> str:
    raw = str(text or "").lower()
    raw = raw.replace("²", "2").replace("³", "3")
    return re.sub(r"[^a-z0-9]+", " ", raw).strip()


def _verify_quotes(extracted: Dict[str, Any], hits: Sequence[Dict[str, Any]]) -> List[EvidenceRef]:
    """Keep only quotes that appear verbatim in a retrieved passage."""
    evidence: List[EvidenceRef] = []
    seen: Set[Tuple[str, int, str]] = set()
    for quote in extracted.get("quotes") or []:
        normalized_quote = _normalize_text(quote)
        if len(normalized_quote) < 3:
            continue
        for hit in hits:
            chunk = hit["chunk"]
            if normalized_quote not in _normalize_text(chunk.text):
                continue
            key = (chunk.chunk_id, chunk.metadata.page, normalized_quote)
            if key in seen:
                break
            seen.add(key)
            evidence.append(EvidenceRef(
                document=chunk.metadata.document or "NOT_FOUND",
                page=chunk.metadata.page,
                quote=str(quote).strip(),
                chunk_id=chunk.chunk_id,
            ))
            break
    return evidence


def _sentence_with(text: str, needle: str) -> str:
    """Return the verbatim sentence/line containing ``needle``."""
    normalized_needle = _normalize_text(needle)
    if not normalized_needle:
        return text[:300].strip()
    for line in re.split(r"(?<=[.;])\s+|\n+", text):
        if normalized_needle in _normalize_text(line):
            return line.strip()
    return text[:300].strip()


def _find_verbatim_value(req: Requirement, hits: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """A non-numeric requirement value found verbatim is decisive evidence."""
    value = str(req.value or "").strip()
    if not value or value.upper() == "NOT_FOUND" or re.fullmatch(r"-?\d+(?:\.\d+)?", value):
        return None
    normalized_value = _normalize_text(value)
    if len(normalized_value) < 3:
        return None
    for hit in hits:
        chunk = hit["chunk"]
        if normalized_value not in _normalize_text(chunk.text):
            continue
        return {
            "status": "full_match",
            "capability": value,
            "confidence": 0.85,
            "needs_review": False,
            "reason": f"Datasheet explicitly states '{value}'.",
            "evidence": [EvidenceRef(
                document=chunk.metadata.document or "NOT_FOUND",
                page=chunk.metadata.page,
                quote=_sentence_with(chunk.text, value),
                chunk_id=chunk.chunk_id,
            )],
        }
    return None


def _numeric_from_capability(req: Requirement, capability: str) -> Optional[Dict[str, Any]]:
    """Apply the requirement operator to the LLM-extracted capability value."""
    parsed = _parse_requirement_number(req)
    if parsed is None:
        return None
    match = _NUM_UNIT_RE.search(str(capability or ""))
    if not match:
        return None
    capability_number = float(match.group(1))
    capability_unit = _normalize_unit(match.group(2) or "")
    requirement_unit = _normalize_unit(req.unit)
    if requirement_unit and capability_unit:
        converted = _to_unit(capability_number, capability_unit, requirement_unit)
        if converted is None:
            return None
        capability_number = converted
    requirement_low, requirement_high, direction = parsed
    if direction == "min":
        satisfies = capability_number >= requirement_low
        partial = capability_number >= requirement_low * 0.8
    elif direction == "max":
        satisfies = capability_number <= requirement_high
        partial = capability_number <= requirement_high * 1.2
    elif direction == "range":
        satisfies = requirement_low <= capability_number <= requirement_high
        partial = requirement_low * 0.8 <= capability_number <= requirement_high * 1.2
    else:
        reference = max(abs(requirement_low), 1e-9)
        satisfies = abs(capability_number - requirement_low) <= 0.02 * reference
        partial = abs(capability_number - requirement_low) <= 0.20 * reference
    status = "full_match" if satisfies else "partial_match" if partial else "no_match"
    return {
        "status": status,
        "reason": (
            f"Verified capability {capability} "
            f"{'satisfies' if satisfies else 'partially satisfies' if partial else 'fails'} "
            f"{direction} {req.value}."
        ),
        "confidence": 0.85,
        "needs_review": False,
    }


def _categorical_decision(req: Requirement, capability: str, quotes: Sequence[str]) -> Dict[str, Any]:
    required = _normalize_text(req.value)
    if not required or required == "not found":
        return {
            "status": "not_applicable",
            "reason": "Requirement has no comparable value; excluded from the capability score.",
            "confidence": 0.8,
            "needs_review": False,
        }
    capability_text = _normalize_text(capability)
    quote_text = _normalize_text(" ".join(quotes))
    if required in capability_text or required in quote_text:
        return {
            "status": "full_match",
            "reason": f"Verified catalogue text states '{req.value}'.",
            "confidence": 0.85,
            "needs_review": False,
        }
    required_tokens = set(required.split())
    capability_tokens = set(capability_text.split()) | set(quote_text.split())
    overlap = len(required_tokens & capability_tokens) / max(len(required_tokens), 1)
    if overlap >= 0.6:
        return {
            "status": "partial_match",
            "reason": f"Catalogue text partially matches '{req.value}'.",
            "confidence": 0.6,
            "needs_review": True,
        }
    return {
        "status": "no_match",
        "reason": f"Verified catalogue text '{capability}' does not provide '{req.value}'.",
        "confidence": 0.7,
        "needs_review": False,
    }


# ── Per-requirement matching ──────────────────────────────────────────────


def _not_applicable(req: Requirement, manufacturer: str, reason: str) -> RequirementMatch:
    return RequirementMatch(
        requirement_id=req.requirement_id,
        boq_item_id=req.boq_item_id,
        manufacturer=manufacturer,
        is_mandatory=req.is_mandatory,
        status="not_applicable",
        score=0.0,
        confidence=0.9,
        needs_review=False,
        reason=reason,
    )


def _no_evidence(req: Requirement, manufacturer: str, hits: Sequence[Dict[str, Any]], reason: str) -> RequirementMatch:
    return RequirementMatch(
        requirement_id=req.requirement_id,
        boq_item_id=req.boq_item_id,
        manufacturer=manufacturer,
        is_mandatory=req.is_mandatory,
        status="no_evidence",
        score=0.0,
        confidence=0.0,
        needs_review=False,
        reason=reason,
        audit=AuditTrail(
            searched_queries=[f"{req.parameter} {req.value} {req.unit}".strip()],
            searched_pages=[hit["chunk"].metadata.page for hit in hits],
            searched_documents=list(dict.fromkeys(
                hit["chunk"].metadata.document for hit in hits if hit["chunk"].metadata.document
            )),
            searched_chunks=[hit["chunk"].chunk_id for hit in hits],
        ),
    )


def match_requirement(
    req: Requirement,
    manufacturer_name: str,
    hits: List[Dict[str, Any]],
    judge: Callable[[Requirement, List[Dict[str, Any]]], Dict[str, Any]],
) -> RequirementMatch:
    if req.kind in _NOT_APPLICABLE_KINDS or req.parameter in _NOT_APPLICABLE_PARAMETERS:
        return _not_applicable(
            req,
            manufacturer_name,
            "Commercial/legal/compliance/reference/procedure requirement — excluded from the product capability score.",
        )

    if not hits:
        return _no_evidence(
            req,
            manufacturer_name,
            hits,
            "No relevant catalogue content retrieved — absence of evidence is not a failure.",
        )

    numeric = _compare_numeric(req, hits)
    if numeric is not None:
        return RequirementMatch(
            requirement_id=req.requirement_id,
            boq_item_id=req.boq_item_id,
            manufacturer=manufacturer_name,
            is_mandatory=req.is_mandatory,
            capability=numeric["capability"],
            status=numeric["status"],
            score=STATUS_WEIGHTS[numeric["status"]] * 100.0,
            evidence=numeric["evidence"],
            confidence=numeric["confidence"],
            needs_review=numeric["needs_review"],
            reason=numeric["reason"],
        )

    verbatim = _find_verbatim_value(req, hits)
    if verbatim is not None:
        return RequirementMatch(
            requirement_id=req.requirement_id,
            boq_item_id=req.boq_item_id,
            manufacturer=manufacturer_name,
            is_mandatory=req.is_mandatory,
            capability=verbatim["capability"],
            status=verbatim["status"],
            score=STATUS_WEIGHTS[verbatim["status"]] * 100.0,
            evidence=verbatim["evidence"],
            confidence=verbatim["confidence"],
            needs_review=verbatim["needs_review"],
            reason=verbatim["reason"],
        )

    try:
        extracted = judge(req, hits)
    except Exception as exc:  # noqa: BLE001 - recorded as no_evidence
        logger.warning("Extraction failed for %s: %s", req.requirement_id, exc)
        return _no_evidence(req, manufacturer_name, hits, f"Evidence extraction failed: {exc}")

    # Injected judges may return a complete verdict; production judges return
    # only {capability, quotes, page, document}.
    if "status" in extracted and "evidence" in extracted:
        status = extracted["status"]
        if status not in STATUS_WEIGHTS and status not in {"no_evidence", "not_applicable"}:
            status = "no_evidence"
        return RequirementMatch(
            requirement_id=req.requirement_id,
            boq_item_id=req.boq_item_id,
            manufacturer=manufacturer_name,
            is_mandatory=req.is_mandatory,
            capability=extracted.get("capability", "NOT_FOUND"),
            status=status,
            score=STATUS_WEIGHTS.get(status, 0.0) * 100.0,
            evidence=extracted.get("evidence", []),
            confidence=extracted.get("confidence", 0.5),
            needs_review=extracted.get("needs_review", False),
            reason=extracted.get("reason", ""),
        )

    evidence = _verify_quotes(extracted, hits)
    capability = str(extracted.get("capability") or "NOT_FOUND").strip()
    if not evidence or capability.upper() == "NOT_FOUND":
        detail = extracted.get("reason") or "No verbatim quote found on the retrieved pages."
        return _no_evidence(req, manufacturer_name, hits, detail)

    decision = _numeric_from_capability(req, capability)
    if decision is None:
        decision = _categorical_decision(req, capability, [item.quote for item in evidence])

    status = decision["status"]
    return RequirementMatch(
        requirement_id=req.requirement_id,
        boq_item_id=req.boq_item_id,
        manufacturer=manufacturer_name,
        is_mandatory=req.is_mandatory,
        capability=capability,
        status=status,
        score=STATUS_WEIGHTS.get(status, 0.0) * 100.0,
        evidence=evidence,
        confidence=decision.get("confidence", 0.6),
        needs_review=decision.get("needs_review", False),
        reason=decision.get("reason", ""),
    )


# ── Report assembly ───────────────────────────────────────────────────────


def _score_counts(matches: Sequence[RequirementMatch]) -> Tuple[Dict[str, int], Dict[str, int], float, float]:
    counts = {"full_match": 0, "partial_match": 0, "no_match": 0, "no_evidence": 0, "not_applicable": 0}
    mandatory = {"passes": 0, "fails": 0, "no_evidence": 0}
    for match in matches:
        counts[match.status] = counts.get(match.status, 0) + 1
        if match.is_mandatory:
            if match.status in {"full_match", "partial_match"}:
                mandatory["passes"] += 1
            elif match.status == "no_match":
                mandatory["fails"] += 1
            elif match.status == "no_evidence":
                mandatory["no_evidence"] += 1
    decided = [match for match in matches if match.status in DECIDED_STATUSES]
    scorable = [match for match in matches if match.status in SCORABLE_STATUSES]
    score_sum = sum(STATUS_WEIGHTS[match.status] for match in decided)
    compatibility = (score_sum / len(decided) * 100.0) if decided else 0.0
    coverage = (len(decided) / len(scorable) * 100.0) if scorable else 0.0
    return counts, mandatory, round(compatibility, 1), round(coverage, 1)


def _item_results(
    matches: Sequence[RequirementMatch],
    requirements_by_id: Dict[str, Requirement],
    items_by_id: Dict[str, Dict[str, Any]],
) -> List[ItemMatchResult]:
    grouped: Dict[str, List[RequirementMatch]] = {}
    for match in matches:
        grouped.setdefault(match.boq_item_id or "", []).append(match)
    results: List[ItemMatchResult] = []
    for item_id, item_matches in grouped.items():
        item = items_by_id.get(item_id) or {}
        counts, mandatory, compatibility, coverage = _score_counts(item_matches)
        item_decided = [m for m in item_matches if m.status in DECIDED_STATUSES]
        item_conf = (
            round((sum(getattr(m, "confidence", 0.0) for m in item_decided) / len(item_decided)) * 100.0, 1)
            if item_decided else 0.0
        )
        results.append(ItemMatchResult(
            item_id=item_id or "tender",
            description=str(item.get("description") or "Tender-level requirements"),
            item_type=str(item.get("item_type") or "main_product"),
            category=normalize_category(item.get("category")),
            processing_status=str(item.get("processing_status") or "ready"),
            compatibility=compatibility,
            coverage=coverage,
            evidence_confidence=item_conf,
            status_counts=counts,
            mandatory_counts=mandatory,
            matches=list(item_matches),
            review_reasons=[match.reason for match in item_matches if match.needs_review],
        ))
    return results


def aggregate(
    manufacturer_name: str,
    matches: List[RequirementMatch],
    *,
    slug: str = "",
    category: str = "",
    has_content: bool = True,
    requirements_by_id: Optional[Dict[str, Requirement]] = None,
    items_by_id: Optional[Dict[str, Dict[str, Any]]] = None,
) -> ManufacturerResult:
    counts, mandatory, compatibility, coverage = _score_counts(matches)
    decided = [m for m in matches if m.status in DECIDED_STATUSES]
    evidence_conf = (
        round((sum(getattr(m, "confidence", 0.0) for m in decided) / len(decided)) * 100.0, 1)
        if decided else 0.0
    )
    low_coverage = bool(matches) and coverage < 50.0
    if not has_content:
        tender_match_status = "unindexed"
    elif not matches:
        tender_match_status = "not_evaluated"
    elif low_coverage:
        tender_match_status = "low_coverage"
    else:
        tender_match_status = "evaluated"
    return ManufacturerResult(
        manufacturer=manufacturer_name,
        slug=slug,
        category=category,
        overall_compatibility=compatibility,
        coverage=coverage,
        evidence_confidence=evidence_conf,
        low_coverage=low_coverage,
        rank=0,
        tender_match_status=tender_match_status,
        status_counts=counts,
        mandatory_counts=mandatory,
        item_results=_item_results(matches, requirements_by_id or {}, items_by_id or {}),
        matches=matches,
    )


def _evidence_coverage(requirements: Sequence[Requirement], manufacturers: Sequence[ManufacturerResult]) -> Dict[str, Any]:
    covered: Set[str] = set()
    for manufacturer in manufacturers:
        for match in manufacturer.matches:
            if match.status in {"full_match", "partial_match"} and match.evidence:
                covered.add(match.requirement_id)
    missing = [
        {"requirement_id": requirement.requirement_id, "parameter": requirement.parameter, "text": requirement.text}
        for requirement in requirements
        if requirement.requirement_id not in covered
    ]
    total = len(requirements)
    return {
        "requirements_total": total,
        "requirements_with_evidence": len(covered),
        "requirements_without_evidence": missing,
        "coverage_ratio": round(len(covered) / total, 4) if total else 0.0,
    }


def _resolve_categories(
    tender_result: Dict[str, Any],
    requirements: List[Requirement],
    vocabulary,
    override: Optional[str],
) -> Tuple[str, List[str]]:
    items = tender_result.get("procurement_items") or []
    items_by_id = {str(item.get("item_id")): item for item in items}
    for item in items:
        if not normalize_category(item.get("category")):
            item["category"] = classify_text(item.get("description"), vocabulary)[0] or ""

    tender_category = normalize_category(tender_result.get("category"))
    if not tender_category:
        tender_category, _ = classify_tender(
            [(item.get("item_type"), item.get("description")) for item in items],
            [requirement.text for requirement in requirements],
            vocabulary,
        )
    if override:
        tender_category = override

    for requirement in requirements:
        category = normalize_category(requirement.category)
        if not category:
            item = items_by_id.get(requirement.boq_item_id) or {}
            category = normalize_category(item.get("category"))
        if not category:
            category = classify_text(requirement.text, vocabulary)[0] or ""
        if not category:
            category = tender_category or ""
        requirement.category = category

    categories = sorted({requirement.category for requirement in requirements if requirement.category})
    return tender_category or "", categories


def _requirement_is_numeric(requirement: Requirement) -> bool:
    return _parse_requirement_number(requirement) is not None


def _rank_results(results: List[ManufacturerResult]) -> List[ManufacturerResult]:
    """Rank by effective tender fit (compatibility * coverage), then raw compatibility and coverage."""
    results.sort(key=lambda item: (
        1 if item.tender_match_status == "unindexed" else 0,
        -round((item.overall_compatibility * item.coverage) / 100.0, 3),
        -round(item.overall_compatibility, 2),
        -round(item.coverage, 2),
        item.manufacturer.lower(),
    ))
    for rank, result in enumerate(results, start=1):
        result.rank = rank
    return results


def generate_match_report(
    tender_result: Dict[str, Any],
    *,
    manufacturers_dir: str = "data/manufacturers",
    tender_file: str = "",
    selected_manufacturers: Optional[List[str]] = None,
    category_filter: Optional[str] = None,
    manufacturer_categories: Optional[Dict[str, str]] = None,
    judge: Optional[Callable] = None,
    corpus_indexer: Optional[Callable] = None,
    retriever_factory: Optional[Callable] = None,
) -> MatchReport:
    """Match every tender requirement against the manufacturers in its category.

    Injectable seams (tests pass fakes; production uses defaults):
      ``judge(req, hits) -> {capability, quotes, page, document}``
      ``corpus_indexer(root, category_filter=...) -> (chunks_by_category, payloads, notes)``
      ``retriever_factory(category, chunks, payloads) -> retriever``
    """
    _reset_llm_budget()
    requirements = split_requirements(tender_result)
    notes: List[str] = list(tender_result.get("notes") or [])
    reconciliation = tender_result.get("reconciliation") or {}
    notes.extend(str(note) for note in reconciliation.get("notes") or [] if note)

    if not requirements:
        reason = "; ".join(str(note) for note in notes[:2]) or "no requirements extracted from uploaded documents"
        raise ValueError(f"No requirements could be extracted from the uploaded documents: {reason}")

    judge = judge or _llm_extract
    corpus_indexer = corpus_indexer or _default_corpus_indexer
    retriever_factory = retriever_factory or _default_retriever_factory

    vocabulary = build_vocabulary(manufacturers_dir)
    override = normalize_category(category_filter) or None
    primary_category, categories = _resolve_categories(tender_result, requirements, vocabulary, override)

    all_manufacturers, corpus_notes = load_manufacturers(manufacturers_dir)
    notes.extend(dict.fromkeys(corpus_notes))
    try:
        chunks_by_category, payloads, index_notes = corpus_indexer(manufacturers_dir, category_filter=override)
    except TypeError:
        chunks_by_category, payloads, index_notes = corpus_indexer(manufacturers_dir)
    notes.extend(str(note) for note in index_notes or [] if note)

    overrides = {normalize_category(slug): normalize_category(category) for slug, category in (manufacturer_categories or {}).items()}
    selected = {str(name).strip().lower() for name in (selected_manufacturers or []) if str(name).strip()}

    eligible: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for manufacturer in all_manufacturers:
        slug = manufacturer["slug"]
        manufacturer_category = overrides.get(normalize_category(slug)) or normalize_category(manufacturer.get("category"))
        manufacturer_categories_set = set(manufacturer.get("categories") or [])
        if manufacturer_category:
            manufacturer_categories_set.add(manufacturer_category)
        manufacturer["categories"] = sorted(manufacturer_categories_set)
        manufacturer["category"] = manufacturer_category or manufacturer.get("category") or ""

        if selected and slug.lower() not in selected and str(manufacturer.get("display_name") or "").lower() not in selected:
            skipped.append({
                "manufacturer": manufacturer.get("display_name"),
                "slug": slug,
                "category": manufacturer.get("category"),
                "reason": "not selected by the user",
            })
            continue
        if categories and not (manufacturer_categories_set & set(categories)):
            skipped.append({
                "manufacturer": manufacturer.get("display_name"),
                "slug": slug,
                "category": manufacturer.get("category"),
                "reason": f"different category (tender: {', '.join(categories)})",
            })
            continue
        if not manufacturer.get("category"):
            skipped.append({
                "manufacturer": manufacturer.get("display_name"),
                "slug": slug,
                "category": "",
                "reason": "no category in sources.json",
            })
            continue
        eligible.append(manufacturer)

    items_by_id = {str(item.get("item_id")): item for item in tender_result.get("procurement_items") or []}
    requirements_by_id = {requirement.requirement_id: requirement for requirement in requirements}

    retrieval_categories = sorted({category for manufacturer in eligible for category in manufacturer.get("categories") or []})
    hits_by_key: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    retriever_failures: Set[str] = set()

    for category in retrieval_categories:
        category_requirements = [
            requirement for requirement in requirements
            if requirement.category == category or not requirement.category
        ]
        category_manufacturers = [manufacturer for manufacturer in eligible if category in (manufacturer.get("categories") or [])]
        if not category_requirements or not category_manufacturers:
            continue
        t_cat_start = time.time()
        retriever = None
        try:
            retriever = retriever_factory(category, chunks_by_category.get(category, []), payloads)

            # Pre-batch compute embeddings once per unique query to avoid N_manufacturers redundant encodings
            unique_queries = list(dict.fromkeys(
                f"{req.parameter} {req.value} {req.unit} {req.text.split(':', 1)[0]}".strip()
                for req in category_requirements
            ))
            logger.info("Matching '%s': pre-encoding %d unique requirement queries...", category, len(unique_queries))
            from tender_extraction.model_runtime import get_embedding_model
            batch_vectors = get_embedding_model().encode(unique_queries, batch_size=32, normalize_embeddings=True)
            encoded_cache = {
                q: batch_vectors[idx]
                for idx, q in enumerate(unique_queries)
            }
            logger.info("Matching '%s': querying %d manufacturer catalogues in Qdrant...", category, len(category_manufacturers))

            for manufacturer in category_manufacturers:
                slug = manufacturer["slug"]
                for requirement in category_requirements:
                    query = f"{requirement.parameter} {requirement.value} {requirement.unit} {requirement.text.split(':', 1)[0]}".strip()
                    emb = encoded_cache.get(query)
                    try:
                        hits = retriever.retrieve(
                            query,
                            top_k=6,
                            payload_filter={"manufacturer": slug, "category": category},
                            embedding=emb,
                        )
                    except Exception as exc:  # noqa: BLE001 - recorded as no_evidence
                        logger.warning("Retrieval failed for %s/%s: %s", slug, requirement.requirement_id, exc)
                        hits = []
                    merged = hits_by_key.setdefault((slug, requirement.requirement_id), [])
                    seen = {hit["chunk"].chunk_id for hit in merged}
                    merged.extend(hit for hit in hits if hit["chunk"].chunk_id not in seen)

            logger.info("Matching '%s': finished retrieval for %d requirements across %d vendors in %.2fs", category, len(category_requirements), len(category_manufacturers), time.time() - t_cat_start)
        except Exception as exc:  # noqa: BLE001 - collection missing/unindexed
            logger.warning("Retriever unavailable for category %s: %s", category, exc)
            retriever_failures.add(category)
            notes.append(f"Catalogue index for '{category}' is unavailable: {exc}")
        finally:
            close = getattr(retriever, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

    results: List[ManufacturerResult] = []
    for manufacturer in eligible:
        slug = manufacturer["slug"]
        manufacturer_categories_set = set(manufacturer.get("categories") or [])
        category_missing = bool(manufacturer_categories_set & set(retriever_failures))
        def _evaluate_req(requirement: Requirement) -> RequirementMatch:
            if requirement.category and categories and requirement.category not in manufacturer_categories_set:
                return _not_applicable(
                    requirement,
                    manufacturer["display_name"],
                    f"Requirement category '{requirement.category}' is outside this manufacturer's catalogue.",
                )
            if category_missing and requirement.category in retriever_failures:
                return _no_evidence(
                    requirement,
                    manufacturer["display_name"],
                    [],
                    f"Catalogue index for '{requirement.category}' is unavailable (ingestion error).",
                )
            hits = sorted(
                hits_by_key.get((slug, requirement.requirement_id), []),
                key=lambda hit: hit.get("score", 0.0),
                reverse=True,
            )[:6]
            return match_requirement(requirement, manufacturer["display_name"], hits, judge)

        if len(requirements) > 1:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=5) as executor:
                matches: List[RequirementMatch] = list(executor.map(_evaluate_req, requirements))
        else:
            matches = [_evaluate_req(req) for req in requirements]

        result = aggregate(
            manufacturer["display_name"],
            matches,
            slug=slug,
            category=manufacturer.get("category") or "",
            has_content=not category_missing,
            requirements_by_id=requirements_by_id,
            items_by_id=items_by_id,
        )
        results.append(result)
        if category_missing:
            skipped.append({
                "manufacturer": manufacturer.get("display_name"),
                "slug": slug,
                "category": manufacturer.get("category"),
                "reason": "catalogue collection is not indexed",
            })

    # Low coverage is always ranked last; compatibility then coverage break ties.
    _rank_results(results)

    if not results:
        notes.append("No manufacturer matched the tender category; nothing could be evaluated.")

    item_summaries: List[Dict[str, Any]] = []
    for item_id, item in items_by_id.items():
        item_requirements = [requirement for requirement in requirements if requirement.boq_item_id == item_id]
        item_summaries.append({
            "item_id": item_id,
            "description": item.get("description") or "",
            "item_type": item.get("item_type") or "main_product",
            "category": item.get("category") or "",
            "processing_status": item.get("processing_status") or "ready",
            "requirement_count": len(item_requirements),
        })
    tender_level = [requirement for requirement in requirements if not requirement.boq_item_id]
    if tender_level:
        item_summaries.append({
            "item_id": "tender",
            "description": "Tender-level requirements",
            "item_type": "main_product",
            "category": primary_category,
            "processing_status": "ready",
            "requirement_count": len(tender_level),
        })

    if primary_category:
        notes.append(f"Tender category: {primary_category} (derived from manufacturer manifests).")
    if categories:
        notes.append(f"Requirement categories evaluated: {', '.join(categories)}.")
    if skipped:
        notes.append(f"{len(skipped)} manufacturer(s) skipped (see skipped_manufacturers).")
    if _LLM_CALLS >= MAX_LLM_CALLS_PER_TENDER:
        notes.append(f"LLM call cap reached ({MAX_LLM_CALLS_PER_TENDER}); undecided requirements remain no_evidence.")

    return MatchReport(
        tender_file=tender_file,
        tender_status="evaluated" if results else "no_manufacturers",
        category=primary_category or "",
        categories=categories,
        requirements=requirements,
        manufacturers=results,
        skipped_manufacturers=skipped,
        item_summaries=item_summaries,
        generated_at=time.time(),
        notes=list(dict.fromkeys(note for note in notes if note)),
        reconciliation=reconciliation,
        technical_compliance=tender_result.get("compliance_requirements") or reconciliation.get("compliance_requirements") or [],
        vendor_eligibility=tender_result.get("vendor_eligibility") or reconciliation.get("vendor_eligibility") or [],
        evidence_coverage=_evidence_coverage(requirements, results),
        llm_calls=_LLM_CALLS,
        llm_cache_hits=_LLM_CACHE_HITS,
    )
