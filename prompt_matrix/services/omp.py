"""OMP (Open Memory Protocol) service layer for Assure.
Provides structured artifact persistence and lineage tracking."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from ..db.connection import init_db
    from ..history import get_db
except ImportError:
    from db.connection import init_db
    from history import get_db

from ..models.omp import (
    OMPArtifact,
    OMPArtifactType,
    OMPLinkage,
    OMPRelation,
    OMPStateRecord,
    ParseArtifactPayload,
    VerifyArtifactPayload,
)

OMP_DIR_NAME = "omp_artifacts"


def _get_omp_dir() -> Path:
    """Get the OMP artifacts directory, creating if needed."""
    db = get_db()
    db_path = db.execute("PRAGMA database_list").fetchone()
    if db_path:
        base_dir = Path(db_path[2]).parent
    else:
        base_dir = Path.cwd()
    omp_dir = base_dir / OMP_DIR_NAME
    omp_dir.mkdir(parents=True, exist_ok=True)
    return omp_dir


def _init_omp_tables() -> None:
    """Initialize OMP tables in SQLite."""
    init_db()
    db = get_db()

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS omp_artifacts (
            artifact_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            artifact_type TEXT NOT NULL,
            source_id TEXT,
            parent_artifact_id TEXT,
            payload_json TEXT NOT NULL,
            confidence_json TEXT,
            provenance_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            metadata_json TEXT
        )
        """
    )

    db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_omp_artifacts_project
        ON omp_artifacts(project_id)
        """
    )

    db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_omp_artifacts_source
        ON omp_artifacts(source_id)
        """
    )

    db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_omp_artifacts_type
        ON omp_artifacts(artifact_type)
        """
    )

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS omp_linkages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            parent_artifact_id TEXT NOT NULL,
            child_artifact_id TEXT NOT NULL,
            relation TEXT NOT NULL,
            project_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            metadata_json TEXT
        )
        """
    )

    db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_omp_linkages_parent
        ON omp_linkages(parent_artifact_id)
        """
    )

    db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_omp_linkages_child
        ON omp_linkages(child_artifact_id)
        """
    )

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS omp_states (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artifact_id TEXT NOT NULL,
            state TEXT NOT NULL,
            payload_json TEXT,
            project_id TEXT,
            recorded_at TEXT NOT NULL
        )
        """
    )

    db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_omp_states_artifact
        ON omp_states(artifact_id)
        """
    )

    db.commit()


def _artifact_row_to_dict(row: Any) -> dict[str, Any]:
    """Convert database row to artifact dict."""
    return {
        "artifact_id": row[0],
        "project_id": row[1],
        "artifact_type": row[2],
        "source_id": row[3],
        "parent_artifact_id": row[4],
        "payload": json.loads(row[5]) if row[5] else {},
        "confidence": json.loads(row[6]) if row[6] else None,
        "provenance": json.loads(row[7]) if row[7] else None,
        "created_at": row[8],
        "updated_at": row[9],
        "metadata": json.loads(row[10]) if row[10] else {},
    }


