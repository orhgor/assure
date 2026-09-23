"""Mirror the canonical JDF tree to object storage on every revision save.

Historically a ``document.jdf`` under ``<package>/projects/<id>/`` — a copy that
lived on one instance's disk and diverged per replica. It now goes to the
object store (``services/object_store``: S3 when ``ASSURE_S3_BUCKET`` is set,
else ``<data dir>/objects/``) under ``jdf/<project>/document.jdf``, so every
replica and worker reads the same bytes. ``ASSURE_JDF_DIR`` is still honoured
as a plain-directory override for the desktop edition.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

try:
    from ..services.object_store import LocalObjectStore, get_object_store
except ImportError:
    from services.object_store import LocalObjectStore, get_object_store


def _safe_project(project_id: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in (project_id or "default"))
    return safe.strip("-") or "default"


def _jdf_dir_override() -> Path | None:
    override = (os.environ.get("ASSURE_JDF_DIR") or "").strip()
    return Path(override) if override else None


def jdf_object_key(project_id: str) -> str:
    # The plain-directory override keeps the legacy layout <dir>/<project>/document.jdf.
    if _jdf_dir_override() is not None:
        return f"{_safe_project(project_id)}/document.jdf"
    return f"jdf/{_safe_project(project_id)}/document.jdf"


def _store():
    override = _jdf_dir_override()
    if override is not None:
        return LocalObjectStore(override)
    return get_object_store()


def jdf_disk_path(project_id: str) -> Path:
    """Where the local backend keeps the mirror (informational; S3 has no path)."""
    store = _store()
    if isinstance(store, LocalObjectStore):
        return store.root / jdf_object_key(project_id)
    return Path(store.uri(jdf_object_key(project_id)))


def write_jdf_disk(project_id: str, tree: dict[str, Any]) -> str:
    """Mirror the tree. Returns a filesystem path for the local backend and the
    ``s3://`` URI for S3 — what ``save_jdf_revision`` reports as ``disk_path``."""
    data = (json.dumps(tree, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    store = _store()
    uri = store.put_bytes(jdf_object_key(project_id), data, content_type="application/json")
    if isinstance(store, LocalObjectStore):
        return str(store.root / jdf_object_key(project_id))
    return uri


def read_jdf_disk(project_id: str) -> dict[str, Any] | None:
    store = _store()
    key = jdf_object_key(project_id)
    try:
        if not store.exists(key):
            return None
        parsed = json.loads(store.get_bytes(key).decode("utf-8"))
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None
