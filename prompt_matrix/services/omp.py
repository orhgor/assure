"""OMP (Open Memory Protocol) service layer for Assure.
Provides structured artifact persistence and lineage tracking."""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

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
    OMPPromptReadySpan,
    OMPRelation,
    OMPStateRecord,
    ParseArtifactPayload,
    VerifyArtifactPayload,
)

OMP_DIR_NAME = "omp_artifacts"

log = logging.getLogger(__name__)


def _get_omp_dir() -> Path:
    """The OMP artifacts directory (the data directory's ``omp_artifacts/``).

    Derived from the resolved database path rather than ``PRAGMA
    database_list`` so it answers the same on PostgreSQL, where there is no
    file behind the connection — ``ASSURE_DATA_DIR`` names the directory there.
    """
    try:
        from ..history import _resolve_db_path
    except ImportError:
        from history import _resolve_db_path
    base_dir = _resolve_db_path().parent
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


def store_omp_artifact(
    project_id: str,
    artifact: OMPArtifact,
    *,
    storage_backend: str | None = None,
    s3_bucket: str | None = None,
    s3_prefix: str | None = None,
) -> str:
    """Store an OMP artifact, returning the artifact_id.

    The database row (``omp_artifacts``) is the queryable record and is always
    written. The full JSON is mirrored to object storage so a worker on another
    host, a client bucket, or a later export can read the artifact without the
    database: S3 when ``ASSURE_S3_BUCKET`` is set (or ``storage_backend="s3"``
    with ``s3_bucket``), else the local ``objects/`` directory under the data
    dir — the same key layout either way (``services/object_store``).

    Args:
        storage_backend: "local" or "s3". Defaults from ``ASSURE_S3_BACKEND``,
            and to "s3" automatically when ``ASSURE_S3_BUCKET`` is set.
        s3_bucket / s3_prefix: override the bucket/prefix for this write; an
            explicit ``storage_backend="s3"`` without a bucket anywhere is a
            caller error. An env-level misconfiguration falls back to local
            with a warning — an ingest must not fail on artifact mirroring.
    """
    backend = storage_backend or os.environ.get("ASSURE_S3_BACKEND") or (
        "s3" if (os.environ.get("ASSURE_S3_BUCKET") or "").strip() else "local"
    )
    bucket = s3_bucket or os.environ.get("ASSURE_S3_BUCKET")
    if backend == "s3" and not bucket:
        if storage_backend == "s3":
            raise ValueError("s3_bucket required when storage_backend='s3'")
        log.warning(
            "ASSURE_S3_BACKEND=s3 but ASSURE_S3_BUCKET is not set; falling back to local storage"
        )
        backend = "local"

    artifact_id = _store_row(artifact)

    try:
        from .object_store import LocalObjectStore, S3ObjectStore, get_object_store, omp_key
    except ImportError:
        from services.object_store import LocalObjectStore, S3ObjectStore, get_object_store, omp_key

    if backend == "s3":
        store = S3ObjectStore(bucket, s3_prefix or os.environ.get("ASSURE_S3_PREFIX") or "")
    else:
        store = get_object_store()
        if not isinstance(store, LocalObjectStore):
            # Env selected S3 but the caller asked for local: honour the caller.
            store = LocalObjectStore(_get_omp_dir().parent / "objects")
    try:
        uri = store.put_bytes(
            omp_key(project_id, artifact_id),
            json.dumps(artifact.to_dict(), separators=(",", ":")).encode("utf-8"),  # compact: 30–40 % smaller object
            content_type="application/json",
        )
        log.info("OMP artifact %s mirrored to %s", artifact_id, uri)
    except Exception:
        # Mirroring is best-effort by contract: the row above is the record.
        log.exception("OMP artifact %s: object-store mirror failed", artifact_id)
    return artifact_id


def _store_row(artifact: OMPArtifact) -> str:
    """The ``omp_artifacts`` row — the authoritative, queryable record."""
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
    return artifact.artifact_id


