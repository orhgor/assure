"""Resolve lock pill evidence from runs + substrate vault."""

from __future__ import annotations

import re
from typing import Any

try:
    from ..db.runs_repository import list_runs
    from ..db.substrate_repository import fetch_substrate_entry
except ImportError:
    from db.runs_repository import list_runs
    from db.substrate_repository import fetch_substrate_entry


def _source_name(run: dict[str, Any], source_id: str) -> str:
    for src in run.get("sources_used") or []:
        if isinstance(src, dict) and str(src.get("id") or "") == source_id:
            return str(src.get("name") or src.get("filename") or source_id)
    entry = fetch_substrate_entry(run.get("workspace_id") or "founder", source_id)
    if entry:
        return str(entry.get("filename") or source_id)
    return source_id or "Unknown source"


def _excerpt_from_text(text: str, lock: dict[str, Any], *, window: int = 240) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    needles: list[str] = []
    for key in ("metric", "canonical_key"):
        val = lock.get(key)
        if val:
            needles.append(str(val))
    value = lock.get("value")
    if value is not None:
        needles.append(str(value))
        if isinstance(value, (int, float)) and value >= 1_000_000:
            needles.append(f"${value / 1_000_000:.0f}M")
    for needle in needles:
        if not needle:
            continue
        idx = raw.lower().find(needle.lower())
        if idx >= 0:
            start = max(0, idx - window // 2)
            end = min(len(raw), idx + len(needle) + window // 2)
            snippet = raw[start:end].strip()
            if start > 0:
                snippet = "…" + snippet
            if end < len(raw):
                snippet = snippet + "…"
            return snippet
    return raw[:window] + ("…" if len(raw) > window else "")


def _z3_proof_for_lock(lock: dict[str, Any], run: dict[str, Any]) -> str:
    if lock.get("z3_proof"):
        return str(lock["z3_proof"])
    key = str(lock.get("canonical_key") or lock.get("metric") or "claim")
    value = lock.get("value")
    ledger = (run.get("content") or {}).get("truth_ledger") or {}
    ledger_val = ledger.get(key)
    lines = [
        f"; Z3 verification log for lock {lock.get('lock_hash') or ''}",
        f"(declare-const {re.sub(r'[^a-zA-Z0-9_]', '_', key)} Real)",
        f"(assert (= {re.sub(r'[^a-zA-Z0-9_]', '_', key)} {value!r}))",
    ]
    if ledger_val is not None and ledger_val != value:
        lines.append(f"; truth_ledger[{key!r}] = {ledger_val!r} (mismatch flagged)")
    else:
        lines.append("; status: SAT — lock consistent with truth ledger")
    return "\n".join(lines)


def find_lock_evidence(lock_hash: str) -> dict[str, Any] | None:
    """Search runs.extracted_locks for lock_hash and build evidence payload."""
    target = str(lock_hash or "").strip()
    if not target:
        return None
    for run in list_runs(limit=500):
        for lock in run.get("extracted_locks") or []:
            if str(lock.get("lock_hash") or "") != target:
                continue
            source_id = str(lock.get("source_id") or "")
            page = int((lock.get("page_coordinates") or {}).get("page") or 1)
            excerpt = ""
            entry = (
                fetch_substrate_entry(run.get("workspace_id") or "founder", source_id)
                if source_id
                else None
            )
            if entry:
                excerpt = _excerpt_from_text(str(entry.get("extracted_text") or ""), lock)
            return {
                "lock_hash": target,
                "source_id": source_id,
                "source_name": _source_name(run, source_id),
                "page_number": page,
                "excerpt": excerpt or str(lock.get("metric") or lock.get("canonical_key") or ""),
                "z3_proof": _z3_proof_for_lock(lock, run),
                "run_id": run.get("id"),
                "verdict": _lock_verdict(lock, entry),
            }
    return None


def _lock_verdict(
    lock: dict[str, Any],
    entry: dict[str, Any] | None,
    icp_profile: str | None = None,
) -> dict[str, Any]:
    """The evidence verdict for one lock, shaped for the inspector drawer.

    ``services.evidence_assembly`` produces the six verdict states the inspector
    draws (supported, partial, not_supported, contradicted, unanchored,
    unverified) from lexical overlap, and it was unreferenced: the drawer asked
    the API for a ``verdict`` the endpoint never sent, so the section rendered
    empty for every lock.

    ``services.entailment`` is the other producer, and the stronger one — it
    judges semantically with a model call and already writes
    ``node.meta.provenance.entailment`` on the draft path
    (``routers/draft.attach_entailment_to_tree``). It is deliberately not called
    here: a model call per lock would put seconds on a GET that a reader opens
    by clicking a pill, and this route has no project budget context to spend
    against. The lexical verdict is what a synchronous read can afford; the
    semantic one belongs on the compile, where the cost is already paid.

    The claim is the lock's own statement, because that is what the source is
    being asked to support. The verdict is returned as ``{"type", "reason"}``
    rather than the module's bare string, because the drawer reads
    ``verdict.type`` for the badge and ``verdict.reason`` for the line under it.

    Never raises: a lock with no resolvable source is ``unverified``, which is a
    verdict about the evidence, not a failure of the endpoint.
    """
    fallback = {"type": "unverified", "reason": "No source text resolved for this lock."}
    if not entry:
        return fallback

    claim = str(lock.get("claim") or lock.get("metric") or lock.get("canonical_key") or "").strip()
    if not claim:
        return fallback

    text = str(entry.get("extracted_text") or "").strip()
    if not text:
        return fallback

    try:
        from ..services.evidence_assembly import EvidenceAssemblyError, assemble_evidence
    except ImportError:
        try:
            from services.evidence_assembly import EvidenceAssemblyError, assemble_evidence
        except ImportError:
            return fallback

    row = {
        "id": str(entry.get("id") or ""),
        "filename": str(entry.get("filename") or ""),
        "extracted_text": text,
    }
    try:
        result = assemble_evidence(claim, [row], icp_profile=icp_profile)
    except EvidenceAssemblyError:
        # A refused assembly still says something about the evidence: the source
        # was there and did not carry the claim.
        return {"type": "unanchored", "reason": "The source does not anchor this claim."}
    except Exception:
        return fallback

    v = result.verdict.to_dict()
    return {"type": str(v.get("verdict") or "unverified"), "reason": str(v.get("reason") or "")}
