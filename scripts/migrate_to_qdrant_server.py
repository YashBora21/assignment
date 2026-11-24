#!/usr/bin/env python3
"""Migrate collections from local Qdrant (SQLite) to running Qdrant Server (Docker / HTTP).

Transfers existing computed vectors and payloads without re-embedding.
Ensures native payload indexes (manufacturer, category, subtype, table_id, page)
are built on the Qdrant server for sub-millisecond pre-filtered search.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams
from tender_extraction.retrieval import ensure_payload_indexes

logger = logging.getLogger("migrate_qdrant")


def migrate_collections(
    local_path: str = "./qdrant_storage",
    server_url: str = "http://localhost:6333",
    batch_size: int = 500,
) -> None:
    logger.info("Opening local Qdrant at %s...", local_path)
    local_client = QdrantClient(path=local_path)

    logger.info("Connecting to Qdrant Server at %s...", server_url)
    server_client = QdrantClient(url=server_url, timeout=120, check_compatibility=False)

    collections = [
        c.name
        for c in local_client.get_collections().collections
        if c.name.startswith("cat_")
    ]
    logger.info("Found collections to migrate: %s", collections)

    existing_server = {
        c.name for c in server_client.get_collections().collections
    }

    for col in collections:
        local_info = local_client.get_collection(col)
        total_local = int(local_info.points_count or 0)
        logger.info("--- Migrating %s (%d points) ---", col, total_local)

        if col in existing_server:
            server_info = server_client.get_collection(col)
            if int(server_info.points_count or 0) == total_local:
                logger.info("Collection %s already migrated with %d points. Skipping.", col, total_local)
                ensure_payload_indexes(server_client, col)
                continue
            logger.info("Recreating collection %s on server...", col)
            server_client.delete_collection(col)

        server_client.create_collection(
            collection_name=col,
            vectors_config=VectorParams(size=1024, distance=Distance.COSINE),
        )

        offset = None
        migrated = 0
        t0 = time.time()
        while True:
            points, offset = local_client.scroll(
                collection_name=col,
                offset=offset,
                limit=batch_size,
                with_vectors=True,
                with_payload=True,
            )
            if not points:
                break

            point_structs = [
                PointStruct(id=r.id, vector=r.vector, payload=r.payload)
                for r in points
            ]
            server_client.upsert(
                collection_name=col,
                points=point_structs,
                wait=False,
            )
            migrated += len(points)
            pct = (migrated / total_local) * 100 if total_local else 100
            sys.stdout.write(f"\r  Progress: {migrated}/{total_local} ({pct:.1f}%)")
            sys.stdout.flush()

            if offset is None:
                break

        dt = time.time() - t0
        print()
        logger.info("Collection %s upserted %d points in %.1fs (%.0f pts/sec).", col, migrated, dt, migrated / max(dt, 0.1))

        # Build payload indexes on server
        logger.info("Ensuring payload indexes on server for %s...", col)
        ensure_payload_indexes(server_client, col)

    server_cols = server_client.get_collections().collections
    logger.info("Migration complete! Server collections:")
    for c in server_cols:
        info = server_client.get_collection(c.name)
        logger.info("  - %s: %d points", c.name, info.points_count)


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate local Qdrant to Qdrant Server.")
    parser.add_argument("--local-path", default="./qdrant_storage", help="Local Qdrant directory")
    parser.add_argument("--server-url", default="http://localhost:6333", help="Qdrant server URL")
    parser.add_argument("--batch-size", type=int, default=500, help="Batch size for scrolling/upserting")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    migrate_collections(args.local_path, args.server_url, args.batch_size)
    return 0


if __name__ == "__main__":
    sys.exit(main())
