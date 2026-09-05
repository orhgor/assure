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


def fetch_jdf_at_version(project_id: str, version: int) -> dict[str, Any] | None:
    init_db()
    db = get_db()
    row = db.execute(
        """
        SELECT jdf_tree FROM jdf_revisions
        WHERE project_id = ? AND version = ?
        """,
        (project_id, int(version)),
    ).fetchone()
    if not row:
        return None
    return json.loads(row[0])


def list_jdf_revisions(project_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
    init_db()
    ensure_project(project_id)
    db = get_db()
    rows = db.execute(
        """
        SELECT id, version, mutation_type, target_node_id, change_summary, created_at
        FROM jdf_revisions
        WHERE project_id = ?
        ORDER BY version DESC
        LIMIT ?
        """,
        (project_id, int(limit)),
    ).fetchall()
    return [
        {
            "revision_id": r[0],
            "version": int(r[1]),
            "mutation_type": r[2],
            "target_node_id": r[3],
            "change_summary": r[4],
            "created_at": r[5],
        }
        for r in rows
    ]


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


def _find_block_json_path(tree: dict[str, Any], node_id: str) -> str | None:
    """Return a JSON path for an existing block node (section children only)."""
    for si, section in enumerate(tree.get("body") or []):
        if not isinstance(section, dict):
            continue
        for ci, child in enumerate(section.get("children") or []):
            if isinstance(child, dict) and child.get("id") == node_id:
                return f"$.body[{si}].children[{ci}]"
    return None


def patch_jdf_node(
    project_id: str,
    node_id: str,
    node_data: dict[str, Any],
    *,
    mutation_type: str = "NODE_UPDATE",
    insert_after_id: str | None = None,
    change_summary: str | None = None,
) -> dict[str, Any]:
    """Surgically upsert one block node; uses JSON1 when updating an existing path."""
    init_db()
    ensure_project(project_id)
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
    base_tree = json.loads(row[0]) if row else empty_document(project_id)

    try:
        from ..models.jdf import upsert_block_node
    except ImportError:
        from models.jdf import upsert_block_node

    node_data = dict(node_data)
    node_data["id"] = node_id
    json_path = _find_block_json_path(base_tree, node_id)
    if json_path:
        patched_row = db.execute(
            "SELECT json_set(?, ?, json(?))",
            (json.dumps(base_tree), json_path, json.dumps(node_data)),
        ).fetchone()
        tree = json.loads(patched_row[0])
    else:
        tree, _ = upsert_block_node(
            base_tree,
            node_id,
            node_data,
            insert_after_id=insert_after_id,
        )

    return save_jdf_revision(
        project_id,
        tree,
        mutation_type=mutation_type,
        target_node_id=node_id,
        change_summary=change_summary,
    )


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
    node_snapshot = None
    if target_node_id:
        for block in tree.get("body") or []:
            if isinstance(block, dict) and block.get("id") == target_node_id:
                node_snapshot = block
                break
            for child in block.get("children") or []:
                if isinstance(child, dict) and child.get("id") == target_node_id:
                    node_snapshot = child
                    break
            if node_snapshot:
                break
    db.commit()
    try:
        from .project_files import save_last_compiled
    except ImportError:
        from db.project_files import save_last_compiled
    save_last_compiled(project_id, tree)
    try:
        from .jdf_disk import write_jdf_disk
    except ImportError:
        from db.jdf_disk import write_jdf_disk
    disk_path = write_jdf_disk(project_id, tree)
    if node_snapshot and target_node_id:
        try:
            from .node_revision_repository import save_node_revision
        except ImportError:
            from db.node_revision_repository import save_node_revision
        save_node_revision(
            project_id,
            target_node_id,
            node_snapshot,
            document_version=next_version,
            mutation_type=mutation_type,
            change_summary=change_summary,
        )
    return {
        "ok": True,
        "version": next_version,
        "revision_id": revision_id,
        "document": tree,
        "disk_path": str(disk_path),
    }
