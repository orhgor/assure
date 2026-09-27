"""Enrich extracted locks with hash, source and lock index.

Until 2026-09-27 every lock without coordinates was stamped
``{"page": 1, "x": 0, "y": 0, "width": 100, "height": 24}`` — a page nobody
looked up, presented by the inspector as the page the figure was found on. A
page is the page the quote was found on or null (``docs/evidence-honesty.md``);
this module has no source text, so it never invents one.
"""

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
    """Attach lock_hash, source_id and lock_index to each lock.

    ``page_coordinates`` is kept when the producer located the figure and set
    to ``None`` otherwise; the key stays so readers that look it up find an
    explicit "not located" rather than a KeyError.
    """
    default_source = str(sources_used[0]["id"]) if sources_used else ""
    out: list[dict[str, Any]] = []
    for i, lock in enumerate(locks):
        enriched = dict(lock)
        enriched.setdefault("source_id", default_source)
        enriched["lock_index"] = i + 1
        coords = enriched.get("page_coordinates")
        enriched["page_coordinates"] = coords if isinstance(coords, dict) and coords.get("page") else None
        enriched["lock_hash"] = lock_hash(enriched)
        out.append(enriched)
    return out
