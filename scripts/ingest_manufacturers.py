#!/usr/bin/env python3
"""Index the manufacturer corpus into category-scoped Qdrant collections.

Storage stays manufacturer-first (provenance in sources.json); retrieval is
category-first: one collection per category (cat_<category>) carrying
manufacturer / subtype / doc_type payloads. A document's category comes from
its manifest entry, falls back to the manufacturer-level category, then to
`general`. Identical content hashes are indexed once (distributor copies).

Usage:
    venv/Scripts/python scripts/ingest_manufacturers.py
    venv/Scripts/python scripts/ingest_manufacturers.py --root data/manufacturers --persist-dir qdrant_storage
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import html
import random
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tender_extraction.chunking import create_chunks
from tender_extraction.ingestion import ingest_document
from tender_extraction.retrieval import HybridRetriever
from tender_extraction.schemas import Chunk
from tender_extraction.table_extraction import extract_tables, extract_docx_tables, extract_image_tables
from tender_extraction.manufacturer_tables import normalize_product_table

logger = logging.getLogger("ingest_manufacturers")

SUPPORTED_SUFFIXES = {".pdf", ".docx", ".jpg", ".jpeg", ".png"}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect(root: Path, review_dir: Path | None = None, category_filter: str | None = None) -> Tuple[Dict[str, List[Chunk]], Dict[str, Dict[str, Any]], List[str]]:
    """Walk the corpus; return (chunks_by_category, payload_by_chunk_id, notes)."""
    chunks_by_cat: Dict[str, List[Chunk]] = {}
    payloads: Dict[str, Dict[str, Any]] = {}
    notes: List[str] = []
    seen_hashes: Dict[str, str] = {}
    samples, totals = {}, {}
    rng = random.Random(42)

    for sub in sorted(root.iterdir()):
        if not sub.is_dir() or sub.name.startswith(("_", ".")):
            continue
        manifest: Dict[str, Any] = {}
        manifest_path = sub / "sources.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                notes.append(f"{sub.name}: sources.json unreadable ({exc}).")
        else:
            notes.append(f"{sub.name}: no sources.json — evidence provenance unverified.")

        docs = {d.get("file"): d for d in manifest.get("documents", []) if isinstance(d, dict)}
        for file_path in sorted(sub.iterdir()):
            if file_path.suffix.lower() not in SUPPORTED_SUFFIXES:
                continue
            doc = docs.get(file_path.name, {})
            digest = doc.get("sha256") or _sha256(file_path)
            if digest in seen_hashes:
                notes.append(
                    f"{sub.name}/{file_path.name}: duplicate of {seen_hashes[digest]} — indexed once."
                )
                continue
            seen_hashes[digest] = f"{sub.name}/{file_path.name}"

            category = str(doc.get("category") or manifest.get("category") or "general").strip().lower()
            if category_filter is not None and category != category_filter:
                continue
            if review_dir is not None:
                samples.setdefault(category, [])
                totals.setdefault(category, 0)
            try:
                pages = ingest_document(str(file_path))
            except Exception as exc:  # noqa: BLE001 - keep going across documents
                notes.append(f"{sub.name}/{file_path.name}: ingestion failed ({exc}).")
                continue

            try:
                if file_path.suffix.lower() == ".pdf":
                    raw_tables = extract_tables(str(file_path), pages=pages, preserve_geometry=True)
                elif file_path.suffix.lower() == ".docx":
                    raw_tables = extract_docx_tables(str(file_path))
                else:
                    raw_tables = extract_image_tables(str(file_path))
            except Exception as exc:
                notes.append(f"{sub.name}/{file_path.name}: table extraction failed ({exc}).")
                raw_tables = []
            tables = [normalize_product_table(table) for table in raw_tables]
            row_payloads = {}
            for table in tables:
                for row_index, product in enumerate(table["products"], start=1):
                    row_payloads[(table["table_id"], row_index)] = product
                if review_dir is not None:
                    totals[category] = totals.get(category, 0) + 1
                    bucket = samples.setdefault(category, [])
                    record = {"manufacturer": sub.name, "document": str(file_path.resolve()),
                              "category": category, "sha256": digest, "source_url": doc.get("url", ""), **table}
                    position = rng.randrange(totals[category])
                    if len(bucket) < 25:
                        bucket.append(record)
                    elif position < 25:
                        bucket[position] = record
            for chunk in create_chunks(pages, tables=tables, use_semantic=False):
                chunk_id = f"{sub.name}:{file_path.name}:{chunk.chunk_id}"
                chunks_by_cat.setdefault(category, []).append(
                    Chunk(chunk_id=chunk_id, text=chunk.text, metadata=chunk.metadata)
                )
                payloads[chunk_id] = {
                    "manufacturer": sub.name,
                    "subtype": doc.get("subtype") or doc.get("model_or_family") or "",
                    "doc_type": doc.get("doc_type") or "",
                    "document": file_path.name,
                    "category": category,
                    "sha256": digest,
                    "page": chunk.metadata.page,
                    "table_id": chunk.metadata.table_id,
                    "model": "",
                    "specs": {},
                }
                if chunk.metadata.chunk_type == "table":
                    row_index = int(chunk.chunk_id.split("_row_")[1].split("_")[0])
                    payloads[chunk_id].update(row_payloads[(chunk.metadata.table_id, row_index)])

    if review_dir is not None:
        export_reviews(review_dir, samples, totals)
    return chunks_by_cat, payloads, notes


def export_reviews(directory, samples, totals):
    directory.mkdir(parents=True, exist_ok=True)
    def grid(rows):
        return '<table border="1">' + ''.join('<tr>' + ''.join('<td>' + html.escape(str(c or "")) + '</td>' for c in row) + '</tr>' for row in rows) + '</table>'
    for category, records in samples.items():
        if not re.fullmatch(r"[a-z0-9_-]+", category):
            raise ValueError(f"Invalid category slug for review export: {category!r}")
        summary = {"category": category, "seed": 42, "total_extracted_tables": totals[category], "sample_count": len(records), "tables": records}
        (directory / f"{category}.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        sections = [f'<meta charset="utf-8"><title>{html.escape(category)} table review</title>',
                    f'<h1>{html.escape(category)}: {len(records)} random tables of {totals[category]}</h1>']
        for record in records:
            link = Path(record["document"]).as_uri() + f'#page={record["page"]}'
            sections += [f'<h2>{html.escape(record["manufacturer"])} — {html.escape(Path(record["document"]).name)} — PDF page {record["page"]} — {html.escape(record["table_id"])}</h2>',
                         f'<a href="{html.escape(link, quote=True)}">Open PDF page</a>', '<h3>Raw extraction</h3>',
                         grid(record.get("raw") or [record.get("headers", [])] + record.get("rows", [])),
                         '<h3>Accepted normalized rows</h3>', grid([record["headers"]] + record["rows"]),
                         '<h3>Rejected rows</h3><pre>' + html.escape(json.dumps(record["rejected"], indent=2, ensure_ascii=False)) + '</pre>']
        (directory / f"{category}.html").write_text('\n'.join(sections), encoding="utf-8")
        logger.info("Review sample %s: %d of %d tables", category, len(records), totals[category])


def index_corpus(root: str | Path, persist_dir: str = "qdrant_storage", review_dir: Path | None = None, category_filter: str | None = None) -> Dict[str, int]:
    chunks_by_cat, payloads, notes = collect(Path(root), review_dir=review_dir, category_filter=category_filter)
    for note in notes:
        logger.warning("%s", note)

    counts: Dict[str, int] = {}
    retriever = HybridRetriever(persist_dir=persist_dir)
    try:
        for category, chunks in sorted(chunks_by_cat.items()):
            retriever.build_index(
                chunks,
                collection_name=f"cat_{category}",
                payload_by_chunk=payloads,
            )
            counts[category] = len(chunks)
            logger.info("cat_%s: indexed %d chunks", category, len(chunks))
    finally:
        retriever.close()
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Index manufacturer catalogues by category.")
    parser.add_argument("--root", default="data/manufacturers", help="Manufacturer corpus directory")
    parser.add_argument("--persist-dir", default="qdrant_storage", help="Qdrant local storage directory")
    parser.add_argument("--review-dir", type=Path, default=Path("outputs/table_review"))
    parser.add_argument("--category", help="Process only this manifest category (e.g. valves)")
    parser.add_argument("--review-only", action="store_true", help="Extract and export tables without downloading embeddings or building indexes")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    if args.review_only:
        _, _, notes = collect(Path(args.root), review_dir=args.review_dir, category_filter=args.category)
        for note in notes:
            logger.warning("%s", note)
        return 0
    counts = index_corpus(args.root, args.persist_dir, args.review_dir, category_filter=args.category)
    if not counts:
        logger.error("No documents indexed under %s", args.root)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
