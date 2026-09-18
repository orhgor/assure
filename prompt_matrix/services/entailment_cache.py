"""Cross-compile cache for entailment verdicts.

The in-compile cache in ``services/entailment.py`` reuses a verdict only while one
compile is running, so the same claim against the same source is re-sent to the
model on every compile — a judgement about *text* that has not changed. This module
holds those verdicts across compiles, keyed on everything the verdict depends on:

    sha256(claim | window | prompt | model | pipeline version)

so a re-worded claim, a different anchor window, an edited prompt, a different model
or a bumped ``PIPELINE_VERSION`` is a different key and a real call. A hit reuses the
stored record and makes no model call.

The store is the existing ``pipeline_cache`` table (kind ``entailment``), so verdicts
carry the same TTL and the same prune as the other pipeline caches — no new table.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

try:
    from ..db.pipeline_cache import fetch_pipeline_cache, save_pipeline_cache
    from .omp_memory import PIPELINE_VERSION
except ImportError:  # pragma: no cover - flat import layout
    from db.pipeline_cache import fetch_pipeline_cache, save_pipeline_cache
    from omp_memory import PIPELINE_VERSION

CACHE_KIND = "entailment"

_WHITESPACE_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    """Case- and whitespace-insensitive form, so re-wrapping is the same text."""
    return _WHITESPACE_RE.sub(" ", str(text or "")).strip().lower()


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:32]


def verdict_cache_key(
    claim: str,
    window: str,
    prompt: str,
    model_id: str,
    *,
    version: int = PIPELINE_VERSION,
) -> str:
    """``entailment:<digest>`` for one (claim, window, prompt, model) judgement."""
    return "entailment:" + _digest(
        _normalize(claim),
        _normalize(window),
        hashlib.sha256(str(prompt or "").encode("utf-8")).hexdigest(),
        str(model_id or ""),
        str(version),
    )


def load_verdict(cache_key: str) -> dict[str, Any] | None:
    """The stored entailment record, or None. Never raises."""
    if not cache_key:
        return None
    try:
        payload = fetch_pipeline_cache(cache_key)
    except Exception:
        return None
    record = (payload or {}).get("record")
    return record if isinstance(record, dict) and record.get("verdict") else None


def store_verdict(
    cache_key: str, project_id: str, record: dict[str, Any], *, ttl_days: int | None = None
) -> None:
    """Persist a verdict. Never raises — accounting must not change a verdict."""
    if not cache_key or not isinstance(record, dict) or not record.get("verdict"):
        return
    try:
        payload = {
            "record": record,
            "model_id": str(record.get("model") or ""),
            "checked_at": str(record.get("checked_at") or ""),
        }
        save_pipeline_cache(
            cache_key, project_id or "entailment", CACHE_KIND, payload, ttl_days=ttl_days
        )
    except Exception:
        return


__all__ = ["CACHE_KIND", "load_verdict", "store_verdict", "verdict_cache_key"]