def _store_local(artifact: OMPArtifact) -> str:
    """Backwards-compatible name: row + local mirror."""
    return store_omp_artifact(artifact.project_id, artifact, storage_backend="local")


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
    """Object-store fallback: the mirrored JSON, when the row is gone.

    The key carries the project id, which a bare artifact id does not, so the
    ``omp_artifacts`` row is tried first by the caller; here every project's
    prefix is not enumerable cheaply on S3, so only the legacy local layout
    (``omp_artifacts/<project>/<id>.json``) is scanned.
    """
    try:
        omp_dir = _get_omp_dir()
        if omp_dir.exists():
            for project_dir in omp_dir.iterdir():
                if project_dir.is_dir():
                    file_path = project_dir / f"{artifact_id}.json"
                    if file_path.exists():
                        return OMPArtifact.from_dict(json.loads(file_path.read_text()))
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

    # The literals must be parenthesised: adjacent string literals concatenate
    # only inside parentheses or a continued expression, and as bare statements
    # after a completed assignment the second and third were discarded, leaving
    # a SELECT with no FROM clause.
    query = (
        "SELECT artifact_id, project_id, artifact_type, source_id, parent_artifact_id, "
        "payload_json, confidence_json, provenance_json, created_at, updated_at, metadata_json "
        "FROM omp_artifacts WHERE project_id = ?"
    )
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


def build_prompt_ready_spans(
    *,
    source_id: str,
    source_name: str,
    text: str,
    artifact_id: str | None = None,
    page_count: int | None = None,
    parse_confidence: float | None = None,
    ocr_confidence: float | None = None,
) -> dict[str, Any]:
    """Split parsed text into prompt-ready spans, sections and a summary.

    Boundary is the paragraph — a blank line — because that is the unit the
    compile can cite and the unit the anchoring gate measures. Deriving spans
    here rather than in the compiler means the work happens once, at parse time,
    and the compiler ranks what it is handed instead of re-splitting a blob.

    Deterministic: the same text always yields the same span ids, so a span
    reference survives a re-read of the artifact.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return {"spans": [], "sections": [], "prompt_ready_summary": _span_summary([])}

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", cleaned) if p.strip()]
    if not paragraphs:
        paragraphs = [cleaned]

    spans: list[dict[str, Any]] = []
    for idx, para in enumerate(paragraphs):
        spans.append(
            OMPPromptReadySpan(
                span_id=f"span_{idx}",
                text=" ".join(para.split()),
                source_id=source_id,
                source_name=source_name,
                artifact_id=artifact_id,
                page=None,
                section=None,
                trust="trusted",
                parse_confidence=float(parse_confidence) if parse_confidence is not None else None,
                ocr_confidence=float(ocr_confidence) if ocr_confidence is not None else None,
                provenance={"source_name": source_name, "page_count": page_count},
            ).to_dict()
        )
    return {
        "spans": spans,
        "sections": [],
        "prompt_ready_summary": _span_summary(spans),
    }


def _span_summary(spans: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts a probe or a human can read, without walking the span list."""
    def _bucket(lo: float, hi: float | None) -> int:
        n = 0
        for s in spans:
            c = s.get("parse_confidence")
            if c is None:
                continue
            if c >= lo and (hi is None or c < hi):
                n += 1
        return n

    return {
        "span_count": len(spans),
        "high_confidence_count": _bucket(0.85, None),
        "medium_confidence_count": _bucket(0.60, 0.85),
        "low_confidence_count": _bucket(0.0, 0.60),
        "total_chars": sum(len(str(s.get("text") or "")) for s in spans),
    }


