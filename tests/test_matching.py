"""
test_matching.py — Matching engine contract tests.

No real models, no network, no Qdrant.  Every seam (LLM extraction, corpus
index, retriever) is injected as a fake.

Contract under test:
  * four statuses + not_applicable, with no_evidence never treated as failure
  * the LLM extracts a value + quote only; code verifies the quote
  * units are normalised before comparison; bore and rod diameter stay distinct
  * categories come from manufacturer manifests and scope retrieval
  * compatibility = score / decided, coverage = decided / scorable
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tender_extraction import matching
from tender_extraction.schemas import Chunk, ChunkMetadata
from tender_extraction.schemas_match import MatchReport, RequirementMatch


def _chunk(text: str, page: int = 1, slug: str = "acme", file: str = "cat.pdf") -> Chunk:
    return Chunk(
        chunk_id=f"{slug}:{file}:p{page}",
        text=text,
        metadata=ChunkMetadata(section=file, page=page, document=file),
    )


def _hits(*chunks: Chunk):
    return [{"chunk": chunk, "score": 0.9} for chunk in chunks]


def _req(**overrides):
    base = dict(
        requirement_id="R001.1",
        text="Pump: capacity = >= 10 m3/h",
        parameter="capacity",
        value="10",
        unit="m3/h",
        direction="min",
        kind="technical",
    )
    base.update(overrides)
    return matching.Requirement(**base)


def _judge_missing(req, hits):
    """Production-shaped judge: extract-only, nothing found."""
    return {"capability": "NOT_FOUND", "quotes": [], "reason": "Datasheet does not cover this requirement."}


def _judge(capability, *quotes):
    def judge(req, hits):
        return {"capability": capability, "quotes": list(quotes)}
    return judge


def _no_judge(req, hits):
    raise AssertionError("judge must not be called")


# ── Requirement splitting ─────────────────────────────────────────────────


def test_split_requirements_parses_directions():
    tender = {"technical_specifications": [{
        "component": "Pump",
        "specs": {
            "pressure": "≥ 10 bar",
            "length": "≤ 500 mm",
            "temp_range": "10-20 °C",
            "material": "SS316",
            "experience": "5 years experience",
        },
        "source": {"chunk_id": "c1", "page": "Page 4", "exact_text": "quote"},
    }]}
    reqs = matching.split_requirements(tender)
    by_param = {r.parameter: r for r in reqs}
    assert by_param["pressure"].direction == "min"
    assert by_param["pressure"].value == "10"
    assert by_param["pressure"].unit == "bar"
    assert by_param["length"].direction == "max"
    assert by_param["temp_range"].direction == "range"
    assert by_param["temp_range"].value == "10-20"
    assert by_param["material"].direction == "any"
    assert by_param["experience"].kind == "commercial"
    assert by_param["pressure"].source.page == 4


def test_split_requirements_skips_procedure_parameters():
    tender = {"canonical_requirements": [
        {"requirement_id": "CR001", "parameter": "quantity", "value": "6", "unit": "Nos", "kind": "technical"},
        {"requirement_id": "CR002", "parameter": "item_category", "value": "Pump", "kind": "technical"},
        {"requirement_id": "CR003", "parameter": "flow_rate", "value": "15", "unit": "m3/h",
         "direction": "exact", "kind": "technical"},
    ]}
    reqs = matching.split_requirements(tender)
    assert [req.requirement_id for req in reqs] == ["CR003"]


# ── Deterministic numeric comparison ──────────────────────────────────────


def test_numeric_min_full_match_with_unit_conversion():
    req = _req(unit="bar", text="Pump: pressure = >= 10 bar", parameter="pressure")
    hits = _hits(_chunk("The pump body withstands a working pressure of 1600 kPa.", page=3))
    result = matching.match_requirement(req, "ACME", hits, _no_judge)
    assert result.status == "full_match", result.reason
    assert result.score == 100.0
    assert result.evidence and result.evidence[0].page == 3
    assert result.evidence[0].document == "cat.pdf"


def test_numeric_min_partial_match():
    req = _req(unit="bar", text="Pump: pressure = >= 10 bar", parameter="pressure")
    hits = _hits(_chunk("Maximum pressure for this model is 8 bar."))
    result = matching.match_requirement(req, "ACME", hits, _no_judge)
    assert result.status == "partial_match"
    assert 0 < result.score < 100


def test_numeric_out_of_range_is_no_match_without_calling_the_llm():
    req = _req(unit="bar", text="Pump: pressure = >= 10 bar", parameter="pressure")
    hits = _hits(_chunk("Rated pressure 5 bar."))
    result = matching.match_requirement(req, "ACME", hits, _no_judge)
    assert result.status == "no_match"
    assert result.score == 0.0
    assert result.evidence


def test_numeric_range_match():
    req = _req(direction="range", value="10-20", unit="bar",
               text="Pump: pressure = 10-20 bar", parameter="pressure")
    hits = _hits(_chunk("Operating pressure 15 bar continuous."))
    result = matching.match_requirement(req, "ACME", hits, _no_judge)
    assert result.status == "full_match"


def test_max_direction():
    req = _req(direction="max", value="500", unit="mm",
               text="Valve: face_to_face = <= 500 mm", parameter="face_to_face")
    hits = _hits(_chunk("Face to face dimension 450 mm per ISO 5752."))
    result = matching.match_requirement(req, "ACME", hits, _no_judge)
    assert result.status == "full_match"


def test_bar_mpa_and_kgcm2_are_comparable():
    req = _req(unit="bar", text="Pump: pressure = >= 10 bar", parameter="pressure")
    for text in ("Working pressure 1.2 MPa.", "Working pressure 11 kg/cm2."):
        result = matching.match_requirement(req, "ACME", _hits(_chunk(text)), _no_judge)
        assert result.status == "full_match", text


def test_bore_and_rod_diameter_are_not_confused():
    req = _req(parameter="rod_diameter", value="100", unit="mm", direction="exact",
               text="Hydraulic cylinder: rod_diameter = 100 mm")
    hits = _hits(_chunk("Bore 200 mm, rod diameter 100 mm."))
    result = matching.match_requirement(req, "ACME", hits, _no_judge)
    assert result.status == "full_match"
    assert result.capability == "100 mm"


# ── Non-numeric paths ─────────────────────────────────────────────────────


def test_material_verbatim_match_skips_judge():
    req = _req(direction="any", value="SS316", unit="NOT_FOUND",
               text="Valve: body material = SS316", parameter="body material")
    hits = _hits(_chunk("Body material: SS316 investment cast."))
    result = matching.match_requirement(req, "ACME", hits, _no_judge)
    assert result.status == "full_match"
    assert result.evidence[0].quote


def test_commercial_requirement_is_not_applicable():
    req = _req(kind="commercial", text="Vendor: 5 years experience", parameter="experience")
    result = matching.match_requirement(req, "ACME", _hits(_chunk("anything")), _no_judge)
    assert result.status == "not_applicable"
    assert result.score == 0.0


def test_fabricated_quotes_are_dropped():
    req = _req(direction="any", value="special coating", unit="NOT_FOUND",
               text="Pump: coating = special coating", parameter="coating")
    real_quote = "Epoxy coated internals"
    hits = _hits(_chunk("Pump internals are epoxy coated internals for corrosion resistance."))
    result = matching.match_requirement(
        req, "ACME", hits,
        _judge("epoxy coating", real_quote, "diamond titanium quantum coating"),
    )
    assert [evidence.quote for evidence in result.evidence] == [real_quote]
    assert result.status in {"partial_match", "no_match"}


def test_all_quotes_fake_is_no_evidence_not_no_match():
    req = _req(direction="any", value="special coating", unit="NOT_FOUND",
               text="Pump: coating = special coating", parameter="coating")
    hits = _hits(_chunk("Standard painted finish."))
    result = matching.match_requirement(
        req, "ACME", hits, _judge("magic coating", "completely invented sentence about coatings"),
    )
    assert result.status == "no_evidence"
    assert result.evidence == []
    assert result.score == 0.0


def test_quote_not_on_the_retrieved_page_is_no_evidence():
    req = _req(direction="any", value="special coating", unit="NOT_FOUND",
               text="Pump: coating = special coating", parameter="coating")
    hits = _hits(_chunk("Standard painted finish."))
    result = matching.match_requirement(
        req, "ACME", hits, _judge("special coating", "This sentence does not exist anywhere."),
    )
    assert result.status == "no_evidence"
    assert result.evidence == []


def test_judge_failure_is_no_evidence_and_never_crashes():
    req = _req(direction="any", value="exotic finish", unit="NOT_FOUND",
               text="Pump: finish = exotic finish", parameter="finish")
    hits = _hits(_chunk("Some unrelated datasheet prose about packaging."))

    def broken_judge(req, hits):
        raise RuntimeError("API down")

    result = matching.match_requirement(req, "ACME", hits, broken_judge)
    assert result.status == "no_evidence"
    assert result.evidence == []


def test_extracted_numeric_capability_is_scored_by_code():
    req = _req(parameter="flow_rate", value="15", unit="m3/h", direction="min",
               text="Pump: flow_rate = >= 15 m3/h")
    hits = _hits(_chunk("Model X delivers 18 m3/h."))
    result = matching.match_requirement(req, "ACME", hits, _judge("18 m3/h", "Model X delivers 18 m3/h."))
    assert result.status == "full_match"

    result = matching.match_requirement(req, "ACME", hits, _judge("13 m3/h", "Model X delivers 18 m3/h."))
    # The quote is verbatim, but the extracted number partially satisfies the requirement.
    assert result.status == "partial_match"


def test_extracted_categorical_capability_must_match_the_requirement():
    req = _req(direction="any", value="Cast Iron", unit="NOT_FOUND",
               text="Pump: body material = Cast Iron", parameter="body material")
    hits = _hits(_chunk("Body: CI"))
    result = matching.match_requirement(req, "ACME", hits, _judge("Cast Iron", "Body: CI"))
    assert result.status == "full_match"


# ── Corpus fixtures ───────────────────────────────────────────────────────


def _write_corpus(root: Path, slug: str, display: str, category: str):
    mdir = root / slug
    mdir.mkdir(parents=True)
    (mdir / "cat.pdf").write_bytes(b"%PDF-1.4")
    (mdir / "sources.json").write_text(json.dumps({
        "manufacturer": slug,
        "display_name": display,
        "category": category,
        "documents": [{
            "file": "cat.pdf",
            "title": f"{category} catalogue",
            "url": f"https://example.com/{slug}.pdf",
            "category": category,
        }],
    }), encoding="utf-8")


class FakeRetriever:
    def __init__(self, chunks, payloads):
        self._chunks = chunks
        self._payloads = payloads

    def retrieve(self, query, top_k=6, payload_filter=None):
        out = []
        for chunk in self._chunks:
            payload = self._payloads.get(chunk.chunk_id, {})
            if payload_filter and any(payload.get(k) != v for k, v in payload_filter.items()):
                continue
            out.append({"chunk": chunk, "score": 0.9})
        return out[:top_k]

    def close(self):
        pass


def _indexer(chunks_by_category, payloads):
    return lambda root, category_filter=None: (chunks_by_category, payloads, [])


def _factory():
    return lambda category, chunks, payloads: FakeRetriever(chunks, payloads)


# ── Report assembly ───────────────────────────────────────────────────────


def test_report_ranks_manufacturers_by_verified_evidence():
    tender = {"technical_specifications": [{
        "component": "Pump",
        "specs": {"capacity": ">= 10 m3/h", "body material": "SS316"},
        "source": {"chunk_id": "c1", "page": 2, "exact_text": "Pump capacity 10 m3/h, body SS316"},
    }]}
    acme_chunk = _chunk("Pump capacity 15 m3/h. Body material SS316.", slug="acme")
    beta_chunk = _chunk("Pump capacity 8 m3/h. Body material SS304.", slug="beta")
    chunks = {"pumps": [acme_chunk, beta_chunk]}
    payloads = {
        acme_chunk.chunk_id: {"manufacturer": "acme", "category": "pumps"},
        beta_chunk.chunk_id: {"manufacturer": "beta", "category": "pumps"},
    }
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_corpus(root, "acme", "ACME Pumps", "pumps")
        _write_corpus(root, "beta", "Beta Pumps", "pumps")
        report = matching.generate_match_report(
            tender,
            manufacturers_dir=str(root),
            tender_file="tender.pdf",
            judge=_judge_missing,
            corpus_indexer=_indexer(chunks, payloads),
            retriever_factory=_factory(),
        )

    assert isinstance(report, MatchReport)
    assert report.category == "pumps"
    assert [m.manufacturer for m in report.manufacturers] == ["ACME Pumps", "Beta Pumps"]
    acme, beta = report.manufacturers
    assert acme.rank == 1 and acme.overall_compatibility == 100.0 and acme.coverage == 100.0
    assert beta.rank == 2 and beta.overall_compatibility == 50.0
    assert acme.status_counts["full_match"] == 2
    assert beta.status_counts["partial_match"] == 1
    assert beta.status_counts["no_evidence"] == 1
    assert report.manufacturers[0].matches[0].evidence[0].document == "cat.pdf"


def test_category_scoping_sends_only_relevant_requirements():
    tender = {
        "procurement_items": [
            {"item_id": "1", "description": "Transfer Pump Without Motor", "category": "pumps"},
            {"item_id": "2", "description": "Drive Motor", "category": "motors"},
        ],
        "canonical_requirements": [
            {"requirement_id": "CR001", "boq_item_id": "1", "component": "Pump",
             "parameter": "flow_rate", "value": "15", "unit": "m3/h", "direction": "exact",
             "kind": "technical", "category": "pumps"},
            {"requirement_id": "CR002", "boq_item_id": "2", "component": "Motor",
             "parameter": "rated_power", "value": "2.2", "unit": "kW", "direction": "exact",
             "kind": "technical", "category": "motors"},
        ],
    }
    pump_chunk = _chunk("Flow rate 15 m3/h", slug="acme-pumps")
    motor_chunk = _chunk("Rated power 2.2 kW", slug="acme-motors")
    chunks = {"pumps": [pump_chunk], "motors": [motor_chunk]}
    payloads = {
        pump_chunk.chunk_id: {"manufacturer": "acme-pumps", "category": "pumps"},
        motor_chunk.chunk_id: {"manufacturer": "acme-motors", "category": "motors"},
    }
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_corpus(root, "acme-pumps", "Acme Pumps", "pumps")
        _write_corpus(root, "acme-motors", "Acme Motors", "motors")
        report = matching.generate_match_report(
            tender,
            manufacturers_dir=str(root),
            judge=_judge_missing,
            corpus_indexer=_indexer(chunks, payloads),
            retriever_factory=_factory(),
        )

    by_name = {m.manufacturer: m for m in report.manufacturers}
    pump_matches = {m.requirement_id: m for m in by_name["Acme Pumps"].matches}
    motor_matches = {m.requirement_id: m for m in by_name["Acme Motors"].matches}
    assert pump_matches["CR001"].status == "full_match"
    assert pump_matches["CR002"].status == "not_applicable"
    assert motor_matches["CR002"].status == "full_match"
    assert motor_matches["CR001"].status == "not_applicable"
    assert by_name["Acme Pumps"].overall_compatibility == 100.0
    assert by_name["Acme Pumps"].coverage == 100.0


def test_cross_manufacturer_requirement_keys_do_not_collide():
    tender = {"technical_specifications": [{
        "component": "Pump",
        "specs": {"capacity": "15 m3/h"},
    }]}
    alpha = _chunk("Pump capacity 15 m3/h.", slug="alpha")
    beta = _chunk("Pump capacity 8 m3/h.", slug="beta")
    chunks = {"pumps": [alpha, beta]}
    payloads = {
        alpha.chunk_id: {"manufacturer": "alpha", "category": "pumps"},
        beta.chunk_id: {"manufacturer": "beta", "category": "pumps"},
    }
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_corpus(root, "alpha", "Alpha", "pumps")
        _write_corpus(root, "beta", "Beta", "pumps")
        report = matching.generate_match_report(
            tender,
            manufacturers_dir=str(root),
            judge=_judge_missing,
            corpus_indexer=_indexer(chunks, payloads),
            retriever_factory=_factory(),
        )
    by_name = {m.manufacturer: m for m in report.manufacturers}
    assert by_name["Alpha"].matches[0].status == "full_match"
    assert by_name["Beta"].matches[0].status == "no_match"
    assert by_name["Alpha"].matches[0].evidence[0].chunk_id.startswith("alpha:")


def test_unknown_category_is_still_evaluated_against_available_catalogues():
    tender = {"technical_specifications": [{"component": "Equipment", "specs": {"material": "SS316"}}]}
    chunk = _chunk("Equipment material SS316.", slug="acme")
    chunks = {"pumps": [chunk]}
    payloads = {chunk.chunk_id: {"manufacturer": "acme", "category": "pumps"}}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_corpus(root, "acme", "ACME", "pumps")
        report = matching.generate_match_report(
            tender,
            manufacturers_dir=str(root),
            judge=_judge_missing,
            corpus_indexer=_indexer(chunks, payloads),
            retriever_factory=_factory(),
        )
    match = report.manufacturers[0].matches[0]
    assert match.status == "full_match"
    assert match.evidence[0].document == "cat.pdf"


def test_missing_requirements_raises_instead_of_reporting_an_empty_match():
    with pytest.raises(ValueError, match="No requirements could be extracted"):
        matching.generate_match_report({"technical_specifications": []})


# ── Coverage, ranking and the LLM budget ─────────────────────────────────


def test_coverage_and_compatibility_use_different_denominators():
    matches = [
        RequirementMatch(requirement_id="r1", manufacturer="M", status="full_match", score=100.0),
        RequirementMatch(requirement_id="r2", manufacturer="M", status="no_evidence", score=0.0),
        RequirementMatch(requirement_id="r3", manufacturer="M", status="not_applicable", score=0.0),
    ]
    counts, _, compatibility, coverage = matching._score_counts(matches)
    assert counts["full_match"] == 1 and counts["no_evidence"] == 1
    assert compatibility == 100.0      # score / decided
    assert coverage == 50.0            # decided / scorable (not_applicable excluded)


def test_low_coverage_ranks_last_even_with_high_compatibility():
    high_but_thin = matching.aggregate("Thin", [
        RequirementMatch(requirement_id="r1", manufacturer="Thin", status="full_match", score=100.0),
        *[
            RequirementMatch(requirement_id=f"r{i}", manufacturer="Thin", status="no_evidence", score=0.0)
            for i in range(2, 5)
        ],
    ])
    complete = matching.aggregate("Complete", [
        RequirementMatch(requirement_id="r1", manufacturer="Complete", status="full_match", score=100.0),
        RequirementMatch(requirement_id="r2", manufacturer="Complete", status="no_match", score=0.0),
    ])
    assert high_but_thin.low_coverage is True
    assert high_but_thin.overall_compatibility == 100.0
    assert complete.low_coverage is False
    ranked = matching._rank_results([high_but_thin, complete])
    assert [item.manufacturer for item in ranked] == ["Complete", "Thin"]
    assert [item.rank for item in ranked] == [1, 2]


def test_llm_answers_are_cached_by_prompt_hash(monkeypatch):
    from tender_extraction import extraction

    monkeypatch.setattr(extraction, "load_model", lambda: None)
    monkeypatch.setattr(extraction, "_repair_json", lambda raw: raw)
    calls = []

    def fake_call(llm, prompt, msg=""):
        calls.append(prompt)
        return json.dumps({
            "capability": "15 m3/h",
            "quotes": ["Flow 15 m3/h"],
            "page": 1,
            "document": "cat.pdf",
        })

    monkeypatch.setattr(extraction, "_call_llm", fake_call)
    matching._LLM_CACHE.clear()
    matching._reset_llm_budget()

    req = _req(parameter="flow_rate", value="15", unit="m3/h", direction="exact",
               text="Pump: flow_rate = 15 m3/h")
    hits = _hits(_chunk("Flow 15 m3/h"))

    first = matching._llm_extract(req, hits)
    second = matching._llm_extract(req, hits)
    assert first["capability"] == second["capability"] == "15 m3/h"
    assert len(calls) == 1
    assert matching._LLM_CACHE_HITS == 1


def test_llm_call_cap_stops_further_calls(monkeypatch):
    from tender_extraction import extraction

    monkeypatch.setattr(extraction, "load_model", lambda: None)
    monkeypatch.setattr(extraction, "_repair_json", lambda raw: raw)
    monkeypatch.setattr(
        extraction, "_call_llm",
        lambda llm, prompt, msg="": json.dumps({"capability": "x", "quotes": []}),
    )
    matching._LLM_CACHE.clear()
    matching._reset_llm_budget()
    monkeypatch.setattr(matching, "MAX_LLM_CALLS_PER_TENDER", 1)

    matching._llm_extract(_req(requirement_id="a"), _hits(_chunk("first prompt context")))
    capped = matching._llm_extract(_req(requirement_id="b"), _hits(_chunk("second prompt context")))
    assert capped["capability"] == "NOT_FOUND"
    assert "cap" in capped["reason"].lower()


# ── Adapter contract ─────────────────────────────────────────────────────


def test_adapter_entry_point_contract():
    import inspect
    from api import matching_adapter

    assert "generate_match_report" in matching_adapter._ENTRY_POINTS
    params = inspect.signature(matching.generate_match_report).parameters
    assert "manufacturers_dir" in params and "tender_file" in params
    assert params["manufacturers_dir"].kind == inspect.Parameter.KEYWORD_ONLY
    assert "manufacturer_categories" in params
