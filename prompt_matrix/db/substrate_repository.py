"""Substrate Vault persistence for Textract extractions."""

from __future__ import annotations

import json
import uuid
from typing import Any

try:
    from ..db.connection import init_db
    from ..history import db_scope, get_db
    from ..lib.source_labels import fetched_url_of
    from ..services.vault_tfidf_cache import invalidate_workspace_cache
except ImportError:
    from db.connection import init_db
    from history import get_db
    from lib.source_labels import fetched_url_of
    from services.vault_tfidf_cache import invalidate_workspace_cache


def _scan_version() -> str:
    try:
        from ..services.compile_guard import scan_version
    except ImportError:
        from services.compile_guard import scan_version
    return scan_version()


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


def _parse_meta_return(
    parser_name: str | None,
    source_kind: str | None,
    parse_confidence: float | None,
    ocr_confidence: float | None,
    table_count: int | None,
    image_count: int | None,
    figure_count: int | None,
    asset_summary: dict[str, Any] | None,
    omp_artifact_id: str | None,
) -> dict[str, Any]:
    """The parse-metadata block every vault write returns, shape-stable.

    Confidence fields pass through as-is: ``None`` means the parser reported
    nothing and must stay ``None`` — 0 would read as "parsed with zero
    confidence", which no parser ever said.
    """
    return {
        "parser_name": parser_name,
        "source_kind": source_kind,
        "parse_confidence": parse_confidence,
        "ocr_confidence": ocr_confidence,
        "table_count": table_count,
        "image_count": image_count,
        "figure_count": figure_count,
        "asset_summary": asset_summary or {},
        "omp_artifact_id": omp_artifact_id,
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
    instruction_like: bool = False,
    instruction_hits: list[str] | None = None,
    parser_name: str | None = None,
    source_kind: str | None = None,
    parse_confidence: float | None = None,
    ocr_confidence: float | None = None,
    table_count: int | None = None,
    image_count: int | None = None,
    figure_count: int | None = None,
    asset_summary: dict[str, Any] | None = None,
    omp_artifact_id: str | None = None,
) -> dict[str, Any]:
    """Persist a Textract extraction in substrate_vault.

    ``instruction_like``/``instruction_hits`` are the ingest scan's verdict
    (``services/compile_guard.scan_source_instruction_like``), kept on the row as
    the record of what the scan that ran at ingest said. They are not the verdict
    a reader sees: ``list_substrate_for_project`` re-runs the scan on read, since
    the phrase set changes and a stored verdict can outlive it.
    """
    with db_scope() as db:
        init_db(db)
        vault_id = entry_id or f"sub-{uuid.uuid4().hex[:16]}"
        db.execute(
            """
            INSERT INTO substrate_vault (
                id, project_id, filename, page_count,
                extracted_text, tables_json, forms_json, file_size_bytes,
                instruction_like, instruction_hits, scan_version,
                parser_name, source_kind, parse_confidence, ocr_confidence,
                table_count, image_count, figure_count, asset_summary,
                omp_artifact_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                1 if instruction_like else 0,
                json.dumps(list(instruction_hits or [])),
                _scan_version(),
                parser_name,
                source_kind,
                parse_confidence,
                ocr_confidence,
                table_count,
                image_count,
                figure_count,
                json.dumps(asset_summary or {}),
                omp_artifact_id,
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
            "instruction_like": bool(instruction_like),
            "instruction_hits": list(instruction_hits or []),
            **_parse_meta_return(
                parser_name,
                source_kind,
                parse_confidence,
                ocr_confidence,
                table_count,
                image_count,
                figure_count,
                asset_summary,
                omp_artifact_id,
            ),
        }


def upsert_substrate_entry(
    project_id: str,
    *,
    filename: str,
    page_count: int,
    extracted_text: str,
    tables: list[dict[str, Any]] | None = None,
    forms: list[dict[str, Any]] | None = None,
    file_size_bytes: int = 0,
    instruction_like: bool = False,
    instruction_hits: list[str] | None = None,
    parser_name: str | None = None,
    source_kind: str | None = None,
    parse_confidence: float | None = None,
    ocr_confidence: float | None = None,
    table_count: int | None = None,
    image_count: int | None = None,
    figure_count: int | None = None,
    asset_summary: dict[str, Any] | None = None,
    omp_artifact_id: str | None = None,
) -> dict[str, Any]:
    """Persist an extraction as this project's row for `filename`, replacing its text.

    The JDF ingest runs on every upload of a file, so keying the row on the
    filename keeps one entry — and one id, which the shell posts back as
    substrate_file_ids — instead of stacking a new row per upload. A row created
    by a vault upload of the same name is the same document in this project, so
    it is reused rather than duplicated. Parse metadata (parser name, source
    kind, confidence, structured-asset counts, OMP artifact linkage) is written
    alongside the text so the row records how this document was parsed.
    """
    init_db()
    db = get_db()
    row = db.execute(
        """
        SELECT id FROM substrate_vault
        WHERE project_id = ? AND filename = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (project_id, filename),
    ).fetchone()
    if not row:
        return save_substrate_entry(
            project_id,
            filename=filename,
            page_count=page_count,
            extracted_text=extracted_text,
            tables=tables,
            forms=forms,
            file_size_bytes=file_size_bytes,
            instruction_like=instruction_like,
            instruction_hits=instruction_hits,
            parser_name=parser_name,
            source_kind=source_kind,
            parse_confidence=parse_confidence,
            ocr_confidence=ocr_confidence,
            table_count=table_count,
            image_count=image_count,
            figure_count=figure_count,
            asset_summary=asset_summary,
            omp_artifact_id=omp_artifact_id,
        )
    vault_id = str(row[0])
    db.execute(
        """
        UPDATE substrate_vault
        SET page_count = ?, extracted_text = ?, tables_json = ?, forms_json = ?,
            file_size_bytes = ?, instruction_like = ?, instruction_hits = ?,
            parser_name = ?, source_kind = ?, parse_confidence = ?,
            ocr_confidence = ?, table_count = ?, image_count = ?,
            figure_count = ?, asset_summary = ?, omp_artifact_id = ?
        WHERE project_id = ? AND id = ?
        """,
        (
            int(page_count),
            extracted_text or "",
            json.dumps(tables or []),
            json.dumps(forms or []),
            int(file_size_bytes or 0),
            1 if instruction_like else 0,
            json.dumps(list(instruction_hits or [])),
            parser_name,
            source_kind,
            parse_confidence,
            ocr_confidence,
            table_count,
            image_count,
            figure_count,
            json.dumps(asset_summary or {}),
            omp_artifact_id,
            project_id,
            vault_id,
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
        "instruction_like": bool(instruction_like),
        "instruction_hits": list(instruction_hits or []),
        **_parse_meta_return(
            parser_name,
            source_kind,
            parse_confidence,
            ocr_confidence,
            table_count,
            image_count,
            figure_count,
            asset_summary,
            omp_artifact_id,
        ),
    }


def list_substrate_for_project(project_id: str, *, with_text: bool = False) -> list[dict[str, Any]]:
    """List vault entries for a project (``extracted_text`` only when asked).

    ``instruction_like``/``instruction_hits`` are the ingest scan **re-run on
    read**, not the columns as written. The columns are the record of the scan
    that ran at ingest and that scan is not stable: the phrase set changes when a
    phrase turns out to flag ordinary policy prose. Measured read-only on the
    deployed box, of the five vault rows carrying ``instruction_like = 1``, four
    carry ``instruction_hits`` naming phrases the current scan does not report —
    and one of them, ``brim-cp-media371.pdf``, scans clean, so its flag is wrong
    outright. Both the SOURCES pane and the export's source manifest read those
    columns, so both reported verdicts no code would reach again. The text is read
    in order to scan it either way; it is put in the entry only when ``with_text``
    is set, so the payload is as light as it was.

    ``fetched_url`` is the retrieval path's tag, read back off the row's label
    (``lib/source_labels.py``): the host when the source was fetched from the web,
    "" when it was uploaded. It is derived on read — the vault's columns are
    unchanged — and it is what lets the counters and the SOURCES pane tell a
    fetched page from an uploaded file.

    ``with_text`` pulls ``extracted_text`` too, for the one caller that matches
    against it (the anchoring gate re-run after a fetch).
    """
    try:
        from ..services.compile_guard import flag_fields
    except ImportError:
        from services.compile_guard import flag_fields

    init_db()
    db = get_db()
    current = _scan_version()
    # The full text is read only for rows whose stored verdict came from another
    # scan version (or when the caller wants the text): a 20-source project no
    # longer ships 20 documents from the database and rescans them on every
    # Sources refresh (audit 2026-09-24). Rescanned rows are stamped in place.
    rows = db.execute(
        """
        SELECT id, filename, page_count, file_size_bytes, included, created_at,
               CASE WHEN scan_version = ? AND ? = 0 THEN '' ELSE extracted_text END,
               parser_name, source_kind, parse_confidence,
               ocr_confidence, table_count, image_count, figure_count,
               asset_summary, omp_artifact_id,
               instruction_like, instruction_hits, scan_version
        FROM substrate_vault
        WHERE project_id = ?
        ORDER BY created_at DESC
        """,
        (current, 1 if with_text else 0, project_id),
    ).fetchall()
    entries: list[dict[str, Any]] = []
    restamped = False
    for row in rows:
        text = row[6] or ""
        if row[18] == current:
            try:
                stored_hits = json.loads(row[17] or "[]")
            except (TypeError, ValueError):
                stored_hits = []
            flag = {"instruction_like": bool(row[16]), "instruction_hits": list(stored_hits)}
        else:
            flag = flag_fields(text)
            db.execute(
                "UPDATE substrate_vault SET instruction_like = ?, instruction_hits = ?, scan_version = ? WHERE id = ?",
                (1 if flag["instruction_like"] else 0, json.dumps(list(flag["instruction_hits"])), current, row[0]),
            )
            restamped = True
        entry = {
            "id": row[0],
            "filename": row[1],
            "page_count": int(row[2] or 1),
            "file_size_bytes": int(row[3] or 0),
            "included": bool(row[4]),
            "created_at": row[5],
            "instruction_like": bool(flag["instruction_like"]),
            "instruction_hits": list(flag["instruction_hits"]),
            "fetched_url": fetched_url_of(row[1]),
            "parser_name": row[7],
            "source_kind": row[8],
            # Confidence columns are REAL-or-NULL; NULL is the honest unknown
            # and must reach the reader as None, not be coerced to 0.
            "parse_confidence": row[9],
            "ocr_confidence": row[10],
            "table_count": row[11],
            "image_count": row[12],
            "figure_count": row[13],
            "asset_summary": (json.loads(row[14]) if row[14] else {}) or {},
            "omp_artifact_id": row[15],
        }
        if with_text:
            entry["extracted_text"] = text
        entries.append(entry)
    if restamped:
        db.commit()
    return entries


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
    with db_scope() as db:
        init_db(db)
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
