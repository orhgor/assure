"""Project template catalog (wizard type step) — Big Four ICP architecture."""

from __future__ import annotations

import json
from typing import Any

try:
    from ..db.connection import init_db
    from ..history import get_db
except ImportError:
    from db.connection import init_db
    from history import get_db

# Ordered Big Four + blank workspace (API surface only).
BIG_FOUR_IDS = (
    "research-dossier",
    "compliance-memo",
    "contract-review",
    "blank",
)

BIG_FOUR_META: dict[str, dict[str, str]] = {
    "research-dossier": {
        "icon": "📄",
        "name": "Research Dossier",
        "description": (
            "Synthesize scattered findings, map claims to primary sources, "
            "and verify citations before publishing."
        ),
    },
    "compliance-memo": {
        "icon": "⚖️",
        "name": "Compliance Memo",
        "description": (
            "Audit regulatory filings, policies, and statutory statements "
            "against binding guidelines."
        ),
    },
    "contract-review": {
        "icon": "📑",
        "name": "Contract Review",
        "description": (
            "Cross-check terms, redlines, and commitments across multi-party agreements."
        ),
    },
    "blank": {
        "icon": "➕",
        "name": "Blank Workspace",
        "description": (
            "Start from scratch. Drop raw documents into the vault and compile "
            "custom audit assertions."
        ),
    },
}


def _enrich_template(row: dict[str, Any]) -> dict[str, Any]:
    tid = row.get("id") or ""
    meta = BIG_FOUR_META.get(tid, {})
    out = dict(row)
    if meta.get("name"):
        out["name"] = meta["name"]
    out["icon"] = meta.get("icon", "")
    out["description"] = meta.get("description", "")
    return out


def list_templates() -> list[dict[str, Any]]:
    init_db()
    db = get_db()
    rows = db.execute(
        """
        SELECT id, name, jdf_structure, default_prompt, suggested_sources, created_at
        FROM project_templates
        """
    ).fetchall()
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        by_id[row[0]] = {
            "id": row[0],
            "name": row[1],
            "jdf_structure": json.loads(row[2] or "{}"),
            "default_prompt": row[3] or "",
            "suggested_sources": json.loads(row[4] or "[]"),
            "created_at": row[5],
        }
    out: list[dict[str, Any]] = []
    for tid in BIG_FOUR_IDS:
        if tid in by_id:
            out.append(_enrich_template(by_id[tid]))
    return out


def fetch_template(template_id: str) -> dict[str, Any] | None:
    init_db()
    db = get_db()
    row = db.execute(
        """
        SELECT id, name, jdf_structure, default_prompt, suggested_sources, created_at
        FROM project_templates WHERE id = ?
        """,
        (template_id,),
    ).fetchone()
    if not row:
        return None
    tpl = {
        "id": row[0],
        "name": row[1],
        "jdf_structure": json.loads(row[2] or "{}"),
        "default_prompt": row[3] or "",
        "suggested_sources": json.loads(row[4] or "[]"),
        "created_at": row[5],
    }
    if template_id in BIG_FOUR_META:
        return _enrich_template(tpl)
    return tpl
