"""
cache.py — Optional Redis cache with a silent in-process fallback.

The app must run without Redis (local dev, unit tests).  When `REDIS_URL` is
set and the `redis` package is installed, values are shared across processes
and survive restarts; otherwise every operation is a no-op.

Cache keys used by the API:
    match:{job_id}:{result_mtime}   — generated MatchReport JSON
    ask:{job_id}:{sha1(question)}   — grounded QA answers
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", str(7 * 24 * 3600)))
_client = None
_client_checked = False
_client_lock = threading.Lock()


def _get_client():
    """Lazily build a Redis client; return None when unavailable."""
    global _client, _client_checked
    if _client_checked:
        return _client
    with _client_lock:
        if _client_checked:
            return _client
        _client_checked = True
        url = os.getenv("REDIS_URL", "").strip()
        if not url:
            logger.info("REDIS_URL not set — cache disabled.")
            return None
        try:
            import redis  # type: ignore

            _client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=2)
            _client.ping()
            logger.info("Redis cache connected: %s", url)
        except Exception as exc:  # noqa: BLE001 - cache must never break the app
            logger.warning("Redis cache unavailable (%s) — continuing without it.", exc)
            _client = None
    return _client


def enabled() -> bool:
    return _get_client() is not None


def get_json(key: str) -> Optional[Any]:
    client = _get_client()
    if client is None:
        return None
    try:
        raw = client.get(key)
        return json.loads(raw) if raw else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Cache read failed for %s: %s", key, exc)
        return None


def set_json(key: str, value: Any, ttl: int = _TTL_SECONDS) -> None:
    client = _get_client()
    if client is None:
        return
    try:
        client.set(key, json.dumps(value, default=str), ex=ttl)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Cache write failed for %s: %s", key, exc)


def delete(*keys: str) -> None:
    client = _get_client()
    if client is None or not keys:
        return
    try:
        client.delete(*keys)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Cache delete failed: %s", exc)


def delete_prefix(prefix: str) -> int:
    """Delete keys matching `prefix*`. Uses SCAN, safe for small caches."""
    client = _get_client()
    if client is None:
        return 0
    removed = 0
    try:
        for key in client.scan_iter(match=f"{prefix}*", count=200):
            client.delete(key)
            removed += 1
    except Exception as exc:  # noqa: BLE001
        logger.warning("Cache prefix delete failed for %s: %s", prefix, exc)
    return removed


def fingerprint(*parts: Any, length: int = 16) -> str:
    """Stable short hash for building cache keys."""
    blob = "|".join(str(p) for p in parts).encode("utf-8")
    return hashlib.sha1(blob).hexdigest()[:length]


def file_mtime(path: Any) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return time.time()
