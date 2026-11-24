#!/usr/bin/env python3
"""
dedupe_corpus.py — Audit, clean, and deduplicate Qdrant collections.

Finds:
1. Exact duplicates by (manufacturer, document, page, normalized_text_hash).
2. Degenerate / noisy chunks (empty table rows, repetitive header-only text).
3. Deletes duplicates from Qdrant and optimizes collections.
4. Ensures payload indexes for manufacturer, category, subtype, table_id, and page.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Set

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tender_extraction.retrieval import get_qdrant_client, ensure_payload_indexes

logger = logging.getLogger("dedupe_corpus")


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _text_hash(mfr: str, doc: str, page: int | str, text: str) -> str:
    norm = _normalize_text(text)
    return hashlib.sha256(f"{mfr}|{doc}|{page}|{norm}".encode("utf-8")).hexdigest()


def _is_noisy_chunk(text: str) -> bool:
    norm = _normalize_text(text)
    if not norm or len(norm) < 10:
        return True
    # Noisy empty table headers like "[Table Headers]: | | |" with no content
    if norm.startswith("[table headers]:") and re.fullmatch(r"\[table headers\]:\s*(\||\s)*", norm):
        return True
    return False


def dedupe_collection(
    client,
    collection_name: str,
    dry_run: bool = False,
) -> Dict[str, int]:
    info = client.get_collection(collection_name)
    total_before = int(info.points_count or 0)
    logger.info("Scanning %s (%d points)...", collection_name, total_before)

    seen_hashes: Set[str] = set()
    to_delete_ids: List[int | str] = []
    mfr_stats: Dict[str, Dict[str, int]] = defaultdict(lambda: {"kept": 0, "dup": 0, "noisy": 0})

    offset = None
    batch_size = 2000

    while True:
        points, offset = client.scroll(
            collection_name=collection_name,
            limit=batch_size,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for point in points:
            payload = point.payload or {}
            mfr = str(payload.get("manufacturer") or "unknown")
            doc = str(payload.get("document") or "")
            page = payload.get("page", 0)
            text = str(payload.get("text") or "")

            if _is_noisy_chunk(text):
                to_delete_ids.append(point.id)
                mfr_stats[mfr]["noisy"] += 1
                continue

            h = _text_hash(mfr, doc, page, text)
            if h in seen_hashes:
                to_delete_ids.append(point.id)
                mfr_stats[mfr]["dup"] += 1
            else:
                seen_hashes.add(h)
                mfr_stats[mfr]["kept"] += 1

        if offset is None:
            break

    logger.info(
        "%s scan complete: %d total, %d to delete (%d duplicates, %d noisy), %d unique to keep.",
        collection_name,
        total_before,
        len(to_delete_ids),
        sum(s["dup"] for s in mfr_stats.values()),
        sum(s["noisy"] for s in mfr_stats.values()),
        len(seen_hashes),
    )

    if not dry_run and to_delete_ids:
        logger.info("Deleting %d points from %s in batches...", len(to_delete_ids), collection_name)
        for i in range(0, len(to_delete_ids), 1000):
            chunk_ids = to_delete_ids[i : i + 1000]
            client.delete(
                collection_name=collection_name,
                points_selector=chunk_ids,
            )
        info_after = client.get_collection(collection_name)
        total_after = int(info_after.points_count or 0)
        logger.info("%s deduplication finished: %d -> %d points.", collection_name, total_before, total_after)
    else:
        total_after = total_before - len(to_delete_ids)

    # Ensure payload indexes on remaining points
    if not dry_run:
        ensure_payload_indexes(client, collection_name)

    return {
        "total_before": total_before,
        "deleted": len(to_delete_ids),
        "total_after": total_after,
        "duplicates": sum(s["dup"] for s in mfr_stats.values()),
        "noisy": sum(s["noisy"] for s in mfr_stats.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit and deduplicate Qdrant collections.")
    parser.add_argument("--dry-run", action="store_true", help="Report counts without deleting points.")
    parser.add_argument("--collections", nargs="*", help="Collections to process (default: all cat_*).")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    client = get_qdrant_client()
    existing = [
        c.name for c in client.get_collections().collections
        if c.name.startswith("cat_")
    ]
    target_collections = args.collections or existing
    logger.info("Target collections: %s", target_collections)

    summary = {}
    for col in target_collections:
        if col not in existing:
            logger.warning("Collection %s not found, skipping.", col)
            continue
        summary[col] = dedupe_collection(client, col, dry_run=args.dry_run)

    print("\n" + "=" * 50)
    print("DEDUPLICATION SUMMARY" + (" (DRY RUN)" if args.dry_run else ""))
    print("=" * 50)
    for col, stats in summary.items():
        print(f"{col}:")
        print(f"  Points before: {stats['total_before']}")
        print(f"  Duplicates:    {stats['duplicates']}")
        print(f"  Noisy:         {stats['noisy']}")
        print(f"  Total deleted: {stats['deleted']}")
        print(f"  Points after:  {stats['total_after']}")
    print("=" * 50)
    return 0


if __name__ == "__main__":
    sys.exit(main())
