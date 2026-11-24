#!/usr/bin/env python3
"""Validate the manufacturer corpus provenance manifests.

Usage:
    python scripts/validate_manufacturer_corpus.py
    python scripts/validate_manufacturer_corpus.py --root data/manufacturers

Exits 1 when any manifest is missing, malformed, references a missing file,
or lacks a source URL / retrieval date.  Stdlib only so it runs before the
Python dependencies are installed.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ALLOWED_DOC_TYPES = {"datasheet", "catalogue", "brochure", "manual", "other"}
REQUIRED_DOC_FIELDS = ("file", "url", "retrieved_at", "pages")


def _load_sources(path: Path) -> tuple[dict | None, str | None]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return None, f"cannot read {path.name}: {exc}"
    if not isinstance(data, dict):
        return None, f"{path.name} must contain a JSON object"
    return data, None


def validate_manufacturer(folder: Path) -> tuple[list[str], list[str], int]:
    errors: list[str] = []
    warnings: list[str] = []
    sources_path = folder / "sources.json"
    if not sources_path.exists():
        return [f"{folder.name}: missing sources.json"], warnings, 0

    data, error = _load_sources(sources_path)
    if error:
        return [f"{folder.name}: {error}"], warnings, 0

    declared = data.get("manufacturer")
    if declared != folder.name:
        warnings.append(
            f"{folder.name}: sources.json manufacturer is {declared!r} "
            f"(expected the directory slug {folder.name!r})"
        )

    documents = data.get("documents")
    if not isinstance(documents, list) or not documents:
        return [f"{folder.name}: 'documents' must be a non-empty list"], warnings, 0

    seen_files: set[str] = set()
    for index, doc in enumerate(documents):
        label = f"{folder.name}/documents[{index}]"
        if not isinstance(doc, dict):
            errors.append(f"{label}: must be an object")
            continue
        for field in REQUIRED_DOC_FIELDS:
            if field not in doc or doc[field] in (None, ""):
                errors.append(f"{label}: missing required field '{field}'")
        file_name = doc.get("file")
        if isinstance(file_name, str) and file_name:
            if file_name in seen_files:
                errors.append(f"{label}: duplicate file reference '{file_name}'")
            seen_files.add(file_name)
            if not (folder / file_name).exists():
                errors.append(f"{label}: file not found: {folder / file_name}")

        url = doc.get("url")
        if isinstance(url, str) and not url.startswith(("http://", "https://")):
            errors.append(f"{label}: url must start with http:// or https://")

        retrieved = doc.get("retrieved_at")
        if isinstance(retrieved, str):
            try:
                date.fromisoformat(retrieved)
            except ValueError:
                errors.append(f"{label}: retrieved_at must be YYYY-MM-DD")

        pages = doc.get("pages")
        if not isinstance(pages, int) or isinstance(pages, bool) or pages <= 0:
            errors.append(f"{label}: pages must be a positive integer")

        doc_type = doc.get("doc_type")
        if doc_type not in ALLOWED_DOC_TYPES:
            errors.append(
                f"{label}: doc_type must be one of {sorted(ALLOWED_DOC_TYPES)}"
            )

    return errors, warnings, len(documents)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        default="data/manufacturers",
        help="Manufacturer corpus root (default: data/manufacturers)",
    )
    args = parser.parse_args()

    root = Path(args.root)
    if not root.exists():
        print(f"[ERROR] corpus root not found: {root}")
        return 1

    folders = sorted(
        path
        for path in root.iterdir()
        if path.is_dir() and not path.name.startswith(("_", "."))
    )
    if not folders:
        print(
            f"[WARN] no manufacturer folders in {root}. "
            "Copy _template/sources.json to add one."
        )
        return 0

    total_errors: list[str] = []
    total_warnings: list[str] = []
    total_docs = 0
    print(f"Validating {len(folders)} manufacturer(s) under {root}\n")
    for folder in folders:
        errors, warnings, doc_count = validate_manufacturer(folder)
        total_errors.extend(errors)
        total_warnings.extend(warnings)
        total_docs += doc_count
        status = "OK" if not errors else "FAIL"
        print(f"  [{status}] {folder.name}: {doc_count} document(s)")

    if total_warnings:
        print("\nWarnings:")
        for warning in total_warnings:
            print(f"  - {warning}")

    if total_errors:
        print("\nErrors:")
        for error in total_errors:
            print(f"  - {error}")
        print(f"\n{len(total_errors)} error(s) across {len(folders)} manufacturer(s).")
        return 1

    print(f"\nOK — {total_docs} document(s) validated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
