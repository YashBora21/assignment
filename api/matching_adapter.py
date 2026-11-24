"""
matching_adapter.py — Thin seam between the HTTP API and the matching engine.

Half A owns `tender_extraction/matching.py` and produces a `MatchReport`
(see `tender_extraction/schemas_match.py`).  Half B must not depend on the
module existing while it is still being built, so every call goes through
this adapter.

Expected entry point (any one of these names):

    generate_match_report(tender_result: dict, *, manufacturers_dir: str,
                          tender_file: str) -> dict | MatchReport

`tender_result` is the normalised extraction JSON returned by
`TenderExtractionPipeline.run()`.  The function must return a dict matching
`MatchReport` (or a `MatchReport` instance).
"""

from __future__ import annotations

import importlib
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_ENTRY_POINTS = ("generate_match_report", "build_match_report", "run_matching")


def manufacturers_dir() -> Path:
    """Directory containing one folder per manufacturer."""
    return Path(os.getenv("MANUFACTURERS_DIR", "data/manufacturers"))


def _engine_module():
    try:
        return importlib.import_module("tender_extraction.matching")
    except Exception:  # ImportError today, real import bugs later
        return None


def is_available() -> bool:
    """True when Half A's matching module can be imported."""
    return _engine_module() is not None


def _resolve_entry_point(module) -> Optional[Any]:
    for name in _ENTRY_POINTS:
        fn = getattr(module, name, None)
        if callable(fn):
            return fn
    return None


def generate_match_report(
    tender_result: Dict[str, Any],
    tender_file: str = "",
    selected_manufacturers: Optional[List[str]] = None,
    category_filter: Optional[str] = None,
    manufacturer_categories: Optional[Dict[str, str]] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Run the matching engine.

    Returns `(report, None)` on success or `(None, reason)` when the engine is
    unavailable / failed.  The API turns `reason` into a user-facing message
    instead of a 500.
    """
    canonical_specs = [
        r for r in (tender_result.get("canonical_requirements") or [])
        if r.get("kind") != "commercial" and r.get("parameter") != "quantity"
    ]
    has_specs = bool(tender_result.get("technical_specifications") or canonical_specs)
    if not has_specs:
        notes = tender_result.get("notes") or []
        reason = "; ".join(str(n) for n in notes[:2]) if notes else "attachments not found or OCR failed"
        return None, f"No requirements could be extracted from the uploaded documents: {reason}"
    module = _engine_module()
    if module is None:
        return None, (
            "Matching engine is not installed yet. Expected "
            "tender_extraction/matching.py to expose generate_match_report()."
        )

    fn = _resolve_entry_point(module)
    if fn is None:
        return None, (
            "tender_extraction/matching.py is present but exposes none of: "
            + ", ".join(_ENTRY_POINTS)
        )

    kwargs = {
        "manufacturers_dir": str(manufacturers_dir()),
        "tender_file": tender_file,
        "selected_manufacturers": selected_manufacturers,
        "category_filter": category_filter,
        "manufacturer_categories": manufacturer_categories,
    }
    try:
        report = fn(tender_result, **kwargs)
    except TypeError:
        # Older/simpler signature while Half A iterates.  A TypeError raised
        # *inside* the engine will surface on this retry and be reported.
        try:
            report = fn(tender_result)
        except Exception as exc:  # noqa: BLE001 - reported to the caller
            logger.exception("Matching engine failed")
            return None, f"Matching engine failed: {exc}"
    except Exception as exc:  # noqa: BLE001 - reported to the caller
        logger.exception("Matching engine failed")
        return None, f"Matching engine failed: {exc}"

    if hasattr(report, "model_dump"):
        report = report.model_dump()
    if not isinstance(report, dict):
        return None, (
            "Matching engine returned an unsupported value "
            f"({type(report).__name__}); expected a MatchReport dict."
        )
    return report, None
