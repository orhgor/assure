"""Enrich extracted locks with hash, source, and page coordinates."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def lock_hash(lock: dict[str, Any]) -> str:
    payload = {
        "key": lock.get("canonical_key") or lock.get("metric"),
        "value": lock.get("value"),
        "source_id": lock.get("source_id"),
    }
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def enrich_extracted_locks(
    locks: list[dict[str, Any]],
    sources_used: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach lock_hash, source_id, page_coordinates, lock_index to each lock."""
    default_source = str(sources_used[0]["id"]) if sources_used else ""
    out: list[dict[str, Any]] = []
    for i, lock in enumerate(locks):
        enriched = dict(lock)
        enriched.setdefault("source_id", default_source)
        enriched["lock_index"] = i + 1
        enriched.setdefault(
            "page_coordinates",
            {"page": 1, "x": 0, "y": 0, "width": 100, "height": 24},
        )
        enriched["lock_hash"] = lock_hash(enriched)
        out.append(enriched)
    return out
