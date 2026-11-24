#!/usr/bin/env python3
"""Sync the curated tender_matching corpus into this repo's manufacturer layout.

Converts `manufacturers/<slug>/manifest.json` and `catalogue/manifest.csv`
(the other project's provenance formats) into `sources.json` + copied document
files here. No network access: every file must already exist locally.
Restricted entries (no path/sha256) and duplicate content hashes are skipped.

Usage:
    venv/Scripts/python scripts/sync_corpus.py
    venv/Scripts/python scripts/sync_corpus.py --source <tender_matching/backend/data> --dest data/manufacturers
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

import pymupdf

SUPPORTED_SUFFIXES = {".pdf", ".docx", ".jpg", ".jpeg", ".png"}

DEFAULT_SOURCE = Path(r"C:/Users/Lenovo/OneDrive/Desktop/tender_matching/backend/data")

# Curated domain tags for catalogue-CSV manufacturers whose rows carry no
# category; without them their documents would land in cat_general and be
# invisible to category-scoped retrieval.
CSV_MANUFACTURER_CATEGORY = {
    "delval-flow": "valves",
    "valmatic": "valves",
    "wilo": "pumps",
    "sulzer": "pumps",
}

ALLOWED_DOC_TYPES = {"datasheet", "catalogue", "brochure", "manual", "other"}
DOC_TYPE_ALIASES = {"specification": "datasheet", "flyer": "brochure"}


def _doc_type(raw: str) -> str:
    raw = (raw or "").strip().lower()
    raw = DOC_TYPE_ALIASES.get(raw, raw)
    return raw if raw in ALLOWED_DOC_TYPES else "other"


def _has_url(raw: str) -> bool:
    return str(raw or "").startswith(("http://", "https://"))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _slug(raw: str) -> str:
    return raw.strip().lower().replace("_", "-")


def _date(raw: str) -> str:
    return str(raw or "")[:10]


def _pages(path: Path) -> int:
    with pymupdf.open(path) as doc:
        return doc.page_count


def sync(source: Path, dest: Path) -> int:
    dest.mkdir(parents=True, exist_ok=True)
    seen_sha: Dict[str, str] = {}
    manifests: Dict[str, Dict[str, Any]] = {}
    notes: List[str] = []
    copied = 0

    def add_doc(slug: str, display: str, entry: Dict[str, Any], src: Path, sha: str) -> None:
        nonlocal copied
        if not _has_url(entry.get("url")):
            notes.append(f"{slug}/{entry['file']}: no source URL recorded — not evidence, skipped.")
            return
        if sha in seen_sha:
            notes.append(f"{slug}/{entry['file']}: duplicate of {seen_sha[sha]} — skipped.")
            return
        if not src.exists():
            notes.append(f"{slug}/{entry['file']}: source file missing — skipped.")
            return
        target_dir = dest / slug
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / entry["file"]
        # Re-runs must not overwrite an identical file: OneDrive/indexer can hold
        # a mapped section open on it, which makes copy2 fail (WinError 1224).
        if not (target.exists() and sha and _file_sha256(target) == sha):
            shutil.copy2(src, target)
        seen_sha[sha] = f"{slug}/{entry['file']}"
        copied += 1
        manifest = manifests.setdefault(slug, {
            "manufacturer": slug,
            "display_name": display,
            "documents": [],
        })
        manifest["documents"].append(entry)

    for sub in sorted((source / "manufacturers").iterdir()):
        manifest_path = sub / "manifest.json"
        if not sub.is_dir() or not manifest_path.exists():
            continue
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        slug = _slug(data.get("manufacturer_slug") or sub.name)
        display = data.get("manufacturer") or slug
        categories = Counter()
        for doc in data.get("documents", []):
            sha = doc.get("sha256")
            rel = doc.get("path")
            if not sha or not rel:
                notes.append(f"{slug}: {doc.get('model_or_family', '?')} has no file ({doc.get('status')}) — skipped.")
                continue
            src = sub / rel
            category = doc.get("category") or ""
            if category:
                categories[category] += 1
            entry = {
                "file": Path(rel).name,
                "title": doc.get("model_or_family") or Path(rel).name,
                "url": doc.get("url", ""),
                "retrieved_at": _date(doc.get("retrieved_at")),
                "pages": _pages(src) if src.exists() else 0,
                "doc_type": _doc_type(doc.get("doc_type")),
                "category": category,
                "subtype": doc.get("model_or_family") or "",
                "sha256": sha,
            }
            add_doc(slug, display, entry, src, sha)
        if categories and manifests.get(slug, {}).get("documents"):
            manifests[slug]["category"] = categories.most_common(1)[0][0]

    csv_path = source / "catalogue" / "manifest.csv"
    if csv_path.exists():
        with csv_path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                slug = _slug(row["manufacturer"])
                sha = row.get("sha256") or ""
                filename = row.get("filename") or ""
                if not sha or not filename:
                    continue
                src = source / "catalogue" / row["manufacturer"] / filename
                if not src.exists():
                    src = source / "catalogue" / row["manufacturer"] / "raw" / filename
                category = (row.get("category") or "").strip()
                entry = {
                    "file": filename,
                    "title": (row.get("notes") or filename).strip(),
                    "url": row.get("source_url", ""),
                    "retrieved_at": _date(row.get("retrieved_at")),
                    "pages": int(row.get("pages") or 0) or (_pages(src) if src.exists() else 0),
                    "doc_type": _doc_type(row.get("doc_type")),
                    "category": category,
                    "subtype": "",
                    "sha256": sha,
                }
                add_doc(slug, slug, entry, src, sha)
                if not category and slug in CSV_MANUFACTURER_CATEGORY and manifests.get(slug, {}).get("documents"):
                    manifests[slug]["category"] = CSV_MANUFACTURER_CATEGORY[slug]

    for slug, manifest in sorted(manifests.items()):
        if not manifest["documents"]:
            continue
        (dest / slug / "sources.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    # Dest slug dirs are created only by this script, so any document file not
    # listed in the fresh manifest is an orphan from an earlier run.
    listed = {
        (slug, entry["file"])
        for slug, manifest in manifests.items()
        for entry in manifest["documents"]
    }
    for sub in sorted(dest.iterdir()):
        if not sub.is_dir() or sub.name.startswith(("_", ".")):
            continue
        for file_path in sub.iterdir():
            if file_path.suffix.lower() in SUPPORTED_SUFFIXES and (sub.name, file_path.name) not in listed:
                try:
                    file_path.unlink()
                except OSError as exc:
                    notes.append(f"{sub.name}/{file_path.name}: orphan could not be removed ({exc}).")
                else:
                    notes.append(f"{sub.name}/{file_path.name}: orphan from earlier sync — removed.")
        if not manifests.get(sub.name, {}).get("documents"):
            stale_manifest = sub / "sources.json"
            if stale_manifest.exists():
                stale_manifest.unlink()
            try:
                sub.rmdir()
            except OSError:
                pass
            else:
                notes.append(f"{sub.name}: no provenanced documents — folder removed.")

    for note in notes:
        print(f"NOTE: {note}")
    kept = sum(1 for m in manifests.values() if m["documents"])
    print(f"Copied {copied} documents into {kept} manufacturer folders under {dest}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync the local tender_matching corpus.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--dest", type=Path, default=Path("data/manufacturers"))
    args = parser.parse_args()
    if not args.source.is_dir():
        print(f"ERROR: source corpus not found: {args.source}", file=sys.stderr)
        return 1
    return sync(args.source, args.dest)


if __name__ == "__main__":
    sys.exit(main())