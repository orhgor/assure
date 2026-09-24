"""JDF project persistence on SQLite."""

from __future__ import annotations

import json
import uuid
from typing import Any

try:
    from ..db.connection import init_db
    from ..history import db_scope, get_db
    from ..models.jdf import JDFDocumentTree, parse_document
except ImportError:
    from db.connection import init_db
    from history import get_db
    from models.jdf import JDFDocumentTree, parse_document

DEFAULT_PROJECT_ID = "default"


class RevisionConflict(Exception):
    """Optimistic lock failed: stored version does not match expected_version."""

    def __init__(
        self,
        latest_version: int,
        current_content: dict[str, Any] | None = None,
        message: str = "Conflict: node was modified elsewhere",
    ) -> None:
        super().__init__(message)
        self.latest_version = int(latest_version)
        self.current_content = current_content or {}


def project_owner_id(project_id: str) -> str | None:
    init_db()
    db = get_db()
    row = db.execute("SELECT owner_id FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not row:
        return None
    raw = row[0]
    return str(raw) if raw else None


def project_exists(project_id: str) -> bool:
    """True when a ``projects`` row exists for ``project_id``.

    Distinct from ``project_owner_id``, which returns None both for a missing
    row and for a row with no owner — the pre-auth ``legacy`` projects — so it
    cannot tell a ghost project from an unowned one.
    """
    if not project_id:
        return False
    init_db()
    row = get_db().execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone()
    return row is not None


def new_revision_id() -> str:
    """A document revision id.

    ``save_jdf_revision`` generates one itself; a caller that has to know the id
    *before* the write — a surgical rewrite stamps the revision that closed a
    finding onto the node it rewrote, and that node is serialized by that same
    write — generates it here and passes it in.
    """
    return f"rev-{uuid.uuid4().hex[:16]}"


def close_findings_for_revision(
    project_id: str,
    tree: JDFDocumentTree | dict[str, Any],
    node_id: str,
    node: dict[str, Any],
    *,
    mutation_type: str,
    resolved_at: str | None = None,
) -> dict[str, Any]:
    """``node`` with the findings this rewrite closes, named against the next revision.

    Returns ``{"node", "revision_id", "version"}``: the node as it is to be
    written, and the identity the write has to carry — the same
    ``revision_id`` passed to ``save_jdf_revision`` and the version it will
    assign when every revision so far is still in place. The node records the
    revision that answered the finding, so the id has to exist before the write
    rather than being read back from it.

    A rewrite that replaces a paragraph the audit convicted used to drop the
    finding with it, and the document then read as one that never had a finding.
    See ``models.jdf.resolve_findings_on_rewrite``: the finding survives,
    ``resolved``, and the paragraph's own grounding does not — it keeps no
    citation it was not matched against.
    """
    try:
        from ..models.jdf import get_node_by_id, resolve_findings_on_rewrite
    except ImportError:
        from models.jdf import get_node_by_id, resolve_findings_on_rewrite

    revision_id = new_revision_id()
    version = current_document_version(project_id) + 1
    return {
        "node": resolve_findings_on_rewrite(
            node,
            get_node_by_id(tree, node_id),
            revision_id=revision_id,
            version=version,
            mutation_type=mutation_type,
            resolved_at=resolved_at,
        ),
        "revision_id": revision_id,
        "version": version,
    }


def current_document_version(project_id: str) -> int:
    init_db()
    db = get_db()
    row = db.execute(
        "SELECT COALESCE(MAX(version), 0) FROM jdf_revisions WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    return int(row[0] or 0)


def empty_document(project_id: str) -> dict[str, Any]:
    return JDFDocumentTree(
        document_id=f"doc-{project_id}",
        meta={"project_id": project_id},
        truth_ledger={},
        body=[],
        confidence=None,
        confidenceBreakdown=None,
        lowConfidenceNodes=None,
    ).model_dump(mode="json")


def ensure_project(project_id: str, title: str | None = None, owner_id: str | None = None) -> None:
    with db_scope() as db:
        init_db(db)
        label = (title or project_id).strip() or project_id
        db.execute(
            """
            INSERT INTO projects (id, title, current_version, owner_id)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (project_id, label, owner_id),
        )
        if owner_id:
            db.execute(
                """
                UPDATE projects
                SET owner_id = ?
                WHERE id = ? AND (owner_id IS NULL OR owner_id = '')
                """,
                (owner_id, project_id),
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
    expected_version: int | None = None,
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
    # Always the Python upsert. The SQLite-era branch patched the tree with
    # `SELECT json_set(?, ?, json(?))`, which PostgreSQL has no function for
    # (pg_compat provides datetime/json_extract/randomblob/hex only), so every
    # update of an existing block node raised ProgrammingError → 500 on
    # PUT /api/projects/<id>/jdf (audit 2026-09-23). upsert_block_node already
    # replaces an existing node in place.
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
        expected_version=expected_version,
    )


def save_jdf_revision(
    project_id: str,
    document: JDFDocumentTree | dict[str, Any],
    *,
    mutation_type: str,
    target_node_id: str | None = None,
    change_summary: str | None = None,
    expected_version: int | None = None,
    revision_id: str | None = None,
) -> dict[str, Any]:
    init_db()
    ensure_project(project_id)
    db = get_db()

    if isinstance(document, JDFDocumentTree):
        tree = document.model_dump(mode="json")
    else:
        tree = parse_document(document).model_dump(mode="json")

    if change_summary is None:
        try:
            body = tree.get("body") if isinstance(tree, dict) else None
            first = body[0] if body and isinstance(body, list) else {}
            title = first.get("title") if isinstance(first, dict) else ""
            change_summary = str(title)[:120] if title else None
        except Exception:
            change_summary = None

    revision_id = revision_id or new_revision_id()
    truth = json.dumps(tree.get("truth_ledger") or {})
    #: Assigned inside ``_persist_revision``, under the write lock. Read after the
    #: call for the node snapshot and the return value.
    next_version = 0

    def _persist_revision() -> None:
        """Assign this revision's version and write it, as one transaction.

        ``UNIQUE (project_id, version)`` makes the version a lock that the read and
        the write have to share, and they did not: ``SELECT MAX(version)`` ran
        outside the closure, so two compiles whose persists landed in the same
        instant both read the same maximum and both tried to insert the same
        version. Measured with a write lock held by a second connection (the window,
        forced open) and eight callers: seven raised ``database is locked`` and one
        raised ``UNIQUE constraint failed: jdf_revisions.project_id,
        jdf_revisions.version`` — and three rows appeared anyway, because a retry
        after a failed commit re-ran the INSERT against the still-open transaction
        and wrote a second revision. The invariant is that two concurrent compiles
        both persist and neither raises.

        So the first statement here is a write: SQLite takes the write lock on it,
        which is what makes the ``MAX(version)`` below and the INSERT that uses it
        one transaction. A retried attempt re-reads against the state that actually
        exists instead of reusing a stale maximum, and the rollback keeps a failed
        attempt from leaving a row behind for the next one to duplicate.
        """
        nonlocal next_version
        try:
            db.execute(
                "UPDATE projects SET updated_at = datetime('now') WHERE id = ?",
                (project_id,),
            )
            current_version = int(
                db.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM jdf_revisions WHERE project_id = ?",
                    (project_id,),
                ).fetchone()[0]
                or 0
            )
            if expected_version is not None and current_version != int(expected_version):
                # Roll back first: the conflict payload reads the database through
                # init_db, and a PRAGMA cannot run inside the transaction this
                # statement just opened.
                db.rollback()
                raise RevisionConflict(current_version, fetch_latest_jdf_or_empty(project_id))
            next_version = current_version + 1
            tree_json = json.dumps(tree)  # serialised once for both rows (was twice per save)
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
                    tree_json,
                    truth,
                    mutation_type,
                    target_node_id,
                    change_summary,
                ),
            )
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
                INSERT INTO jdf_documents (project_id, document_id, tree_json, updated_at)
                VALUES (?, ?, ?, datetime('now'))
                ON CONFLICT(project_id) DO UPDATE SET
                    document_id = excluded.document_id,
                    tree_json = excluded.tree_json,
                    updated_at = datetime('now')
                """,
                (project_id, tree.get("document_id") or f"doc-{project_id}", tree_json),
            )
            db.commit()
        except Exception:
            # A retry must start from the state this attempt did not change.
            db.rollback()
            raise

    try:
        from .connection import execute_write_with_retry
    except ImportError:
        from connection import execute_write_with_retry
    execute_write_with_retry(_persist_revision)

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


def save_omp_linkage(project_id: str, jdf_revision_id: str, omp_artifact_ids: list[str]) -> None:
    """Persist OMP artifact IDs linked to a JDF revision for audit trail."""
    init_db()
    db = get_db()

    for artifact_id in omp_artifact_ids:
        db.execute(
            """
            INSERT OR IGNORE INTO jdf_omp_linkages (jdf_revision_id, omp_artifact_id, project_id, created_at)
            VALUES (?, ?, ?, datetime('now'))
            """,
            (jdf_revision_id, artifact_id, project_id),
        )
    db.commit()


def get_omp_linkages_for_revision(jdf_revision_id: str) -> list[str]:
    """Get OMP artifact IDs linked to a JDF revision."""
    init_db()
    db = get_db()

    rows = db.execute(
        "SELECT omp_artifact_id FROM jdf_omp_linkages WHERE jdf_revision_id = ?",
        (jdf_revision_id,),
    ).fetchall()
    return [row[0] for row in rows]


def init_omp_linkage_table() -> None:
    """Initialize the JDF-OMP linkage table."""
    init_db()
    db = get_db()

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS jdf_omp_linkages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            jdf_revision_id TEXT NOT NULL,
            omp_artifact_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_jdf_omp_linkages_revision
        ON jdf_omp_linkages(jdf_revision_id)
        """
    )
    db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_jdf_omp_linkages_artifact
        ON jdf_omp_linkages(omp_artifact_id)
        """
    )
    db.commit()
