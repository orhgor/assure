"""JDF project persistence on SQLite."""

from __future__ import annotations

import json
import uuid
from typing import Any

try:
    from ..db.connection import init_db
    from ..history import get_db
    from ..models.jdf import JDFDocumentTree
except ImportError:
    from db.connection import init_db
    from history import get_db
    from models.jdf import JDFDocumentTree

DEFAULT_PROJECT_ID = "default"


def empty_document(project_id: str) -> dict[str, Any]:
    return JDFDocumentTree(
        document_id=f"doc-{project_id}",
        meta={"project_id": project_id},
        truth_ledger={},
        body=[],
    ).model_dump(mode="json")


def ensure_project(project_id: str, title: str | None = None) -> None:
    init_db()
    db = get_db()
    label = (title or project_id).strip() or project_id
    db.execute(
        """
        INSERT INTO projects (id, title, current_version)
        VALUES (?, ?, 1)
        ON CONFLICT(id) DO NOTHING
        """,
        (project_id, label),
    )
    db.commit()


def fetch_latest_jdf(project_id: str) -> dict[str, Any] | None:
    init_db()
    db = get_db()
    row = db.execute(
        """
        SELECT jdf_tree FROM jdf_revisions
        WHERE project_id = ?
        ORDER BY version DESC
        LIMIT 1
        """,
        (project_id,),
    ).fetchone()
    if row:
        return json.loads(row[0])

    legacy = db.execute(
        "SELECT tree_json FROM jdf_documents WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    if legacy:
        return json.loads(legacy[0])
    return None


def fetch_latest_jdf_or_empty(project_id: str) -> dict[str, Any]:
    return fetch_latest_jdf(project_id) or empty_document(project_id)


def save_jdf_revision(
    project_id: str,
    document: JDFDocumentTree | dict[str, Any],
    *,
    mutation_type: str,
    target_node_id: str | None = None,
    change_summary: str | None = None,
) -> dict[str, Any]:
    init_db()
    ensure_project(project_id)
    db = get_db()

    if isinstance(document, JDFDocumentTree):
        tree = document.model_dump(mode="json")
    else:
        tree = JDFDocumentTree.model_validate(document).model_dump(mode="json")

    row = db.execute(
        "SELECT COALESCE(MAX(version), 0) FROM jdf_revisions WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    next_version = int(row[0]) + 1
    revision_id = f"rev-{uuid.uuid4().hex[:16]}"
    truth = json.dumps(tree.get("truth_ledger") or {})

    db.execute(
        """
        UPDATE projects
        SET current_version = ?, updated_at = datetime('now')
        WHERE id = ?
        """,
        (next_version, project_id),
    )
    db.execute(
        """
        INSERT INTO jdf_revisions (
            id, project_id, version, jdf_tree, truth_ledger,
            mutation_type, target_node_id, change_summary
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            revision_id,
            project_id,
            next_version,
            json.dumps(tree),
            truth,
            mutation_type,
            target_node_id,
            change_summary,
        ),
    )
    db.execute(
        """
        INSERT INTO jdf_documents (project_id, document_id, tree_json, updated_at)
        VALUES (?, ?, ?, datetime('now'))
        ON CONFLICT(project_id) DO UPDATE SET
            document_id = excluded.document_id,
            tree_json = excluded.tree_json,
            updated_at = datetime('now')
        """,
        (project_id, tree.get("document_id") or f"doc-{project_id}", json.dumps(tree)),
    )
    db.commit()
    return {
        "ok": True,
        "version": next_version,
        "revision_id": revision_id,
        "document": tree,
    }