def normalize_parse_artifact(raw_parse: dict[str, Any]) -> dict[str, Any]:
    """Normalize substrate parse output into a parse artifact payload.

    When the parser supplied no spans, they are derived here from the text, so
    every parse artifact carries the prompt-ready channel the compiler consumes
    — a parser that has not been taught to emit spans still produces them.

    Parser metadata (``parser_name``/``source_kind``), structured-asset counts
    (``table_count``/``image_count``/``figure_count``/``asset_summary``) and
    confidence ride through verbatim: None stays None (the honest unknown), a
    reported 0.0 stays 0.0 — no defaults are invented here.
    """
    source_id = raw_parse.get("id", "")
    source_name = raw_parse.get("filename", "")
    parse_conf = raw_parse.get("parse_confidence")
    ocr_conf = raw_parse.get("ocr_confidence")
    parser_name = raw_parse.get("parser_name")
    source_kind = raw_parse.get("source_kind")
    table_count = raw_parse.get("table_count")
    image_count = raw_parse.get("image_count")
    figure_count = raw_parse.get("figure_count")
    asset_summary = raw_parse.get("asset_summary")
    spans = raw_parse.get("spans")
    sections = raw_parse.get("sections")
    summary = raw_parse.get("prompt_ready_summary")

    if not spans:
        derived = build_prompt_ready_spans(
            source_id=source_id,
            source_name=source_name,
            text=raw_parse.get("text", ""),
            page_count=raw_parse.get("page_count"),
            parse_confidence=parse_conf,
            ocr_confidence=ocr_conf,
        )
        spans = derived["spans"]
        summary = summary or derived["prompt_ready_summary"]
        sections = sections or derived["sections"]

    payload = ParseArtifactPayload(
        source_id=source_id,
        source_name=source_name,
        page_count=raw_parse.get("page_count", 1),
        text=raw_parse.get("text", ""),
        tables=raw_parse.get("tables", []),
        forms=raw_parse.get("forms", []),
        file_size_bytes=raw_parse.get("size_bytes", 0),
        is_image=raw_parse.get("is_image", False),
        parse_confidence=parse_conf,
        ocr_confidence=ocr_conf,
        parser_name=parser_name,
        source_kind=source_kind,
        table_count=table_count,
        image_count=image_count,
        figure_count=figure_count,
        asset_summary=asset_summary,
        spans=spans,
        sections=sections,
        prompt_ready_summary=summary,
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
    parse_confidence: float | None = None,
    ocr_confidence: float | None = None,
    parser_name: str | None = None,
    source_kind: str | None = None,
    page_count: int | None = None,
    table_count: int | None = None,
    image_count: int | None = None,
    figure_count: int | None = None,
    asset_summary: dict[str, Any] | None = None,
    verification: dict[str, Any] | None = None,
) -> OMPArtifact:
    """Build an OMP parse artifact from substrate ingestion result.

    Confidence is attached with ``is not None`` guards, never truthiness: a
    parser-reported 0.0 is real data (a parse that genuinely failed to read),
    and dropping it would fabricate an "unknown" where the parser said "zero".
    Unknown stays None. Parser metadata and asset counts go into both the
    payload and the provenance so an artifact is self-describing.

    ``verification`` is the post-parse verification result
    (``services/verification.run_verification_after_parse``): its Z3 status
    rides in the artifact's confidence and its provenance records the
    verification pass itself.
    """
    normalized = normalize_parse_artifact(
        {
            **substrate_result,
            **(
                {
                    "parse_confidence": parse_confidence,
                    "ocr_confidence": ocr_confidence,
                    "parser_name": parser_name,
                    "source_kind": source_kind,
                    "table_count": table_count,
                    "image_count": image_count,
                    "figure_count": figure_count,
                    "asset_summary": asset_summary,
                    "page_count": (
                        page_count
                        if page_count is not None
                        else substrate_result.get("page_count")
                    ),
                }
            ),
        }
    )

    provenance: dict[str, Any] = {
        "source_name": substrate_result.get("filename"),
        "parser_name": parser_name,
        "source_kind": source_kind,
        "page_count": (
            page_count if page_count is not None else substrate_result.get("page_count")
        ),
        "file_size_bytes": substrate_result.get("size_bytes"),
    }
    if verification is not None:
        provenance["verification"] = {
            "z3_status": verification.get("z3_status"),
            "redhat_status": verification.get("redhat_status"),
        }

    confidence_payload: dict[str, Any] | None = None
    if parse_confidence is not None or ocr_confidence is not None:
        confidence_payload = {
            "parse": parse_confidence,
            "ocr": ocr_confidence,
            "parser_name": parser_name,
            "source_kind": source_kind,
            "page_count": page_count,
            "table_count": table_count,
            "image_count": image_count,
            "figure_count": figure_count,
            "asset_summary": asset_summary,
        }
    if verification is not None and verification.get("z3_status") is not None:
        confidence_payload = confidence_payload or {}
        confidence_payload["z3_status"] = verification.get("z3_status")

    artifact = OMPArtifact(
        artifact_id=f"omp-parse-{uuid.uuid4().hex[:12]}",
        project_id=project_id,
        artifact_type="parse",
        source_id=substrate_result.get("id"),
        payload=normalized,
        confidence=confidence_payload,
        provenance=provenance,
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