"""JDF sidecar — the document plus the verification that travelled with it.

The export used to be PDF-only, which is a one-way door: the prose leaves, the
per-node provenance, the entailment verdicts, the sources and the version chain
stay in SQLite. "Nothing locks you in" is only true if the JDF a reader takes away
carries what Assure decided about the document, so this module assembles the file
that does.

It reads the persisted tree and never rewrites it: ``meta.provenance`` per node is
already written by the compile pipeline (``services/audit_summary`` ->
``services/provenance_meta``) before the revision is saved, so the sidecar emits
the stored tree verbatim and adds an index over it — one entry per node, with the
anchor that node matched and the state the pipeline reached for it (``supported``,
``partial``, ``unsupported``, ``unverified``, ``anchored``, ``unanchored``,
``not_a_claim``). No field is renamed, added or restructured in the stored tree.

What a reader needs beyond the tree, and could not get from the PDF:

* ``source_manifest`` — the vault entries this document was grounded in, with the
  identity an anchor points at (``source_id``) and a hash of the text the anchor
  was matched against, so evidence can be checked rather than trusted;
* ``version_chain`` — every revision, oldest first, each with the hash of its own
  tree, so the sidecar's document is provably one link of that chain;
* ``drafting_model`` — the model that wrote the draft, resolved from what the
  compile persisted (``projects.last_compiled_json.gate.measure`` or the
  ``DRAFT_STREAM`` audit row), or an explicit null when neither recorded one.

``GET /api/projects/<id>/export?format=jdf`` serves this file; ``POST
/api/projects/<id>/import-jdf`` loads it into a fresh project and answers with
``round_trip``, the same audit ``audit_jdf_payload`` computes here — anchors
resolved against the manifest the JDF carries, and the verification-state counts
the document came with.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

try:
    from ..db.document_lock_repository import hash_jdf_tree
    from ..db.jdf_repository import list_jdf_revisions
    from ..db.substrate_repository import list_substrate_for_project
    from ..models.jdf import _MIN_CLAIM_TOKENS, _tokenize
    from .audit_bundle import compute_export_gate
except ImportError:
    from db.document_lock_repository import hash_jdf_tree
    from db.jdf_repository import list_jdf_revisions
    from db.substrate_repository import list_substrate_for_project
    from models.jdf import _MIN_CLAIM_TOKENS, _tokenize
    from audit_bundle import compute_export_gate

SIDECAR_FORMAT = "assure-jdf-sidecar"
SIDECAR_VERSION = 1

#: Revisions hashed into the chain. A project can carry thousands; the export is
#: read by a human, and the newest 500 is the span anyone reads. Older links are
#: reported as ``truncated`` rather than silently dropped.
_MAX_CHAIN_LINKS = 500

#: States a node can be in. ``supported`` is the only one that means verified.
_STATE_SUPPORTED = "supported"
_STATE_PARTIAL = "partial"
_STATE_UNSUPPORTED = "unsupported"
_STATE_UNVERIFIED = "unverified"
_STATE_ANCHORED = "anchored"
_STATE_UNANCHORED = "unanchored"
_STATE_NOT_A_CLAIM = "not_a_claim"

_VERDICT_STATE = {
    "yes": _STATE_SUPPORTED,
    "partial": _STATE_PARTIAL,
    "no": _STATE_UNSUPPORTED,
    "unverified": _STATE_UNVERIFIED,
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _walk_nodes(tree: dict[str, Any]):
    """Sections and their children, document order — the shape the index mirrors."""
    for section in tree.get("body") or []:
        if not isinstance(section, dict):
            continue
        yield section
        for child in section.get("children") or []:
            if isinstance(child, dict):
                yield child


def _anchor_row(node: dict[str, Any]) -> dict[str, Any] | None:
    """The provenance row an anchor rests on — the one carrying the quote."""
    rows = node.get("provenance") or []
    for row in rows:
        if isinstance(row, dict) and str(row.get("extracted_quote") or "").strip():
            return row
    for row in rows:
        if isinstance(row, dict) and (row.get("source_id") or row.get("source_name")):
            return row
    return None


def _entailment(node: dict[str, Any]) -> dict[str, Any] | None:
    meta = node.get("meta") or {}
    prov = meta.get("provenance") if isinstance(meta, dict) else None
    record = prov.get("entailment") if isinstance(prov, dict) else None
    if not isinstance(record, dict) or not record.get("verdict"):
        return None
    return {
        "verdict": str(record.get("verdict") or ""),
        "reasoning": str(record.get("reasoning") or ""),
        "model": str(record.get("model") or ""),
        "checked_at": str(record.get("checked_at") or ""),
    }


def _label(node: dict[str, Any], limit: int = 90) -> str:
    raw = str(node.get("title") or node.get("content") or node.get("caption") or "").strip()
    return raw[:limit]


def _verification_state(node: dict[str, Any], *, eligible: bool) -> str:
    if str(node.get("type") or "") != "paragraph" or not eligible:
        return _STATE_NOT_A_CLAIM
    verdict = (_entailment(node) or {}).get("verdict") or ""
    if verdict in _VERDICT_STATE:
        return _VERDICT_STATE[verdict]
    return _STATE_ANCHORED if _anchor_row(node) else _STATE_UNANCHORED


def node_verification_index(tree: dict[str, Any]) -> list[dict[str, Any]]:
    """One entry per node: its anchor and the state the pipeline reached for it."""
    index: list[dict[str, Any]] = []
    for node in _walk_nodes(tree):
        meta = node.get("meta") if isinstance(node.get("meta"), dict) else {}
        prov = meta.get("provenance") if isinstance(meta.get("provenance"), dict) else {}
        row = _anchor_row(node)
        eligible = (
            str(node.get("type") or "") == "paragraph"
            and len(_tokenize(str(node.get("content") or ""))) >= _MIN_CLAIM_TOKENS
        )
        anchor = None
        if row is not None:
            anchor = {
                "source_id": str(row.get("source_id") or ""),
                "source_name": str(row.get("source_name") or ""),
                "page_number": row.get("page_number"),
                "quote": str(row.get("extracted_quote") or ""),
                "window": str(row.get("anchor_window") or ""),
                "window_span": str(row.get("anchor_window_span") or ""),
            }
        z3 = [
            {
                "canonical_key": str(item.get("canonical_key") or ""),
                "status": str(item.get("status") or ""),
                "message": str(item.get("message") or ""),
            }
            for item in ((node.get("annotations") or {}).get("z3") or [])
            if isinstance(item, dict)
        ]
        index.append(
            {
                "node_id": str(node.get("id") or ""),
                "type": str(node.get("type") or ""),
                "label": _label(node),
                "claim_eligible": eligible,
                "verification_state": _verification_state(node, eligible=eligible),
                "anchor": anchor,
                "entailment": _entailment(node),
                "z3": z3,
                "confidence": prov.get("confidence"),
                "rule": prov.get("rule") or None,
                "verified_at": prov.get("verified_at") or None,
            }
        )
    return index


def state_counts(index: list[dict[str, Any]]) -> dict[str, int]:
    """Verification states across the index — zero-filled, so an absent state reads 0."""
    counts = {
        _STATE_SUPPORTED: 0,
        _STATE_PARTIAL: 0,
        _STATE_UNSUPPORTED: 0,
        _STATE_UNVERIFIED: 0,
        _STATE_ANCHORED: 0,
        _STATE_UNANCHORED: 0,
        _STATE_NOT_A_CLAIM: 0,
    }
    for entry in index:
        state = str(entry.get("verification_state") or "")
        if state in counts:
            counts[state] += 1
    return counts


def build_source_manifest(project_id: str) -> list[dict[str, Any]]:
    """The vault entries the document was grounded in, hashed for checking.

    ``text_sha256`` is over the extracted text the anchor was matched against: a
    reader holding the same file can hash it and confirm the evidence the claim
    cites is the evidence it was checked against.
    """
    manifest: list[dict[str, Any]] = []
    try:
        rows = list_substrate_for_project(project_id, with_text=True)
    except TypeError:  # pragma: no cover - older repository signature
        rows = list_substrate_for_project(project_id)
    for row in rows:
        text = str(row.get("extracted_text") or "")
        if not text and not row.get("text_sha256"):
            text = ""
        manifest.append(
            {
                "source_id": str(row.get("id") or ""),
                "filename": str(row.get("filename") or ""),
                "page_count": int(row.get("page_count") or 1),
                "chars": len(text),
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest() if text else "",
                "included": bool(row.get("included")),
                "instruction_like": bool(row.get("instruction_like")),
                "created_at": row.get("created_at"),
            }
        )
    return manifest


def build_version_chain(project_id: str) -> dict[str, Any]:
    """Every revision, oldest first, each with the hash of the tree it holds."""
    revisions = list_jdf_revisions(project_id, limit=_MAX_CHAIN_LINKS + 1)
    truncated = len(revisions) > _MAX_CHAIN_LINKS
    links: list[dict[str, Any]] = []
    for row in revisions[:_MAX_CHAIN_LINKS]:
        version = int(row.get("version") or 0)
        links.append(
            {
                "version": version,
                "mutation_type": str(row.get("mutation_type") or ""),
                "change_summary": str(row.get("change_summary") or ""),
                "target_node_id": row.get("target_node_id"),
                "created_at": row.get("created_at"),
                "document_sha256": _revision_hash(project_id, version),
            }
        )
    links.reverse()
    return {
        "ordered": "oldest_first",
        "revision_count": len(revisions),
        "truncated": truncated,
        "revisions": links,
    }


def _revision_hash(project_id: str, version: int) -> str | None:
    try:
        from ..db.jdf_repository import fetch_jdf_at_version
    except ImportError:
        from db.jdf_repository import fetch_jdf_at_version
    try:
        tree = fetch_jdf_at_version(project_id, version)
    except Exception:
        return None
    return hash_jdf_tree(tree) if isinstance(tree, dict) and tree else None


def resolve_drafting_model(project_id: str) -> dict[str, Any]:
    """The model that drafted the current revision, and where that came from.

    Neither source is invented: the compile persists ``measure.model`` with the
    gate when it saves, and the audit row carries the model it actually called.
    When a document predates both, the answer is null with the reason — the same
    honest gap the shell's ROUTED TO panel names rather than guessing.
    """
    measure = _gate_measure(project_id)
    for key in ("model", "model_id"):
        value = str((measure or {}).get(key) or "").strip()
        if value:
            return {"model": value, "resolved_from": "projects.last_compiled_json.gate.measure", "measure": measure}
    from_audit = _audit_stream_model(project_id)
    if from_audit:
        return {"model": from_audit, "resolved_from": "audit_log.DRAFT_STREAM", "measure": measure}
    return {
        "model": None,
        "resolved_from": "",
        "measure": measure,
        "reason": "No compile recorded a model for this document.",
    }


def _gate_measure(project_id: str) -> dict[str, Any] | None:
    try:
        from ..db.connection import init_db
        from ..history import get_db
    except ImportError:
        from db.connection import init_db
        from history import get_db
    try:
        init_db()
        row = get_db().execute(
            "SELECT last_compiled_json FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        data = json.loads(row[0]) if (row and row[0]) else {}
        measure = ((data or {}).get("gate") or {}).get("measure")
        return measure if isinstance(measure, dict) else None
    except Exception:
        return None


def _audit_stream_model(project_id: str) -> str:
    try:
        from ..db.connection import init_db
        from ..history import get_db
    except ImportError:
        from db.connection import init_db
        from history import get_db
    try:
        init_db()
        rows = get_db().execute(
            "SELECT details FROM audit_log WHERE project_id = ? AND action = 'DRAFT_STREAM' "
            "AND success = 1 ORDER BY created_at DESC LIMIT 20",
            (project_id,),
        ).fetchall()
        for row in rows:
            try:
                details = json.loads(row[0] or "{}")
            except (TypeError, ValueError):
                continue
            model = str((details or {}).get("model") or "").strip()
            if model:
                return model
    except Exception:
        return ""
    return ""


def build_jdf_sidecar(project_id: str, tree: dict[str, Any]) -> dict[str, Any]:
    """The .jdf export: the persisted tree plus everything a reader needs to verify it."""
    index = node_verification_index(tree)
    meta = tree.get("meta") if isinstance(tree.get("meta"), dict) else {}
    gate = compute_export_gate(project_id, tree)
    chain = build_version_chain(project_id)
    document_hash = hash_jdf_tree(tree)
    latest = chain["revisions"][-1]["document_sha256"] if chain["revisions"] else None
    return {
        "format": SIDECAR_FORMAT,
        "sidecar_version": SIDECAR_VERSION,
        "exported_at": _now(),
        "project_id": project_id,
        "title": str(meta.get("title") or meta.get("project_id") or project_id),
        "document_id": str(tree.get("document_id") or ""),
        "document_sha256": document_hash,
        "chain_head_sha256": latest,
        "document": tree,
        "verification": {
            "gate_status": gate.get("gate_status"),
            "z3_status": gate.get("z3_status"),
            "unverified": gate.get("unverified"),
            "unverified_reason": gate.get("unverified_reason"),
            "provenance_stats": dict(gate.get("provenance_stats") or {}),
            "states": state_counts(index),
        },
        "nodes": index,
        "source_manifest": build_source_manifest(project_id),
        "version_chain": chain,
        "drafting_model": resolve_drafting_model(project_id),
    }


def sidecar_document(payload: dict[str, Any]) -> dict[str, Any]:
    """The document inside a sidecar, or the payload itself when it is a bare tree."""
    if not isinstance(payload, dict):
        return {}
    document = payload.get("document")
    if isinstance(document, dict) and document.get("body") is not None:
        return document
    return payload


def audit_jdf_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Round-trip audit: can every anchor in this document be resolved?

    Resolution is against the manifest **the JDF carries** — a fresh project has
    no vault rows, and the file is the only thing the reader took away — so an
    anchor resolves when its ``source_id`` names a manifest entry, and the
    manifest entry carries the hash of the text the anchor was matched against.
    A document whose anchors do not resolve has lost its evidence, and this is
    where that is said out loud instead of being read as a clean import.
    """
    document = sidecar_document(payload)
    manifest = payload.get("source_manifest") if isinstance(payload, dict) else None
    if not isinstance(manifest, list):
        manifest = []
    known = {str(row.get("source_id") or "") for row in manifest if isinstance(row, dict)}
    index = node_verification_index(document)
    anchors = [entry for entry in index if entry.get("anchor")]
    unresolved = [
        {"node_id": entry["node_id"], "source_id": (entry["anchor"] or {}).get("source_id", "")}
        for entry in anchors
        if str((entry["anchor"] or {}).get("source_id") or "") not in known
    ]
    verdicts: dict[str, int] = {}
    for entry in index:
        verdict = str((entry.get("entailment") or {}).get("verdict") or "")
        if verdict:
            verdicts[verdict] = verdicts.get(verdict, 0) + 1
    declared = payload.get("document_sha256") if isinstance(payload, dict) else None
    actual = hash_jdf_tree(document) if document else ""
    return {
        "format_ok": (payload.get("format") == SIDECAR_FORMAT) if isinstance(payload, dict) else False,
        "declared_document_sha256": declared,
        "document_sha256": actual,
        "document_sha256_matches": bool(declared) and declared == actual,
        "manifest_entries": len(manifest),
        "manifest_source_ids": sorted(known),
        "nodes_total": len(index),
        "claims_eligible": sum(1 for entry in index if entry.get("claim_eligible")),
        "anchors_total": len(anchors),
        "anchors_resolved": len(anchors) - len(unresolved),
        "anchors_unresolved": unresolved,
        "verification_states": state_counts(index),
        "entailment_verdicts": verdicts,
    }
