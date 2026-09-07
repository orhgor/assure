"""Synchronous evidence run creation with Z3 lock verification."""

from __future__ import annotations

from typing import Any

try:
    from ..db.runs_repository import insert_run
    from ..db.substrate_repository import fetch_substrate_entries_by_ids
    from ..models.jdf import build_document_from_draft, document_to_dict
    from ..services.lock_metadata import enrich_extracted_locks
    from ..routers.draft import run_lock_inference, verify_locks
except ImportError:
    from db.runs_repository import insert_run
    from db.substrate_repository import fetch_substrate_entries_by_ids
    from models.jdf import build_document_from_draft, document_to_dict
    from services.lock_metadata import enrich_extracted_locks
    from routers.draft import run_lock_inference, verify_locks

SUBSTRATE_CHARS = 4000


def create_run_from_directive(
    directive: str,
    *,
    workspace_id: str | None = None,
    source_ids: list[str] | None = None,
    model: str = "gemini",
) -> dict[str, Any]:
    """Parse directive, verify locks when sources present, persist run."""
    text = (directive or "").strip()
    if not text:
        raise ValueError("directive is required")

    ws = workspace_id or "default"
    source_ids = [s for s in (source_ids or []) if s]
    sources_used: list[dict[str, Any]] = []
    substrate_blob = ""

    if source_ids:
        rows = fetch_substrate_entries_by_ids(ws, source_ids)
        for row in rows:
            fid = str(row.get("id") or "")
            name = str(row.get("filename") or row.get("name") or fid)
            excerpt = str(row.get("extracted_text") or row.get("content") or "")[:SUBSTRATE_CHARS]
            sources_used.append({"id": fid, "name": name, "excerpt_chars": len(excerpt)})
            substrate_blob += f"\n\n[{name}]\n{excerpt}"

    combined = text + substrate_blob
    locks: list[dict[str, Any]] = []
    status = "draft"

    if source_ids:
        locks, _lock_model = run_lock_inference(combined)
        locks = enrich_extracted_locks(locks, sources_used)
        z3 = verify_locks(locks, text)
        status = "contradiction" if z3.get("status") == "VIOLATION" else "stamped"
    else:
        status = "draft"

    truth_ledger: dict[str, float] = {}
    for lock in locks:
        key = str(lock.get("canonical_key") or lock.get("metric") or "").strip()
        if not key:
            continue
        try:
            truth_ledger[key] = float(lock.get("value"))
        except (TypeError, ValueError):
            continue

    doc = build_document_from_draft(ws, text, truth_ledger=truth_ledger)
    content = document_to_dict(doc)

    run = insert_run(
        directive=text,
        content=content,
        model=model,
        sources_used=sources_used,
        extracted_locks=locks,
        status=status,
        workspace_id=ws,
    )
    run["lock_count"] = len(locks)
    run["title"] = text[:72] + ("…" if len(text) > 72 else "")
    return run
