"""
test_api.py — Half B tests: API durability, upload safety, races, match seam.

These tests never call an LLM, Qdrant, or the extraction pipeline.  They use
FastAPI's TestClient and a fake job queue so the background worker stays idle.

Run:
    venv\\Scripts\\python.exe -m pytest tests/test_api.py -q
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api.main as main
from tender_extraction import cache


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Isolate uploads/outputs/state and capture queued jobs."""
    upload_dir = tmp_path / "uploads"
    output_dir = tmp_path / "outputs"
    upload_dir.mkdir()
    output_dir.mkdir()

    # Keep the real worker thread alive but make it a no-op: tests assert on
    # the queued job, not on pipeline execution (which needs torch/Qdrant).
    monkeypatch.setattr(main, "_run_pipeline", lambda job_id, file_path: None)
    monkeypatch.setattr(main, "UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(main, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(main, "STATE_DIR", output_dir)
    monkeypatch.setattr(main, "JOB_STATE_PATH", output_dir / "jobs_state.json")

    main.jobs.clear()
    main.chat_sessions.clear()
    main.chat_session_locks.clear()

    yield TestClient(main.app)

    main.jobs.clear()
    main.chat_sessions.clear()
    main.chat_session_locks.clear()


def _make_done_job(job_id: str = "job123456789", result: dict | None = None) -> Path:
    result_path = main.OUTPUT_DIR / f"{job_id}.json"
    default_result = {
        "technical_specifications": [{"component": "Pump", "specs": {"material": "SS316"}}],
        "canonical_requirements": [
            {"requirement_id": "r1", "boq_item_id": "1", "component": "Pump", "parameter": "material",
             "value": "SS316", "unit": "NOT_FOUND", "direction": "any", "kind": "technical"}
        ],
        "scope_of_work": {},
    }
    result = {"pipeline_version": main.PIPELINE_VERSION, **(result or default_result)}
    result_path.write_text(
        json.dumps(result),
        encoding="utf-8",
    )
    main.jobs[job_id] = {
        "job_id": job_id,
        "pipeline_version": result["pipeline_version"],
        "status": "done",
        "progress": 100,
        "message": "Complete",
        "filename": "tender.pdf",
        "file_path": str(main.UPLOAD_DIR / f"{job_id}.pdf"),
        "result_path": str(result_path),
        "created_at": 1.0,
        "updated_at": 2.0,
        "chat_history": [],
        "warnings": [],
    }
    return result_path


@pytest.mark.parametrize("version", ["", "older"])
def test_stale_results_are_flagged_but_only_missing_payloads_stop_matching(client, version):
    path = _make_done_job(result={
        "pipeline_version": version,
        "technical_specifications": [{"component": "Pump", "specs": {"material": "SS316"}}],
    })
    assert client.get('/jobs').json()[0]['stale']
    assert client.get('/jobs/job123456789/result').json()['stale']
    # Staleness is advisory; requirements still make the job matchable.
    unavailable, _ = main._match_unavailable(main.jobs['job123456789'])
    assert not unavailable
    if not version:
        path.write_text('{}', encoding='utf-8')
        unavailable, reason = main._match_unavailable(main.jobs['job123456789'])
        assert unavailable and 'No requirements could be extracted' in reason
    main.jobs['job123456789']['pipeline_version'] = main.PIPELINE_VERSION
    main._persist_state_locked()
    loaded = main._load_jobs_from_disk()
    assert main._public_job(loaded['job123456789'])['stale']


def test_rerun_preserves_original_and_rejects_duplicate(client):
    path = _make_done_job(result={"pipeline_version": "old"})
    original = path.read_bytes()
    source = Path(main.jobs['job123456789']['file_path'])
    source.write_bytes(b'%PDF-1.4 original')
    assert client.post('/jobs/job123456789/rerun', json={}).status_code == 400
    response = client.post('/jobs/job123456789/rerun', json={'confirm_model_usage': True})
    assert response.status_code == 201
    job = response.json()
    assert job['job_id'] != 'job123456789' and job['rerun_of'] == 'job123456789'
    assert Path(job['file_path']).read_bytes() == source.read_bytes()
    assert path.read_bytes() == original
    assert main.jobs['job123456789']['status'] == 'done'
    assert client.post('/jobs/job123456789/rerun', json={'confirm_model_usage': True}).status_code == 409


def test_rerun_missing_source_does_not_queue(client):
    _make_done_job()
    assert client.post('/jobs/job123456789/rerun', json={'confirm_model_usage': True}).status_code == 409
    assert len(main.jobs) == 1


# ── Config / health ────────────────────────────────────────────────────────

def test_cors_origins_are_parsed_from_env_format():
    assert main._parse_origins("http://a, http://b ,,") == ["http://a", "http://b"]


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "matching_engine" in body
    assert "cache" in body


# ── Upload ─────────────────────────────────────────────────────────────────

def test_upload_rejects_unsupported_suffix(client):
    response = client.post(
        "/upload", files={"file": ("notes.txt", b"hello", "text/plain")}
    )
    assert response.status_code == 415
    assert "Unsupported" in response.json()["detail"]
    assert list(main.UPLOAD_DIR.iterdir()) == []


def test_upload_rejects_empty_file(client):
    response = client.post(
        "/upload", files={"file": ("empty.pdf", b"", "application/pdf")}
    )
    assert response.status_code == 400
    assert list(main.UPLOAD_DIR.iterdir()) == []


def test_upload_rejects_oversize_and_removes_partial_file(client, monkeypatch):
    monkeypatch.setattr(main, "_max_upload_bytes", lambda: 8)
    response = client.post(
        "/upload", files={"file": ("big.pdf", b"x" * 64, "application/pdf")}
    )
    assert response.status_code == 413
    assert list(main.UPLOAD_DIR.iterdir()) == []
    assert main.jobs == {}


def test_upload_streams_to_disk_and_queues_job(client):
    response = client.post(
        "/upload", files={"file": ("tender.pdf", b"%PDF-1.4 data", "application/pdf")}
    )
    assert response.status_code == 201
    body = response.json()
    job_id = body["job_id"]
    assert len(job_id) == 12

    stored = main.UPLOAD_DIR / f"{job_id}.pdf"
    assert stored.exists()
    assert stored.read_bytes() == b"%PDF-1.4 data"

    job = main.jobs[job_id]
    assert job["status"] == "queued"
    assert job["size_bytes"] == len(b"%PDF-1.4 data")

    # The index is flushed to disk immediately.
    state = json.loads(main.JOB_STATE_PATH.read_text(encoding="utf-8"))
    assert job_id in state


def test_delete_job_removes_every_file_and_lock(client):
    response = client.post(
        "/upload", files={"file": ("tender.pdf", b"%PDF-1.4 data", "application/pdf")}
    )
    job_id = response.json()["job_id"]
    result_path = main.OUTPUT_DIR / f"{job_id}.json"
    result_path.write_text("{}", encoding="utf-8")
    chunks_path = main.OUTPUT_DIR / f"{job_id}_chunks.json"
    chunks_path.write_text("[]", encoding="utf-8")
    match_path = main.OUTPUT_DIR / f"{job_id}_match.json"
    match_path.write_text("{}", encoding="utf-8")

    main._set_job(
        job_id,
        status="done",
        result_path=str(result_path),
        progress=100,
        message="Complete",
    )
    main.chat_session_locks[job_id] = main.threading.Lock()

    delete = client.delete(f"/jobs/{job_id}")
    assert delete.status_code == 200
    assert job_id not in main.jobs
    assert job_id not in main.chat_session_locks
    assert not (main.UPLOAD_DIR / f"{job_id}.pdf").exists()
    assert not result_path.exists()
    assert not chunks_path.exists()
    assert not match_path.exists()


# ── Persistence / restart ──────────────────────────────────────────────────

def test_state_roundtrip_interrupts_running_jobs(tmp_path):
    result_path = tmp_path / "done.json"
    result_path.write_text("{}", encoding="utf-8")
    state_path = tmp_path / "jobs_state.json"
    state_path.write_text(
        json.dumps(
            {
                "running01": {
                    "job_id": "running01",
                    "status": "running",
                    "filename": "a.pdf",
                },
                "orphan002": {
                    "job_id": "orphan002",
                    "status": "done",
                    "result_path": str(tmp_path / "missing.json"),
                    "filename": "b.pdf",
                },
                "finished3": {
                    "job_id": "finished3",
                    "status": "done",
                    "result_path": str(result_path),
                    "filename": "c.pdf",
                },
            }
        ),
        encoding="utf-8",
    )

    loaded = main._load_jobs_from_disk(state_path)
    assert loaded["running01"]["status"] == "error"
    assert loaded["running01"]["interrupted"] is True
    assert loaded["orphan002"]["status"] == "error"
    assert loaded["finished3"]["status"] == "done"
    assert loaded["finished3"]["chat_history"] == []


def test_public_job_strips_history_and_aliases_grounding():
    public = main._public_job(
        {
            "job_id": "a",
            "status": "done",
            "chat_history": [{"secret": True}],
            "accuracy_score": 91.5,
        }
    )
    assert "chat_history" not in public
    assert public["grounding_score"] == 91.5
    assert public["accuracy_score"] == 91.5


def test_get_result_exposes_grounding_score(client):
    _make_done_job("resultjob001", {"accuracy_score": 87.25, "technical_specifications": []})
    response = client.get("/jobs/resultjob001/result")
    assert response.status_code == 200
    body = response.json()
    assert body["grounding_score"] == 87.25
    assert body["accuracy_score"] == 87.25  # legacy key still present
    assert body["warnings"] == []


def test_get_result_not_ready(client):
    main.jobs["pending001"] = {"job_id": "pending001", "status": "running"}
    assert client.get("/jobs/pending001/result").json() == {"error": "not ready"}


# ── Match report seam ──────────────────────────────────────────────────────

def _sample_report() -> dict:
    return {
        "tender_file": "tender.pdf",
        "requirements": [
            {
                "requirement_id": "r1",
                "text": "Capacity shall be at least 10 m3/h",
                "parameter": "capacity",
                "value": "10",
                "unit": "m3/h",
                "direction": "min",
                "kind": "technical",
                "source": {"chunk_id": "c1", "page": 4, "clause": "3.2", "exact_text": "capacity at least 10 m3/h"},
            }
        ],
        "manufacturers": [
            {
                "manufacturer": "KSB",
                "overall_compatibility": 100.0,
                "rank": 1,
                "status_counts": {"full_match": 1},
                "matches": [
                    {
                        "requirement_id": "r1",
                        "manufacturer": "KSB",
                        "capability": "Etanorm",
                        "status": "full_match",
                        "score": 100.0,
                        "evidence": [
                            {"document": "ksb.pdf", "page": 2, "quote": "Q = 12 m3/h", "chunk_id": "k1"}
                        ],
                        "confidence": 0.94,
                        "needs_review": False,
                        "reason": "Datasheet exceeds the requested flow.",
                    }
                ],
            }
        ],
        "generated_at": 0.0,
        "notes": [],
    }


def test_match_get_without_report_reports_engine_state(client):
    _make_done_job("matchjob0001")
    body = client.get("/jobs/matchjob0001/match").json()
    assert body["error"] == "match report not generated"
    assert "engine_available" in body


def test_match_post_writes_report_and_summary(client, monkeypatch):
    _make_done_job("matchjob0002")
    report = _sample_report()
    monkeypatch.setattr(
        main.matching_adapter,
        "generate_match_report",
        lambda tender_result, tender_file="", **kwargs: (report, None),
    )

    response = client.post("/jobs/matchjob0002/match")
    assert response.status_code == 200
    body = response.json()
    assert body["manufacturers"][0]["manufacturer"] == "KSB"

    report_path = main.OUTPUT_DIR / "matchjob0002_match.json"
    assert report_path.exists()
    assert json.loads(report_path.read_text(encoding="utf-8"))["manufacturers"][0]["rank"] == 1

    status = client.get("/jobs/matchjob0002/status").json()
    assert status["match_summary"]["best_manufacturer"] == "KSB"
    assert status["match_summary"]["best_score"] == 100.0
    assert status["match_score"] == 100.0

    # GET now serves the written report.
    fetched = client.get("/jobs/matchjob0002/match").json()
    assert fetched["requirements"][0]["requirement_id"] == "r1"


def test_match_post_reports_missing_engine(client, monkeypatch):
    _make_done_job("matchjob0003")
    monkeypatch.setattr(
        main.matching_adapter,
        "generate_match_report",
        lambda tender_result, tender_file="", **kwargs: (None, "engine missing"),
    )
    body = client.post("/jobs/matchjob0003/match").json()
    assert body["error"] == "engine missing"
    assert "engine_available" in body


def test_match_post_rejects_invalid_report(client, monkeypatch):
    _make_done_job("matchjob0004")
    monkeypatch.setattr(
        main.matching_adapter,
        "generate_match_report",
        lambda tender_result, tender_file="", **kwargs: ({"manufacturers": "not-a-list"}, None),
    )
    body = client.post("/jobs/matchjob0004/match").json()
    assert "invalid report" in body["error"]


# ── Deterministic chunk IDs ────────────────────────────────────────────────

def test_chunk_ids_are_deterministic():
    pytest.importorskip("sentence_transformers")
    from tender_extraction.chunking import create_chunks

    pages = [{
        "page": 2,
        "text": "1 Scope\nSupply ten pumps.\n\n2 Technical\nCapacity shall be at least 10 m3/h.",
        "is_ocr": False,
        "headings": [],
    }]
    first = create_chunks(pages, tables=None, use_semantic=False)
    second = create_chunks(pages, tables=None, use_semantic=False)
    assert [c.chunk_id for c in first] == [c.chunk_id for c in second]

    tables = [{
        "table_id": "table_001",
        "page": 3,
        "headers": ["Item", "Specification"],
        "rows": [["Pump", "10 m3/h"], ["Valve", "SS316"]],
    }]
    t1 = create_chunks([], tables, use_semantic=False)
    t2 = create_chunks([], tables, use_semantic=False)
    assert [c.chunk_id for c in t1] == [c.chunk_id for c in t2]
    assert len({c.chunk_id for c in t1}) == 2


def test_qa_topic_uses_cached_chunks_not_ingestion(monkeypatch):
    # qdrant is checked first so the skip is instant on minimal dev machines.
    pytest.importorskip("qdrant_client")
    pytest.importorskip("sentence_transformers")
    from tender_extraction import qa
    from tender_extraction.schemas import Chunk, ChunkMetadata

    def _boom(_path):
        raise AssertionError("ingest_document must not be called when chunks are cached")

    monkeypatch.setattr(qa, "ingest_document", _boom)
    topic = qa._topic_from_chunks(
        [Chunk(chunk_id="c1", text="Procurement of centrifugal pumps for water supply", metadata=ChunkMetadata(page=1))]
    )
    assert topic
    assert "pump" in topic.lower()


# ── Cache fallback ─────────────────────────────────────────────────────────

def test_cache_is_disabled_without_redis(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setattr(cache, "_client", None)
    monkeypatch.setattr(cache, "_client_checked", False)
    assert cache.enabled() is False
    assert cache.get_json("missing") is None
    cache.set_json("missing", {"a": 1})  # no-op, must not raise
    assert cache.delete_prefix("missing") == 0


def test_match_endpoint_runs_real_engine_and_adapter(client, monkeypatch, tmp_path):
    from types import SimpleNamespace
    from tender_extraction import matching
    from tender_extraction.schemas import Chunk, ChunkMetadata

    corpus = tmp_path / "corpus"
    maker = corpus / "acme"
    maker.mkdir(parents=True)
    (maker / "cat.pdf").write_bytes(b"%PDF-1.4 test")
    (maker / "sources.json").write_text(json.dumps({
        "manufacturer": "acme", "display_name": "ACME", "category": "pumps",
        "documents": [{"file": "cat.pdf", "url": "https://example.com/cat.pdf",
                       "retrieved_at": "2026-11-18", "pages": 1, "doc_type": "datasheet"}],
    }), encoding="utf-8")
    chunk = Chunk(chunk_id="acme:cat.pdf:row1", text="Pump capacity 15 m3/h.", metadata=ChunkMetadata(page=2, document="cat.pdf"))
    monkeypatch.setenv("MANUFACTURERS_DIR", str(corpus))
    monkeypatch.setattr(matching, "_default_corpus_indexer", lambda root: (
        {"pumps": [chunk]}, {chunk.chunk_id: {"manufacturer": "acme", "category": "pumps"}}, [],
    ))
    monkeypatch.setattr(matching, "_default_retriever_factory", lambda *args: SimpleNamespace(
        retrieve=lambda *args, **kwargs: [{"chunk": chunk, "score": 0.9}],
    ))
    llm_calls = []
    def no_llm(*args):
        llm_calls.append(args)
        raise AssertionError("Numeric evidence must not call the LLM")
    monkeypatch.setattr(matching, "_llm_extract", no_llm)
    _make_done_job("integrated01", {"technical_specifications": [
        {"component": "Pump", "specs": {"capacity": ">= 10 m3/h"}},
    ]})
    response = client.post("/jobs/integrated01/match")
    assert response.status_code == 200
    report = response.json()
    match = report["manufacturers"][0]["matches"][0]
    assert match["status"] == "full_match" and match["score"] == 100
    assert match["evidence"][0]["document"] == "cat.pdf"
    assert match["evidence"][0]["page"] == 2
    assert not llm_calls
    assert client.get("/jobs/integrated01/match").json() == report


def test_incomplete_tender_blocks_fresh_cached_and_company_scores(client, monkeypatch):
    job_id = "incomplete01"
    _make_done_job(job_id, {"technical_specifications": [], "canonical_requirements": [], "scope_of_work": {}})
    main._match_path(main.jobs[job_id]).write_text(json.dumps(_sample_report()), encoding="utf-8")
    def never(*args, **kwargs):
        raise AssertionError("An incomplete tender must not reach the matching engine")
    monkeypatch.setattr(main.matching_adapter, "generate_match_report", never)
    for response in (client.get(f"/jobs/{job_id}/match"), client.post(f"/jobs/{job_id}/match"),
                     client.get(f"/jobs/{job_id}/score")):
        body = response.json()
        assert "No requirements could be extracted" in body["error"]
        assert "match_score" not in body and "manufacturers" not in body
