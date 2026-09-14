"""Substrate Vault persistence for Textract extractions."""

from __future__ import annotations

import json
import uuid
from typing import Any

try:
    from ..db.connection import init_db
    from ..history import get_db
    from ..services.vault_tfidf_cache import invalidate_workspace_cache
except ImportError:
    from db.connection import init_db
    from history import get_db
    from services.vault_tfidf_cache import invalidate_workspace_cache


def save_substrate_text(
    project_id: str,
    raw_text: str,
    *,
    page_count: int = 1,
    source: str = "edge",
    entry_id: str | None = None,
) -> dict[str, Any]:
    """Persist edge-ingested substrate text."""
    init_db()
    db = get_db()
    row_id = entry_id or f"edge-{uuid.uuid4().hex[:16]}"
    db.execute(
        """
        INSERT INTO substrates (id, project_id, raw_text, page_count, source)
        VALUES (?, ?, ?, ?, ?)
        """,
        (row_id, project_id, raw_text or "", int(page_count), source),
    )
    db.commit()
    invalidate_workspace_cache(project_id)
    return {
        "id": row_id,
        "project_id": project_id,
        "raw_text": raw_text or "",
        "page_count": int(page_count),
        "source": source,
    }


def save_substrate_entry(
    project_id: str,
    *,
    filename: str,
    page_count: int,
    extracted_text: str,
    tables: list[dict[str, Any]] | None = None,
    forms: list[dict[str, Any]] | None = None,
    entry_id: str | None = None,
    file_size_bytes: int = 0,
) -> dict[str, Any]:
    """Persist a Textract extraction in substrate_vault."""
    init_db()
    db = get_db()
    vault_id = entry_id or f"sub-{uuid.uuid4().hex[:16]}"
    db.execute(
        """
        INSERT INTO substrate_vault (
            id, project_id, filename, page_count,
            extracted_text, tables_json, forms_json, file_size_bytes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            vault_id,
            project_id,
            filename,
            int(page_count),
            extracted_text or "",
            json.dumps(tables or []),
            json.dumps(forms or []),
            int(file_size_bytes or 0),
        ),
    )
    db.commit()
    invalidate_workspace_cache(project_id)
    return {
        "id": vault_id,
        "project_id": project_id,
        "filename": filename,
        "page_count": int(page_count),
        "extracted_text": extracted_text or "",
        "tables": tables or [],
        "forms": forms or [],
        "file_size_bytes": int(file_size_bytes or 0),
    }


def list_substrate_for_project(project_id: str) -> list[dict[str, Any]]:
    """List vault entries for a project (no extracted_text — keep the list light)."""
    init_db()
    db = get_db()
    rows = db.execute(
        """
        SELECT id, filename, page_count, file_size_bytes, included, created_at
        FROM substrate_vault
        WHERE project_id = ?
        ORDER BY created_at DESC
        """,
        (project_id,),
    ).fetchall()
    return [
        {
            "id": row[0],
            "filename": row[1],
            "page_count": int(row[2] or 1),
            "file_size_bytes": int(row[3] or 0),
            "included": bool(row[4]),
            "created_at": row[5],
        }
        for row in rows
    ]


def delete_substrate_entry(project_id: str, file_id: str) -> bool:
    """Delete a vault entry. Returns True if a row was removed."""
    init_db()
    db = get_db()
    cur = db.execute(
        "DELETE FROM substrate_vault WHERE project_id = ? AND id = ?",
        (project_id, file_id),
    )
    db.commit()
    if cur.rowcount > 0:
        invalidate_workspace_cache(project_id)
    return cur.rowcount > 0


def set_substrate_included(project_id: str, file_id: str, included: bool) -> bool:
    """Toggle whether a file feeds compile grounding. Returns True if a row matched."""
    init_db()
    db = get_db()
    cur = db.execute(
        "UPDATE substrate_vault SET included = ? WHERE project_id = ? AND id = ?",
        (1 if included else 0, project_id, file_id),
    )
    db.commit()
    if cur.rowcount > 0:
        invalidate_workspace_cache(project_id)
    return cur.rowcount > 0


def fetch_substrate_entry(project_id: str, file_id: str) -> dict[str, Any] | None:
    """Fetch a single vault entry including extracted_text."""
    rows = fetch_substrate_entries_by_ids(project_id, [file_id])
    if not rows:
        return None
    row = rows[0]
    return {
        "id": row["id"],
        "filename": row["filename"],
        "extracted_text": row.get("extracted_text") or "",
    }


def fetch_substrate_entries_by_ids(project_id: str, file_ids: list[str]) -> list[dict[str, Any]]:
    """Fetch full rows (including extracted_text) for the given ids — used to ground compile."""
    if not file_ids:
        return []
    init_db()
    db = get_db()
    placeholders = ",".join("?" for _ in file_ids)
    cols = {r[1] for r in db.execute(
        "PRAGMA table_info(substrate_vault)").fetchall()}
    has_pages = "page_count" in cols
    page_expr = "page_count" if has_pages else "NULL as page_count"
    sql = f"""
        SELECT id, filename, extracted_text, {page_expr}
        FROM substrate_vault
        WHERE project_id = ? AND id IN ({placeholders})
    """
    rows = db.execute(sql, (project_id, *file_ids)).fetchall()
    return [
        {"id": row[0], "filename": row[1],
         "extracted_text": row[2] or "",
         "page_count": row[3]}
        for row in rows
    ]


def list_included_vault_text(project_id: str) -> list[dict[str, Any]]:
    """Included vault files with extracted_text for keyword conflict scans."""
    init_db()
    db = get_db()
    rows = db.execute(
        """
        SELECT id, filename, extracted_text
        FROM substrate_vault
        WHERE project_id = ? AND included = 1
        ORDER BY created_at DESC
        """,
        (project_id,),
    ).fetchall()
    return [{"id": row[0], "filename": row[1], "extracted_text": row[2] or ""} for row in rows]


def fetch_latest_substrate(project_id: str) -> dict[str, Any] | None:
    init_db()
    db = get_db()
    row = db.execute(
        """
        SELECT id, filename, page_count, extracted_text, tables_json, forms_json, created_at
        FROM substrate_vault
        WHERE project_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (project_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "filename": row[1],
        "page_count": int(row[2] or 1),
        "text": row[3] or "",
        "tables": json.loads(row[4] or "[]"),
        "forms": json.loads(row[5] or "[]"),
        "created_at": row[6],
    }
