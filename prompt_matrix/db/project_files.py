"""Per-project source.md and last-compiled JDF AST (manifest.json)."""

from __future__ import annotations

import json
from typing import Any

try:
    from ..db.connection import init_db
    from ..db.jdf_repository import ensure_project
    from ..history import get_db
except ImportError:
    from db.connection import init_db
    from db.jdf_repository import ensure_project
    from history import get_db

MANIFEST_VERSION = "assure-files-1"


def empty_manifest() -> dict[str, Any]:
    return {
        "manifestVersion": MANIFEST_VERSION,
        "lastCompiledOutput": [],
        "truth_ledger": {},
        "document_id": "",
        "meta": {},
    }


def _parse_compiled(raw: str | None) -> dict[str, Any]:
    manifest = empty_manifest()
    if not raw:
        return manifest
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return manifest
    if isinstance(parsed, list):
        manifest["lastCompiledOutput"] = parsed
        return manifest
    if isinstance(parsed, dict):
        nodes = parsed.get("lastCompiledOutput")
        if nodes is None and isinstance(parsed.get("body"), list):
            nodes = parsed["body"]
        if isinstance(nodes, list):
            manifest["lastCompiledOutput"] = nodes
        if isinstance(parsed.get("truth_ledger"), dict):
            manifest["truth_ledger"] = parsed["truth_ledger"]
        if parsed.get("document_id"):
            manifest["document_id"] = str(parsed["document_id"])
        if isinstance(parsed.get("meta"), dict):
            manifest["meta"] = parsed["meta"]
        if parsed.get("manifestVersion"):
            manifest["manifestVersion"] = str(parsed["manifestVersion"])
    return manifest


def fetch_project_files(project_id: str) -> dict[str, Any]:
    init_db()
    ensure_project(project_id)
    db = get_db()
    row = db.execute(
        "SELECT source_md, last_compiled_json FROM projects WHERE id = ?",
        (project_id,),
    ).fetchone()
    source_md = (row[0] if row else "") or ""
    manifest = _parse_compiled(row[1] if row else None)
    if not manifest.get("document_id"):
        manifest["document_id"] = f"doc-{project_id}"
    return {
        "ok": True,
        "project_id": project_id,
        "path": f"/projects/{project_id}/",
        "source_md": source_md,
        "manifest": manifest,
    }


def save_project_source(project_id: str, source_md: str) -> dict[str, Any]:
    init_db()
    ensure_project(project_id)
    db = get_db()
    db.execute(
        """
        UPDATE projects
        SET source_md = ?, updated_at = datetime('now')
        WHERE id = ?
        """,
        (source_md or "", project_id),
    )
    db.commit()
    return fetch_project_files(project_id)


def save_last_compiled(
    project_id: str,
    last_compiled: list[Any] | dict[str, Any] | None,
) -> dict[str, Any]:
    init_db()
    ensure_project(project_id)
    current = fetch_project_files(project_id)
    manifest = current["manifest"]
    if isinstance(last_compiled, list):
        manifest["lastCompiledOutput"] = last_compiled
    elif isinstance(last_compiled, dict):
        body = last_compiled.get("lastCompiledOutput")
        if body is None:
            body = last_compiled.get("body")
        if isinstance(body, list):
            manifest["lastCompiledOutput"] = body
        if isinstance(last_compiled.get("truth_ledger"), dict):
            manifest["truth_ledger"] = last_compiled["truth_ledger"]
        if last_compiled.get("document_id"):
            manifest["document_id"] = str(last_compiled["document_id"])
        if isinstance(last_compiled.get("meta"), dict):
            manifest["meta"] = last_compiled["meta"]
    db = get_db()
    db.execute(
        """
        UPDATE projects
        SET last_compiled_json = ?, updated_at = datetime('now')
        WHERE id = ?
        """,
        (json.dumps(manifest), project_id),
    )
    db.commit()
    return fetch_project_files(project_id)
