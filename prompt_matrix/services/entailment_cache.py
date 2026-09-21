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

**The invariant the write obeys** (it was once a fallback, and the fallback was the
bug): a cache write must reference a real ``projects`` row — or NULL — never a
sentinel string. ``pipeline_cache.project_id`` carries
``FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE`` on every
database that has run ``scripts/aws/migrate_fk_constraints.py``, and the rejection
of a sentinel is silent unless it is counted: the row is simply absent, the next
read is a miss, and a miss reads as a cold start. ``store_verdict`` therefore writes
the caller's id unchanged and counts a refusal at ``/api/health``
(``cache_drops``) instead of inventing one.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from typing import Any

try:
    from ..db.pipeline_cache import fetch_pipeline_cache, save_pipeline_cache
    from ..lib.logger import note_cache_drop
    from .omp_memory import PIPELINE_VERSION
except ImportError:  # pragma: no cover - flat import layout
    from db.pipeline_cache import fetch_pipeline_cache, save_pipeline_cache
    from lib.logger import note_cache_drop
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
    """Persist a verdict under the project that asked for it.

    ``project_id`` is written as given. It used to fall back to the literal
    ``"entailment"`` when it was falsy, and that literal is not a project: on a
    database whose ``pipeline_cache`` declares the foreign key (the migrated
    staging box) the write was rejected and the verdict was dropped, and on one
    that does not (a fresh ``init_db`` before
    scripts/aws/migrate_fk_constraints.py has run) it stored a row claiming a
    project that does not exist. Either way the caller's id was not the id
    recorded, so the fallback is gone and the id is the caller's responsibility.

    A foreign-key rejection is counted rather than raised: a write with no parent
    is a rejected write, not a bug, and ``/api/health`` reports the count. Any
    other failure propagates — see ``note_cache_drop``.
    """
    if not cache_key or not isinstance(record, dict) or not record.get("verdict"):
        return
    try:
        payload = {
            "record": record,
            "model_id": str(record.get("model") or ""),
            "checked_at": str(record.get("checked_at") or ""),
        }
        save_pipeline_cache(cache_key, project_id, CACHE_KIND, payload, ttl_days=ttl_days)
    except sqlite3.IntegrityError:
        note_cache_drop(
            site="entailment_cache.store_verdict", project_id=project_id, kind=CACHE_KIND
        )
        return


__all__ = ["CACHE_KIND", "load_verdict", "store_verdict", "verdict_cache_key"]
