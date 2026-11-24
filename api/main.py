"""
api/main.py â€” FastAPI surface for TenderExtractPro.

Half B responsibilities:
  * durable job index (survives restarts)
  * streamed, size-capped uploads with real HTTP errors
  * race-free job/chat state
  * environment-driven CORS
  * match-report endpoints (requirement x manufacturer) + Redis read cache

The extraction pipeline itself lives in `tender_extraction/` and is owned by
Half A.  Heavy imports stay inside handlers so API tests do not need torch,
Qdrant or a Groq key.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

# Pre-initialize environment and model cache paths before any ML or HuggingFace imports
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")
os.environ.setdefault("HF_HOME", "D:/hf_cache")
os.environ.setdefault("PADDLE_PDX_CACHE_HOME", "D:/paddlex_cache")

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from api import matching_adapter
from tender_extraction import PIPELINE_VERSION, cache
from tender_extraction.schemas_match import MatchReport

logger = logging.getLogger("uvicorn")

# â”€â”€ Paths & configuration â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", BASE_DIR / "uploads"))
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", BASE_DIR / "outputs"))
STATE_DIR = Path(os.getenv("STATE_DIR", BASE_DIR / "outputs"))
JOB_STATE_PATH = Path(os.getenv("JOB_STATE_PATH", STATE_DIR / "jobs_state.json"))

for _directory in (UPLOAD_DIR, OUTPUT_DIR, STATE_DIR, JOB_STATE_PATH.parent):
    _directory.mkdir(parents=True, exist_ok=True)


def _parse_origins(raw: str) -> List[str]:
    """Split a comma-separated CORS_ORIGINS value, trimming blanks."""
    return [item.strip() for item in raw.split(",") if item.strip()]


CORS_ORIGINS = _parse_origins(
    os.getenv(
        "CORS_ORIGINS",
        "http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173,http://127.0.0.1:3000",
    )
)

SUPPORTED_UPLOAD_SUFFIXES = {".pdf", ".docx", ".jpg", ".jpeg", ".png"}

app = FastAPI(title="TenderExtractPro", version="2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# â”€â”€ In-process state (persisted to JOB_STATE_PATH) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

jobs: Dict[str, Dict[str, Any]] = {}
chat_sessions: Dict[str, Any] = {}          # job_id -> DocumentChatSession
chat_session_locks: Dict[str, threading.Lock] = {}
job_queue: "queue.Queue[Optional[tuple]]" = queue.Queue()

_jobs_lock = threading.RLock()              # guards `jobs` + state file


def _persist_state_locked() -> None:
    """Atomically write the job index. Caller must hold `_jobs_lock`."""
    try:
        tmp = JOB_STATE_PATH.with_name(JOB_STATE_PATH.name + ".tmp")
        tmp.write_text(
            json.dumps(jobs, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        os.replace(tmp, JOB_STATE_PATH)
    except Exception as exc:  # noqa: BLE001 - never fail a request on persistence
        logger.warning("Could not persist job state: %s", exc)


def _load_jobs_from_disk(path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """Read the job index and mark orphaned in-flight jobs as interrupted."""
    state_path = Path(path or JOB_STATE_PATH)
    if not state_path.exists():
        return {}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Ignoring unreadable job state %s: %s", state_path, exc)
        return {}
    if not isinstance(data, dict):
        return {}

    now = time.time()
    for job_id, job in list(data.items()):
        if not isinstance(job, dict):
            data.pop(job_id, None)
            continue
        job.setdefault("job_id", job_id)
        job.setdefault("chat_history", [])
        job.setdefault("warnings", [])

        status = job.get("status")
        if status in {"queued", "running"}:
            # The worker thread did not survive the restart; the pipeline is
            # not resumable, so fail loudly instead of showing a frozen job.
            job.update(
                status="error",
                interrupted=True,
                message=(
                    "Server restarted before this job finished. "
                    "Please re-upload the document."
                ),
                updated_at=now,
            )
        elif status == "done":
            result_path = job.get("result_path")
            if not result_path or not Path(result_path).exists():
                job.update(
                    status="error",
                    message="Result file is missing. Please re-upload the document.",
                    updated_at=now,
                )
            else:
                try:
                    result = json.loads(Path(result_path).read_text(encoding="utf-8"))
                    job["pipeline_version"] = result.get("pipeline_version", "")
                except (OSError, ValueError):
                    job["pipeline_version"] = ""
    return data


jobs = _load_jobs_from_disk()


def _set_job(job_id: str, persist: bool = True, **fields: Any) -> bool:
    """Update one job under the lock; optionally flush the index."""
    with _jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            return False
        job.update(fields)
        if persist:
            _persist_state_locked()
        return True


def _public_job(job: Dict[str, Any]) -> Dict[str, Any]:
    """Job payload without the (potentially large) chat history."""
    from tender_extraction import PIPELINE_VERSION

    data = {key: value for key, value in job.items() if key != "chat_history"}
    # Consumers should use `grounding_score`; keep the old key for old clients.
    if "grounding_score" not in data and "accuracy_score" in data:
        data["grounding_score"] = data["accuracy_score"]
    data["processing"] = data.get("status", "queued")
    data["stage"] = data.get("stage") or data.get("message") or data.get("status")
    data["stale"] = data.get("status") == "done" and data.get("pipeline_version") != PIPELINE_VERSION
    # Graceful migration: read req / item counts if missing
    if "requirements_count" not in data or "items_count" not in data:
        res_path = Path(data.get("result_path") or "")
        if res_path.is_file():
            try:
                res_json = json.loads(res_path.read_text(encoding="utf-8"))
                data.setdefault("requirements_count", len(res_json.get("canonical_requirements") or res_json.get("technical_specifications") or []))
                data.setdefault("items_count", len(res_json.get("procurement_items") or []))
            except Exception:
                pass
    data.setdefault("requirements_count", 0)
    data.setdefault("items_count", 0)
    return data


def _max_upload_bytes() -> int:
    from tender_extraction.config import config

    return max(1, int(config.max_file_size_mb)) * 1024 * 1024


def _resolve_upload_path(job_id: str, filename: str | None) -> Path:
    """Validate the suffix and return the on-disk upload path."""
    suffix = Path(filename or "").suffix.lower()
    if suffix not in SUPPORTED_UPLOAD_SUFFIXES:
        raise ValueError(f"Unsupported upload type: {suffix or 'unknown'}")
    return UPLOAD_DIR / f"{job_id}{suffix}"


def _safe_unlink(path: Any) -> None:
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Could not delete %s: %s", path, exc)


def _match_path(job: Dict[str, Any]) -> Path:
    """Match reports sit next to the extraction result."""
    result_path = job.get("result_path")
    if result_path:
        p = Path(result_path)
        return p.with_name(f"{p.stem}_match.json")
    return OUTPUT_DIR / f"{job.get('job_id', 'unknown')}_match.json"


def _match_unavailable(job: Dict[str, Any]) -> tuple[bool, str]:
    """Return ``(unavailable, reason)`` for a job that has no matchable result.

    This is not a tender-completeness gate: a result with any requirement is
    matchable.  The only hard stop is a missing/unreadable payload or zero
    extracted requirements.
    """
    target = Path(job.get("result_path") or "")
    if not target.is_file():
        return True, "Result payload is unavailable; matching cannot run."
    try:
        result = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True, "Result payload is unreadable; matching cannot run."
    # Only hard stop: zero requirements extracted from all documents
    has_specs = bool(result.get("technical_specifications") or result.get("canonical_requirements"))
    if not has_specs:
        notes = result.get("notes") or []
        reason = "; ".join(str(n) for n in notes[:2]) if notes else "attachments not found or OCR failed"
        return True, f"No requirements could be extracted from the uploaded documents: {reason}"
    return False, ""


def _collect_warnings(result: Dict[str, Any]) -> List[str]:
    """Normalise OCR/validation warnings emitted by the pipeline."""
    warnings: List[str] = []
    for key in ("warnings", "review_warnings", "notes"):
        value = result.get(key)
        if isinstance(value, list):
            warnings.extend(str(item) for item in value if item)
        elif isinstance(value, str) and value.strip():
            warnings.append(value.strip())

    for key in ("ocr_failed_pages", "ocr_failures"):
        value = result.get(key)
        if isinstance(value, list) and value:
            pages = ", ".join(str(page) for page in value[:20])
            warnings.append(
                f"OCR failed on {len(value)} page(s): {pages}. "
                "Evidence from these pages may be incomplete."
            )
        elif isinstance(value, dict) and value:
            pages = ", ".join(str(page) for page in list(value)[:20])
            warnings.append(
                f"OCR failed on {len(value)} page(s): {pages}. "
                "Evidence from these pages may be incomplete."
            )
    # De-duplicate while preserving order.
    return list(dict.fromkeys(warnings))


# â”€â”€ Background worker â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _worker() -> None:
    """Process extraction jobs sequentially in a daemon thread."""
    while True:
        job_data = job_queue.get()
        try:
            if job_data is None:
                break
            job_id, file_path = job_data
            _run_pipeline(job_id, file_path)
        except Exception as exc:  # noqa: BLE001
            logger.error("Worker error: %s", exc)
        finally:
            job_queue.task_done()


threading.Thread(target=_worker, daemon=True, name="tender-worker").start()


# â”€â”€ Models â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class AskRequest(BaseModel):
    question: str


# â”€â”€ Health / upload â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "jobs": len(jobs),
        "matching_engine": matching_adapter.is_available(),
        "cache": cache.enabled(),
        "timestamp": time.time(),
    }


@app.post("/upload", status_code=201)
async def upload(file: UploadFile = File(...)):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in SUPPORTED_UPLOAD_SUFFIXES:
        raise HTTPException(
            status_code=415,
            detail=(
                f"Unsupported file type '{suffix or 'unknown'}'. "
                f"Allowed: {', '.join(sorted(SUPPORTED_UPLOAD_SUFFIXES))}"
            ),
        )

    job_id = uuid.uuid4().hex[:12]
    file_path = UPLOAD_DIR / f"{job_id}{suffix}"
    max_bytes = _max_upload_bytes()
    total = 0
    oversize = False

    # Stream to disk in 1 MiB blocks so a 2 GB upload never lands in RAM.
    try:
        with open(file_path, "wb") as out:
            while True:
                block = await file.read(1024 * 1024)
                if not block:
                    break
                total += len(block)
                if total > max_bytes:
                    oversize = True
                    break
                out.write(block)
    finally:
        await file.close()

    if oversize:
        _safe_unlink(file_path)
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds the {max_bytes // (1024 * 1024)} MB limit.",
        )
    if total == 0:
        _safe_unlink(file_path)
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    now = time.time()
    with _jobs_lock:
        jobs[job_id] = {
            "status": "queued",
            "progress": 0,
            "message": "Waiting in queue...",
            "filename": file.filename or file_path.name,
            "job_id": job_id,
            "result_path": None,
            "created_at": now,
            "started_at": None,
            "updated_at": now,
            "file_path": str(file_path),
            "size_bytes": total,
            "warnings": [],
            "chat_history": [],
        }
        _persist_state_locked()

    job_queue.put((job_id, str(file_path)))
    return {"job_id": job_id, "filename": file.filename, "size_bytes": total}


# â”€â”€ Pipeline runner â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _run_pipeline(job_id: str, file_path: str) -> None:
    heartbeat_stop = threading.Event()
    started_at = time.time()
    try:
        import sys

        sys.path.insert(0, str(BASE_DIR))
        from tender_extraction.main import TenderExtractionPipeline

        _set_job(
            job_id,
            status="running",
            progress=5,
            message="Starting pipeline...",
            started_at=started_at,
            updated_at=started_at,
        )
        stage_state = {"message": "Starting pipeline...", "progress": 5}

        def _heartbeat() -> None:
            while not heartbeat_stop.wait(5):
                with _jobs_lock:
                    job = jobs.get(job_id)
                    if not job or job.get("status") != "running":
                        continue
                    elapsed = int(time.time() - started_at)
                    job["message"] = f"{stage_state['message']} ({elapsed}s)"
                    job["updated_at"] = time.time()

        threading.Thread(target=_heartbeat, daemon=True).start()

        def _progress(pct: int, msg: str) -> None:
            stage_state.update({"message": msg, "progress": pct})
            stg = "extracting"
            if pct >= 95 or "matching" in msg.lower():
                stg = "matching"
            _set_job(
                job_id,
                progress=pct,
                message=msg,
                status="running",
                stage=stg,
                updated_at=time.time(),
            )

        output_path = str(OUTPUT_DIR / f"{job_id}.json")
        pipeline = TenderExtractionPipeline()
        result = pipeline.run(
            file_path, output_path=output_path, progress_callback=_progress
        )

        warnings = _collect_warnings(result)
        grounding = result.get(
            "grounding_score", result.get("accuracy_score", 0.0)
        )
        completion: Dict[str, Any] = {
            "grounding_score": grounding,
            "warnings": warnings,
            "pipeline_version": result.get("pipeline_version", ""),
        }

        # Optional legacy company-profile score.
        profile_path = BASE_DIR / "company_profile.json"
        if profile_path.exists():
            try:
                _progress(93, "Ranking against company profile...")
                from tender_extraction.scoring import score_tender_match

                profile = json.loads(profile_path.read_text(encoding="utf-8"))
                score_res = score_tender_match(profile, result)
                completion["match_score"] = score_res.get("match_score")
                completion["match_data"] = score_res
            except Exception as exc:  # noqa: BLE001
                logger.warning("Auto-scoring failed for %s: %s", job_id, exc)

        # Optional automatic manufacturer matching.  Re-runs inherit this when
        # the original job already had a match report.
        with _jobs_lock:
            job_snapshot = dict(jobs.get(job_id) or {})
        auto_match = (
            os.getenv("MATCH_AUTORUN", "0").lower() in {"1", "true", "yes"}
            or bool(job_snapshot.get("auto_match"))
        )
        if auto_match:
            _progress(96, "Matching requirements against manufacturer catalogues...")
            options = job_snapshot.get("match_options") or {}
            report, reason = matching_adapter.generate_match_report(
                result,
                file_path,
                selected_manufacturers=options.get("selected_manufacturers") or None,
                category_filter=options.get("category_filter") or None,
                manufacturer_categories=options.get("manufacturer_categories") or None,
            )
            if report is not None:
                try:
                    report = MatchReport(**report).model_dump()
                    _write_match_report(job_id, report)
                    completion["match_summary"] = _match_summary(report)
                    if completion["match_summary"].get("best_score") is not None:
                        completion["match_score"] = completion["match_summary"]["best_score"]
                except Exception as exc:  # noqa: BLE001
                    warnings.append(f"Match report could not be stored: {exc}")
                    completion["warnings"] = warnings
            elif reason:
                warnings.append(reason)
                completion["warnings"] = warnings

        specs = len(result.get("technical_specifications", []))
        reqs = len(result.get("canonical_requirements", [])) or specs
        items = len(result.get("procurement_items", []))
        deliverables = len(result.get("scope_of_work", {}).get("deliverables", []))
        msg = f"Complete â€” {reqs} requirements, {items} items, {deliverables} deliverables"
        reconciliation = result.get("reconciliation") or {}
        completion["requirements_count"] = reqs
        completion["items_count"] = items
        completion["reconciliation_summary"] = reconciliation.get("summary", {})
        if completion.get("match_score") is not None:
            msg += f" (Company fit: {completion['match_score']}%)"
        summary = completion.get("match_summary")
        if summary and summary.get("best_manufacturer"):
            msg += f" (Best: {summary['best_manufacturer']} {summary['best_score']}%)"

        completion.update(
            progress=100,
            status="done",
            stage="done",
            result_path=output_path,
            message=msg,
            updated_at=time.time(),
        )
        _set_job(job_id, **completion)

    except Exception as exc:  # noqa: BLE001 - surfaced to the UI
        logger.exception("Pipeline failed for %s", job_id)
        _set_job(job_id, status="error", message=str(exc), updated_at=time.time())
    finally:
        heartbeat_stop.set()


# â”€â”€ Job endpoints â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@app.get("/jobs")
def list_jobs() -> List[Dict[str, Any]]:
    # Deep-enough snapshot under the lock: an upload may insert and the worker
    # may add keys while the UI polls.
    with _jobs_lock:
        snapshot = [dict(job) for job in jobs.values()]
    return [_public_job(job) for job in snapshot]


@app.get("/jobs/{job_id}/status")
def get_status(job_id: str) -> Dict[str, Any]:
    with _jobs_lock:
        job = jobs.get(job_id)
        snapshot = dict(job) if job else None
    if snapshot is None:
        return {"error": "not found"}
    return _public_job(snapshot)


@app.get("/jobs/{job_id}/result")
def get_result(job_id: str) -> Dict[str, Any]:
    with _jobs_lock:
        job = dict(jobs.get(job_id) or {})
    if not job or job.get("status") != "done":
        return {"error": "not ready"}
    try:
        data = json.loads(Path(job["result_path"]).read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"error": f"result unavailable: {exc}"}

    # Rename in the consumer contract while keeping the legacy key for
    # clients that have not been updated yet.
    if "grounding_score" not in data and "accuracy_score" in data:
        data["grounding_score"] = data["accuracy_score"]
    # Obsolete blocking flags from older result files must not reach the UI.
    data.pop("tender_complete", None)
    data.pop("matching_blocked", None)
    data.setdefault("review_notes", [])
    data.setdefault("category", "")
    data.setdefault("categories", [])
    data.setdefault("warnings", job.get("warnings", []))
    data["stale"] = data.get("pipeline_version") != PIPELINE_VERSION
    data["current_pipeline_version"] = PIPELINE_VERSION
    return data


class RerunRequest(BaseModel):
    confirm_model_usage: bool = False
    run_matching: bool = True


@app.post("/jobs/{job_id}/rerun", status_code=201)
def rerun_job(job_id: str, payload: RerunRequest) -> Dict[str, Any]:
    if not payload.confirm_model_usage:
        raise HTTPException(400, "Confirm model usage before re-running extraction.")
    with _jobs_lock:
        original = jobs.get(job_id)
        if original is None:
            raise HTTPException(404, "Job not found.")
        if original.get("status") not in {"done", "error"}:
            raise HTTPException(409, "This extraction is already queued or running.")
        if any(job.get("rerun_of") == job_id and job.get("status") in {"queued", "running"} for job in jobs.values()):
            raise HTTPException(409, "A re-run is already queued or running.")
        source = Path(original.get("file_path") or "")
        if not source.is_file():
            raise HTTPException(409, "Original upload is missing; upload it again.")
        new_id = uuid.uuid4().hex[:12]
        target = _resolve_upload_path(new_id, source.name)
        attachments = OUTPUT_DIR / f"{new_id}_attachments"
        try:
            shutil.copy2(source, target)
            previous = OUTPUT_DIR / f"{job_id}_attachments"
            if previous.is_dir():
                shutil.copytree(previous, attachments)
        except OSError as exc:
            _safe_unlink(target)
            shutil.rmtree(attachments, ignore_errors=True)
            raise HTTPException(500, "Could not prepare the re-run files.") from exc
        now = time.time()
        had_match = _match_path(original).exists()
        job = {
            "job_id": new_id, "rerun_of": job_id, "status": "queued",
            "stage": "queued", "processing": "queued",
            "progress": 0, "message": "Waiting in queue...",
            "filename": original["filename"], "file_path": str(target),
            "result_path": None, "created_at": now, "updated_at": now,
            "started_at": None, "size_bytes": target.stat().st_size,
            "warnings": [], "chat_history": [],
            "auto_match": bool(payload.run_matching or had_match),
            "match_options": original.get("match_options") or {},
        }
        original["superseded_by"] = new_id
        jobs[new_id] = job
        _persist_state_locked()
        snapshot = _public_job(job)
    job_queue.put((new_id, str(target)))
    return snapshot


_indexed_categories_cache: Dict[str, int] = {}
_indexed_categories_time: float = 0.0


def _indexed_categories() -> Dict[str, int]:
    """Category -> point count for the persisted ``cat_<category>`` collections."""
    global _indexed_categories_cache, _indexed_categories_time
    now = time.time()
    if _indexed_categories_cache and (now - _indexed_categories_time) < 60.0:
        return dict(_indexed_categories_cache)
    try:
        from tender_extraction.retrieval import get_qdrant_client

        client = get_qdrant_client()
        counts: Dict[str, int] = {}
        for collection in client.get_collections().collections:
            if not collection.name.startswith("cat_"):
                continue
            try:
                info = client.get_collection(collection.name)
                counts[collection.name[4:]] = int(info.points_count or 0)
            except Exception:  # noqa: BLE001 - report zero, not a 500
                counts[collection.name[4:]] = 0
        _indexed_categories_cache = counts
        _indexed_categories_time = now
        return counts
    except Exception as exc:  # noqa: BLE001 - the endpoint stays usable
        logger.warning("Could not inspect Qdrant collections: %s", exc)
        return dict(_indexed_categories_cache)


@app.get("/manufacturers")
def list_manufacturers(category: Optional[str] = None) -> Dict[str, Any]:
    from api.matching_adapter import manufacturers_dir
    from tender_extraction.matching import load_manufacturers
    mfrs, notes = load_manufacturers(str(manufacturers_dir()))
    indexed = _indexed_categories()
    for manufacturer in mfrs:
        manufacturer["indexed"] = any(
            indexed.get(item, 0) > 0 for item in (manufacturer.get("categories") or [])
        )
    categories = sorted({
        item
        for manufacturer in mfrs
        for item in (manufacturer.get("categories") or [])
        if item
    })
    if category:
        clean_cat = category.strip().lower()
        mfrs = [m for m in mfrs if clean_cat in (m.get("categories") or [])]
    return {
        "manufacturers": mfrs,
        "count": len(mfrs),
        "categories": categories,
        "indexed_categories": indexed,
        "notes": notes,
    }


@app.delete("/jobs/{job_id}")
def delete_job(job_id: str) -> Dict[str, Any]:
    with _jobs_lock:
        job = jobs.pop(job_id, None)
        lock = chat_session_locks.pop(job_id, None)
        if job is not None:
            _persist_state_locked()
    if job is None:
        return {"error": "not found"}

    session = chat_sessions.pop(job_id, None)
    if session is not None:
        try:
            session.close()
        except Exception:  # noqa: BLE001
            pass
    if lock is not None:
        logger.debug("Released chat lock for %s", job_id)

    result_path = job.get("result_path")
    _safe_unlink(job.get("file_path"))
    _safe_unlink(result_path)
    _safe_unlink(_match_path(job))
    if result_path:
        p = Path(result_path)
        _safe_unlink(p.with_name(f"{p.stem}_chunks.json"))
        # Per-job Qdrant directories, if a future version persists them.
        shutil.rmtree(p.with_name(f"{p.stem}_qdrant"), ignore_errors=True)

    cache.delete_prefix(f"match:{job_id}:")
    cache.delete_prefix(f"ask:{job_id}:")
    return {"deleted": job_id}


# â”€â”€ Chat / Q&A endpoints â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _chat_lock(job_id: str) -> threading.Lock:
    # dict.setdefault is atomic for a single key; no check-then-set race.
    return chat_session_locks.setdefault(job_id, threading.Lock())


@app.post("/jobs/{job_id}/ask")
def ask_document(job_id: str, payload: AskRequest) -> Dict[str, Any]:
    with _jobs_lock:
        job = dict(jobs.get(job_id) or {})
    if not job:
        return {"error": "not found"}
    if job.get("status") != "done":
        return {"error": "not ready"}

    question = (payload.question or "").strip()
    if not question:
        return {"error": "question is required"}

    file_path = job.get("file_path")
    if not file_path or not Path(file_path).exists():
        return {"error": "source document is unavailable"}

    cache_key = f"ask:{job_id}:{cache.fingerprint(question)}"
    cached = cache.get_json(cache_key)
    if cached is not None:
        return cached

    try:
        lock = _chat_lock(job_id)
        # Build under the lock so two simultaneous first questions cannot
        # both ingest the document and leak one index.
        with lock:
            session = chat_sessions.get(job_id)
            if session is None:
                import sys

                sys.path.insert(0, str(BASE_DIR))
                from tender_extraction.qa import DocumentChatSession

                session = DocumentChatSession(
                    file_path,
                    persist_dir=":memory:",
                    force_reindex=False,
                )
                session.build()
                chat_sessions[job_id] = session

        answer_data = session.ask(question)
    except Exception as exc:  # noqa: BLE001
        logger.error("ask_document error for %s: %s", job_id, exc)
        return {"error": str(exc)}

    with _jobs_lock:
        current = jobs.get(job_id)
        if current is not None:
            history = current.setdefault("chat_history", [])
            history.append(
                {
                    "id": uuid.uuid4().hex[:8],
                    "ts": time.time(),
                    "question": question,
                    "answer": answer_data,
                }
            )
            del history[:-200]  # keep the index small
            _persist_state_locked()

    cache.set_json(cache_key, answer_data, ttl=24 * 3600)
    return answer_data


@app.get("/jobs/{job_id}/history")
def get_chat_history(job_id: str) -> Dict[str, Any]:
    with _jobs_lock:
        job = jobs.get(job_id)
        history = list(job.get("chat_history", [])) if job else None
    if history is None:
        return {"error": "not found"}
    return {"history": history}


@app.delete("/jobs/{job_id}/history")
def clear_chat_history(job_id: str) -> Dict[str, Any]:
    with _jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            return {"error": "not found"}
        job["chat_history"] = []
        _persist_state_locked()
    return {"cleared": True}


# â”€â”€ Profile & score endpoints â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@app.get("/profile")
def get_profile() -> Dict[str, Any]:
    profile_path = BASE_DIR / "company_profile.json"
    if profile_path.exists():
        return json.loads(profile_path.read_text(encoding="utf-8"))
    return {}


@app.post("/profile")
def update_profile(profile: dict) -> Dict[str, Any]:
    profile_path = BASE_DIR / "company_profile.json"
    profile_path.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    return {"status": "success"}


@app.get("/jobs/{job_id}/score")
def get_job_score(job_id: str) -> Dict[str, Any]:
    with _jobs_lock:
        job = dict(jobs.get(job_id) or {})
    if not job or job.get("status") != "done":
        return {"error": "not ready"}
    blocked, reason = _match_unavailable(job)
    if blocked:
        return {"error": reason, "needs_review": True}

    profile_path = BASE_DIR / "company_profile.json"
    if not profile_path.exists():
        return {"error": "company profile not set"}

    result = json.loads(Path(job["result_path"]).read_text(encoding="utf-8"))
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    import sys

    sys.path.insert(0, str(BASE_DIR))
    from tender_extraction.scoring import score_tender_match

    return score_tender_match(profile, result)


# â”€â”€ Reconciliation endpoint â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@app.get("/jobs/{job_id}/reconciliation")
def get_job_reconciliation(job_id: str) -> Dict[str, Any]:
    with _jobs_lock:
        job = dict(jobs.get(job_id) or {})
    if not job or job.get("status") != "done":
        return {"error": "not ready"}
    try:
        result = json.loads(Path(job["result_path"]).read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"error": f"result unavailable: {exc}"}
    reconciliation = result.get("reconciliation")
    if not reconciliation:
        return {"error": "reconciliation not available"}
    return reconciliation


# â”€â”€ Match-report endpoints (requirement x manufacturer) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _match_summary(report: Dict[str, Any]) -> Dict[str, Any]:
    manufacturers = list(report.get("manufacturers") or [])
    ranked = sorted(
        manufacturers,
        key=lambda item: (item.get("rank", 999), -float(item.get("overall_compatibility") or 0)),
    )
    best = ranked[0] if ranked else None
    return {
        "manufacturer_count": len(manufacturers),
        "requirement_count": len(report.get("requirements") or []),
        "best_manufacturer": best.get("manufacturer") if best else None,
        "best_score": best.get("overall_compatibility") if best else None,
        "best_coverage": best.get("coverage") if best else None,
        "categories": report.get("categories") or [],
        "category": report.get("category") or "",
        "skipped_count": len(report.get("skipped_manufacturers") or []),
        "llm_calls": report.get("llm_calls", 0),
        "llm_cache_hits": report.get("llm_cache_hits", 0),
        "generated_at": report.get("generated_at", 0),
        "reconciliation": (report.get("reconciliation") or {}).get("summary", {}),
        "evidence_coverage": (report.get("evidence_coverage") or {}).get("coverage_ratio", 0),
    }


def _write_match_report(job_id: str, report: Dict[str, Any]) -> Path:
    with _jobs_lock:
        job = dict(jobs.get(job_id) or {})
    target = _match_path(job)
    if not report.get("generated_at"):
        report["generated_at"] = time.time()
    if not report.get("tender_file"):
        report["tender_file"] = job.get("filename", "")
    target.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return target


@app.get("/jobs/{job_id}/match")
def get_job_match(job_id: str) -> Dict[str, Any]:
    with _jobs_lock:
        job = dict(jobs.get(job_id) or {})
    if not job or job.get("status") != "done":
        return {"error": "not ready"}
    blocked, reason = _match_unavailable(job)
    if blocked:
        return {"error": reason, "engine_available": matching_adapter.is_available()}

    target = _match_path(job)
    cache_key = f"match:{job_id}:{int(cache.file_mtime(target))}"
    cached = cache.get_json(cache_key)
    if cached is not None:
        return cached

    if not target.exists():
        return {
            "error": "match report not generated",
            "engine_available": matching_adapter.is_available(),
        }

    try:
        report = json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"error": f"match report unavailable: {exc}"}

    cache.set_json(cache_key, report)
    return report


@app.post("/jobs/{job_id}/match")
def run_job_match(job_id: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    with _jobs_lock:
        job = dict(jobs.get(job_id) or {})
    if not job or job.get("status") != "done":
        return {"error": "not ready"}
    blocked, reason = _match_unavailable(job)
    if blocked:
        return {"error": reason, "engine_available": matching_adapter.is_available()}

    try:
        result = json.loads(Path(job["result_path"]).read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"error": f"result unavailable: {exc}"}

    selected_mfrs = None
    category_filter = None
    manufacturer_categories = None
    if isinstance(payload, dict):
        selected_mfrs = payload.get("selected_manufacturers")
        category_filter = payload.get("category_filter")
        manufacturer_categories = payload.get("manufacturer_categories")

    report, reason = matching_adapter.generate_match_report(
        result,
        job.get("file_path", ""),
        selected_manufacturers=selected_mfrs,
        category_filter=category_filter,
        manufacturer_categories=manufacturer_categories,
    )
    if report is None:
        return {
            "error": reason or "Matching engine unavailable",
            "engine_available": matching_adapter.is_available(),
        }

    try:
        report = MatchReport(**report).model_dump()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Matching engine returned an invalid report: {exc}"}

    target = _write_match_report(job_id, report)
    summary = _match_summary(report)
    fields: Dict[str, Any] = {
        "match_summary": summary,
        "match_options": {
            "selected_manufacturers": selected_mfrs or [],
            "category_filter": category_filter or "",
            "manufacturer_categories": manufacturer_categories or {},
        },
    }
    if summary.get("best_score") is not None:
        fields["match_score"] = summary["best_score"]
    _set_job(job_id, **fields)
    cache.set_json(
        f"match:{job_id}:{int(cache.file_mtime(target))}", report
    )
    return report
