"""
test_matching_step3.py — Status semantics and aggregate accounting.

Complements test_matching.py: no_evidence must always carry an audit trail,
mandatory counts stay separate, and not_applicable never enters the score.
"""

from tender_extraction.matching import aggregate, match_requirement
from tender_extraction.schemas import Chunk, ChunkMetadata
from tender_extraction.schemas_match import Requirement, RequirementMatch


def _req(req_id="r1", parameter="size", value="DN150", kind="technical", is_mandatory=False):
    return Requirement(
        requirement_id=req_id,
        boq_item_id="1",
        text=f"SYNTHETIC: {parameter} shall be {value}",
        parameter=parameter,
        value=value,
        unit="mm",
        direction="exact",
        kind=kind,
        is_mandatory=is_mandatory,
    )


def _fake_judge(req, hits):
    return {"capability": "NOT_FOUND", "quotes": [], "reason": "SYNTHETIC: nothing found"}


def _chunk(text, page=1, doc="SYNTHETIC_doc.pdf"):
    return Chunk(chunk_id=f"{doc}:{page}", text=text,
                 metadata=ChunkMetadata(page=page, document=doc))


def test_no_evidence_on_empty_hits_carries_audit_trail():
    result = match_requirement(_req(), "SYNTHETIC_MFR", [], _fake_judge)
    assert result.status == "no_evidence"
    assert result.audit is not None
    assert isinstance(result.audit.searched_queries, list)
    assert "No relevant catalogue content" in result.reason


def test_catalogue_silence_is_no_evidence_not_no_match():
    result = match_requirement(_req(), "SYNTHETIC_MFR", [], _fake_judge)
    assert result.status == "no_evidence"
    assert result.status != "no_match"


def test_no_match_requires_evidence_that_fails():
    hits = [{"chunk": _chunk("SYNTHETIC: valve size is 100 mm"), "score": 0.8}]
    req = _req(value="150")
    result = match_requirement(req, "SYNTHETIC_MFR", hits, _fake_judge)
    assert result.status == "no_match"


def test_commercial_requirement_is_not_applicable():
    result = match_requirement(
        _req(kind="commercial"), "SYNTHETIC_MFR",
        [{"chunk": _chunk("SYNTHETIC commercial terms"), "score": 0.8}],
        _fake_judge,
    )
    assert result.status == "not_applicable"


def test_aggregate_counts_all_five_statuses():
    matches = [
        RequirementMatch(requirement_id="r1", manufacturer="M", status="full_match", score=100.0),
        RequirementMatch(requirement_id="r2", manufacturer="M", status="partial_match", score=50.0),
        RequirementMatch(requirement_id="r3", manufacturer="M", status="no_match", score=0.0),
        RequirementMatch(requirement_id="r4", manufacturer="M", status="no_evidence", score=0.0),
        RequirementMatch(requirement_id="r5", manufacturer="M", status="not_applicable", score=0.0),
    ]
    result = aggregate("SYNTHETIC_MFR", matches)
    assert result.status_counts == {
        "full_match": 1, "partial_match": 1, "no_match": 1,
        "no_evidence": 1, "not_applicable": 1,
    }
    # (100 + 50 + 0) / 3 decided = 50; not_applicable and no_evidence excluded.
    assert result.overall_compatibility == 50.0
    # 3 decided / 4 scorable = 75.
    assert result.coverage == 75.0
    assert result.low_coverage is False


def test_aggregate_mandatory_counts():
    matches = [
        RequirementMatch(requirement_id="r1", manufacturer="M", status="full_match", score=100.0, is_mandatory=True),
        RequirementMatch(requirement_id="r2", manufacturer="M", status="no_match", score=0.0, is_mandatory=True),
        RequirementMatch(requirement_id="r3", manufacturer="M", status="no_evidence", score=0.0, is_mandatory=True),
    ]
    result = aggregate("SYNTHETIC_MFR", matches)
    assert result.mandatory_counts["passes"] == 1
    assert result.mandatory_counts["fails"] == 1
    assert result.mandatory_counts["no_evidence"] == 1
