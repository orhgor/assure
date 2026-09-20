"""Read-only audit log queries for compliance export."""

from __future__ import annotations

import json
from typing import Any

try:
    from ..db.connection import closing_connection, init_db
except ImportError:
    from db.connection import closing_connection, init_db


def fetch_audit_entries(project_id: str, *, limit: int = 500) -> list[dict[str, Any]]:
    init_db()
    # Used to open a connection and never close it — not on the failure path (there
    # was none) but on the success path either: every export abandoned one
    # connection and left its read transaction open. `closing` would have closed it
    # without rolling back and without removing it from db_open_connections.
    with closing_connection(site="db.audit_repository.fetch_audit_entries") as db:
        rows = db.execute(
            """
            SELECT id, request_id, action, target_node_id, success,
                   duration_ms, error_type, error_message, details, created_at
            FROM audit_log
            WHERE project_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (project_id, max(1, min(limit, 2000))),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        details = row[8]
        if isinstance(details, str) and details.strip():
            try:
                details = json.loads(details)
            except json.JSONDecodeError:
                pass
        out.append(
            {
                "id": row[0],
                "request_id": row[1],
                "action": row[2],
                "target_node_id": row[3],
                "success": bool(row[4]),
                "duration_ms": row[5],
                "error_type": row[6],
                "error_message": row[7],
                "details": details,
                "created_at": row[9],
            }
        )
    return out


def _collect_provenance(body: list[Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for section in body:
        if not isinstance(section, dict):
            continue
        for child in section.get("children") or []:
            if not isinstance(child, dict):
                continue
            prov = child.get("provenance")
            if not prov:
                continue
            entries = prov if isinstance(prov, list) else [prov]
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                found.append(
                    {
                        "node_id": child.get("id"),
                        "type": child.get("type"),
                        "provenance": entry,
                        "entities_referenced": child.get("entities_referenced") or [],
                    }
                )
    return found