def store_omp_artifact(project_id: str, artifact: OMPArtifact) -> str:
    """Store an OMP artifact, returning the artifact_id."""
    _init_omp_tables()
    init_db()
    db = get_db()

    now = datetime.utcnow().isoformat()
    if not artifact.artifact_id:
        artifact.artifact_id = f"omp-{uuid.uuid4().hex[:16]}"

    artifact.created_at = artifact.created_at or now
    artifact.updated_at = now

    db.execute(
        """
        INSERT OR REPLACE INTO omp_artifacts (
            artifact_id, project_id, artifact_type, source_id,
            parent_artifact_id, payload_json, confidence_json,
            provenance_json, created_at, updated_at, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            artifact.artifact_id,
            artifact.project_id,
            artifact.artifact_type,
            artifact.source_id,
            artifact.parent_artifact_id,
            json.dumps(artifact.payload),
            json.dumps(artifact.confidence) if artifact.confidence else None,
            json.dumps(artifact.provenance) if artifact.provenance else None,
            artifact.created_at,
            artifact.updated_at,
            json.dumps(artifact.metadata) if artifact.metadata else None,
        ),
    )
    db.commit()

    # Also persist to filesystem for backup/portability
    _persist_artifact_to_disk(artifact)

    return artifact.artifact_id


def _persist_artifact_to_disk(artifact: OMPArtifact) -> None:
    """Persist artifact to filesystem as JSON."""
    try:
        omp_dir = _get_omp_dir()
        project_dir = omp_dir / artifact.project_id
        project_dir.mkdir(parents=True, exist_ok=True)
        file_path = project_dir / f"{artifact.artifact_id}.json"
        file_path.write_text(json.dumps(artifact.to_dict(), indent=2))
    except Exception:
        # Disk persistence is best-effort
        pass


def load_omp_artifact(artifact_id: str) -> OMPArtifact | None:
    """Load an OMP artifact by ID."""
    _init_omp_tables()
    init_db()
    db = get_db()

    row = db.execute(
        "SELECT artifact_id, project_id, artifact_type, source_id, parent_artifact_id, "
        "payload_json, confidence_json, provenance_json, created_at, updated_at, metadata_json "
        "FROM omp_artifacts WHERE artifact_id = ?",
        (artifact_id,),
    ).fetchone()

    if not row:
        # Try disk fallback
        return _load_artifact_from_disk(artifact_id)

    return OMPArtifact.from_dict(_artifact_row_to_dict(row))


def _load_artifact_from_disk(artifact_id: str) -> OMPArtifact | None:
    """Load artifact from filesystem fallback."""
    try:
        omp_dir = _get_omp_dir()
        for project_dir in omp_dir.iterdir():
            if project_dir.is_dir():
                file_path = project_dir / f"{artifact_id}.json"
                if file_path.exists():
                    data = json.loads(file_path.read_text())
                    return OMPArtifact.from_dict(data)
    except Exception:
        pass
    return None


def list_omp_artifacts(
    project_id: str,
    artifact_type: OMPArtifactType | None = None,
    source_id: str | None = None,
) -> list[OMPArtifact]:
    """List OMP artifacts for a project, optionally filtered."""
    _init_omp_tables()
    init_db()
    db = get_db()

    query = "SELECT artifact_id, project_id, artifact_type, source_id, parent_artifact_id, "
    "payload_json, confidence_json, provenance_json, created_at, updated_at, metadata_json "
    "FROM omp_artifacts WHERE project_id = ?"
    params = [project_id]

    if artifact_type:
        query += " AND artifact_type = ?"
        params.append(artifact_type)
    if source_id:
        query += " AND source_id = ?"
        params.append(source_id)

    query += " ORDER BY created_at DESC"

    rows = db.execute(query, params).fetchall()
    return [OMPArtifact.from_dict(_artifact_row_to_dict(row)) for row in rows]


def link_omp_artifacts(
    parent_artifact_id: str,
    child_artifact_id: str,
    relation: OMPRelation,
    project_id: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Create a lineage link between two OMP artifacts."""
    _init_omp_tables()
    init_db()
    db = get_db()

    linkage = OMPLinkage(
        parent_artifact_id=parent_artifact_id,
        child_artifact_id=child_artifact_id,
        relation=relation,
        project_id=project_id,
        metadata=metadata or {},
    )

    db.execute(
        """
        INSERT INTO omp_linkages (
            parent_artifact_id, child_artifact_id, relation,
            project_id, created_at, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            linkage.parent_artifact_id,
            linkage.child_artifact_id,
            linkage.relation,
            linkage.project_id,
            linkage.created_at,
            json.dumps(linkage.metadata),
        ),
    )
    db.commit()


def get_omp_linkages(
    artifact_id: str,
    direction: Literal["parents", "children", "both"] = "both",
) -> list[OMPLinkage]:
    """Get lineage links for an artifact."""
    _init_omp_tables()
    init_db()
    db = get_db()

    linkages = []

    if direction in ("parents", "both"):
        rows = db.execute(
            "SELECT parent_artifact_id, child_artifact_id, relation, project_id, created_at, metadata_json "
            "FROM omp_linkages WHERE child_artifact_id = ?",
            (artifact_id,),
        ).fetchall()
        for row in rows:
            linkages.append(
                OMPLinkage(
                    parent_artifact_id=row[0],
                    child_artifact_id=row[1],
                    relation=row[2],
                    project_id=row[3],
                    created_at=row[4],
                    metadata=json.loads(row[5]) if row[5] else {},
                )
            )

    if direction in ("children", "both"):
        rows = db.execute(
            "SELECT parent_artifact_id, child_artifact_id, relation, project_id, created_at, metadata_json "
            "FROM omp_linkages WHERE parent_artifact_id = ?",
            (artifact_id,),
        ).fetchall()
        for row in rows:
            linkages.append(
                OMPLinkage(
                    parent_artifact_id=row[0],
                    child_artifact_id=row[1],
                    relation=row[2],
                    project_id=row[3],
                    created_at=row[4],
                    metadata=json.loads(row[5]) if row[5] else {},
                )
            )

    return linkages


def record_omp_state(
    artifact_id: str,
    state: str,
    payload: dict[str, Any] | None = None,
    project_id: str | None = None,
) -> None:
    """Record a state transition for an artifact."""
    _init_omp_tables()
    init_db()
    db = get_db()

    state_record = OMPStateRecord(
        artifact_id=artifact_id,
        state=state,
        payload=payload,
        project_id=project_id,
    )

    db.execute(
        """
        INSERT INTO omp_states (artifact_id, state, payload_json, project_id, recorded_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            state_record.artifact_id,
            state_record.state,
            json.dumps(state_record.payload) if state_record.payload else None,
            state_record.project_id,
            state_record.recorded_at,
        ),
    )
    db.commit()


def get_omp_states(artifact_id: str) -> list[OMPStateRecord]:
    """Get all state records for an artifact."""
    _init_omp_tables()
    init_db()
    db = get_db()

    rows = db.execute(
        "SELECT artifact_id, state, payload_json, project_id, recorded_at "
        "FROM omp_states WHERE artifact_id = ? ORDER BY recorded_at",
        (artifact_id,),
    ).fetchall()

    return [
        OMPStateRecord(
            artifact_id=row[0],
            state=row[1],
            payload=json.loads(row[2]) if row[2] else None,
            project_id=row[3],
            recorded_at=row[4],
        )
        for row in rows
    ]


def normalize_parse_artifact(raw_parse: dict[str, Any]) -> dict[str, Any]:
    """Normalize substrate parse output into a parse artifact payload."""
    payload = ParseArtifactPayload(
        source_id=raw_parse.get("id", ""),
        source_name=raw_parse.get("filename", ""),
        page_count=raw_parse.get("page_count", 1),
        text=raw_parse.get("text", ""),
        tables=raw_parse.get("tables", []),
        forms=raw_parse.get("forms", []),
        file_size_bytes=raw_parse.get("size_bytes", 0),
        is_image=raw_parse.get("is_image", False),
        parse_confidence=raw_parse.get("parse_confidence"),
        ocr_confidence=raw_parse.get("ocr_confidence"),
        spans=raw_parse.get("spans"),
    )
    return payload.to_dict()


def normalize_verify_artifact(jdf: dict[str, Any], confidence: dict[str, Any]) -> dict[str, Any]:
    """Normalize JDF + confidence into a verification artifact payload."""
    meta = jdf.get("meta", {})
    payload = VerifyArtifactPayload(
        jdf=jdf,
        confidence=confidence,
        gate_status=meta.get("gate_status"),
        z3_status=meta.get("z3_status"),
        provenance_stats={
            "total_nodes": len(_collect_nodes(jdf)),
            "nodes_with_provenance": sum(1 for n in _collect_nodes(jdf) if n.get("provenance")),
        },
    )
    return payload.to_dict()


def _collect_nodes(tree: dict[str, Any]) -> list[dict[str, Any]]:
    """Recursively collect all nodes from JDF body."""
    nodes = []

    def collect(section: dict[str, Any]) -> None:
        if not isinstance(section, dict):
            return
        nodes.append(section)
        for child in section.get("children") or []:
            collect(child)

    for section in tree.get("body") or []:
        collect(section)
    return nodes


def build_omp_artifact_from_parse(
    project_id: str,
    substrate_result: dict[str, Any],
    parse_confidence: int | None = None,
    ocr_confidence: int | None = None,
) -> OMPArtifact:
    """Build an OMP parse artifact from substrate ingestion result."""
    normalized = normalize_parse_artifact(substrate_result)
    normalized["parse_confidence"] = parse_confidence
    normalized["ocr_confidence"] = ocr_confidence

    artifact = OMPArtifact(
        artifact_id=f"omp-parse-{uuid.uuid4().hex[:12]}",
        project_id=project_id,
        artifact_type="parse",
        source_id=substrate_result.get("id"),
        payload=normalized,
        confidence={"parse": parse_confidence, "ocr": ocr_confidence}
        if parse_confidence or ocr_confidence
        else None,
        provenance={"source_name": substrate_result.get("filename")},
    )
    return artifact


def build_omp_artifact_from_verify(
    project_id: str,
    jdf: dict[str, Any],
    confidence: dict[str, Any],
    parse_artifact_id: str | None = None,
) -> OMPArtifact:
    """Build an OMP verification artifact from JDF + confidence."""
    normalized = normalize_verify_artifact(jdf, confidence)

    artifact = OMPArtifact(
        artifact_id=f"omp-verify-{uuid.uuid4().hex[:12]}",
        project_id=project_id,
        artifact_type="verification",
        parent_artifact_id=parse_artifact_id,
        payload=normalized,
        confidence=confidence,
        provenance={"jdf_document_id": jdf.get("document_id")},
    )
    return artifact


def build_omp_artifact_from_compile_prompt(
    project_id: str,
    structured_prompt: dict[str, Any],
    source_artifact_ids: list[str],
) -> OMPArtifact:
    """Build an OMP compile prompt artifact."""
    artifact = OMPArtifact(
        artifact_id=f"omp-compile-{uuid.uuid4().hex[:12]}",
        project_id=project_id,
        artifact_type="compile_prompt",
        payload=structured_prompt,
        provenance={"source_artifact_ids": source_artifact_ids},
    )
    return artifact


def build_omp_artifact_from_evidence(
    project_id: str,
    evidence_results: list[dict[str, Any]],
    compile_artifact_id: str | None = None,
) -> OMPArtifact:
    """Build an OMP evidence artifact from evidence assembly results."""
    artifact = OMPArtifact(
        artifact_id=f"omp-evidence-{uuid.uuid4().hex[:12]}",
        project_id=project_id,
        artifact_type="evidence",
        parent_artifact_id=compile_artifact_id,
        payload={"evidence": evidence_results},
        provenance={"count": len(evidence_results)},
    )
    return artifact