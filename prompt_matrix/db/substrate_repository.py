"""Substrate Vault persistence for Textract extractions."""

from __future__ import annotations

import json
import uuid
from typing import Any

try:
    from ..db.connection import init_db
    from ..history import get_db
except ImportError:
    from db.connection import init_db
    from history import get_db


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
) -> dict[str, Any]:
    """Persist a Textract extraction in substrate_vault."""
    init_db()
    db = get_db()
    vault_id = entry_id or f"sub-{uuid.uuid4().hex[:16]}"
    db.execute(
        """
        INSERT INTO substrate_vault (
            id, project_id, filename, page_count,
            extracted_text, tables_json, forms_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            vault_id,
            project_id,
            filename,
            int(page_count),
            extracted_text or "",
            json.dumps(tables or []),
            json.dumps(forms or []),
        ),
    )
    db.commit()
    return {
        "id": vault_id,
        "project_id": project_id,
        "filename": filename,
        "page_count": int(page_count),
        "extracted_text": extracted_text or "",
        "tables": tables or [],
        "forms": forms or [],
    }


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
