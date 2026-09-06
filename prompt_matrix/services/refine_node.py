"""Surgical refine: rewrite one JDF node with N-1/N+1 context, then Z3 that node."""

from __future__ import annotations

import copy
from typing import Any

try:
    from ..compiler.aperture import build_aperture_context
    from ..cost_governance import CostGovernor, TaskType
    from ..db.project_files import save_last_compiled
    from ..lib.sanitize import sanitize_jdf_node
    from ..models.jdf import (
        document_to_dict,
        empty_annotations,
        get_node_by_id,
        splice_node,
    )
    from ..routers.draft import verify_locks
    from ..routers.inquire_stream import _build_messages, resolve_active_document
    from ..services.confidence_spans import (
        attach_confidence_spans_to_document,
        build_confidence_spans,
    )
except ImportError:
    from compiler.aperture import build_aperture_context
    from cost_governance import CostGovernor, TaskType
    from db.project_files import save_last_compiled
    from lib.sanitize import sanitize_jdf_node
    from models.jdf import document_to_dict, empty_annotations, get_node_by_id, splice_node
    from routers.draft import verify_locks
    from routers.inquire_stream import _build_messages, resolve_active_document
    from services.confidence_spans import (
        attach_confidence_spans_to_document,
        build_confidence_spans,
    )

VAULT_INSTRUCTION = (
    "Rewrite this node so every claim is grounded in the Substrate Vault excerpts. "
    "Do not invent numbers or sources. Keep neighboring flow."
)


def neighbor_context_from_tree(document: dict[str, Any], node_id: str) -> dict[str, Any]:
    aperture = build_aperture_context(document, node_id)
    return {
        "preceding": aperture.get("preceding"),
        "succeeding": aperture.get("succeeding"),
        "target": aperture.get("target"),
        "prompt_harness": aperture.get("prompt_harness"),
    }


def _locks_from_ledger(ledger: dict[str, Any] | None) -> list[dict[str, Any]]:
    locks: list[dict[str, Any]] = []
    for key, val in (ledger or {}).items():
        if not key:
            continue
        locks.append({"canonical_key": str(key), "value": val})
    return locks


def _apply_text_to_node(original: dict[str, Any], text: str) -> dict[str, Any]:
    node = copy.deepcopy(original)
    ntype = str(node.get("type") or "paragraph")
    if ntype == "section":
        node["title"] = text
    elif ntype == "callout":
        node["content"] = text
    else:
        node["content"] = text
    anns = dict(node.get("annotations") or empty_annotations())
    anns["z3"] = []
    node["annotations"] = anns
    return node


def replace_node_in_tree(
    document: dict[str, Any], node_id: str, new_node: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    mutated, found = splice_node(document, node_id, new_node)
    if found:
        return mutated, True
    body = list(mutated.get("body") or [])
    for i, section in enumerate(body):
        if isinstance(section, dict) and str(section.get("id") or "") == node_id:
            merged = copy.deepcopy(section)
            if new_node.get("title"):
                merged["title"] = new_node["title"]
            elif new_node.get("content"):
                merged["title"] = new_node["content"]
            if new_node.get("annotations"):
                merged["annotations"] = new_node["annotations"]
            body[i] = merged
            mutated["body"] = body
            return mutated, True
    return mutated, False


def _vault_excerpts(project_id: str, file_ids: list[str] | None) -> str:
    try:
        from ..db.substrate_repository import (
            fetch_substrate_entries_by_ids,
            list_substrate_for_project,
        )
    except ImportError:
        from db.substrate_repository import (
            fetch_substrate_entries_by_ids,
            list_substrate_for_project,
        )

    try:
        ids = [str(x) for x in (file_ids or []) if x]
        if not ids:
            ids = [
                row["id"] for row in list_substrate_for_project(project_id) if row.get("included")
            ]
        rows = fetch_substrate_entries_by_ids(project_id, ids[:8])
        chunks: list[str] = []
        budget = 12000
        used = 0
        for row in rows:
            text = str(row.get("extracted_text") or "").strip()
            if not text:
                continue
            slice_text = text[:4000]
            name = str(row.get("filename") or row.get("id") or "vault")
            block = f"### {name}\n{slice_text}"
            if used + len(block) > budget:
                break
            chunks.append(block)
            used += len(block)
        return "\n\n".join(chunks)
    except Exception:
        return ""


def apply_refined_text(
    document: dict[str, Any],
    node_id: str,
    text: str,
) -> dict[str, Any]:
    """Splice rewritten text, run Z3 on that node, attach confidence spans."""
    doc = copy.deepcopy(document)
    original = get_node_by_id(doc, node_id)
    if not original:
        raise KeyError(f"target node not found: {node_id}")
    updated = _apply_text_to_node(original, text)
    doc, found = replace_node_in_tree(doc, node_id, updated)
    if not found:
        raise KeyError(f"target node not found: {node_id}")
    z3 = verify_locks(_locks_from_ledger(doc.get("truth_ledger")), text)
    if str(z3.get("status") or "").upper() == "VIOLATION":
        try:
            from ..models.jdf import apply_z3_violations_to_tree
        except ImportError:
            from models.jdf import apply_z3_violations_to_tree
        doc = apply_z3_violations_to_tree(doc, z3.get("violations") or [])
        updated = get_node_by_id(doc, node_id) or updated
    spans = build_confidence_spans(doc, z3_results=z3)
    doc = attach_confidence_spans_to_document(doc, spans)
    node_spans = [s for s in spans if str(s.get("nodeId") or "") == str(node_id)]
    return {
        "document": doc,
        "node": updated,
        "z3_results": z3,
        "confidenceSpans": spans,
        "nodeSpans": node_spans,
    }


def run_refine_node(
    project_id: str,
    *,
    node_id: str,
    user_instruction: str,
    document: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    ground_from_vault: bool = False,
    substrate_file_ids: list[str] | None = None,
    gov: CostGovernor | None = None,
    persist: bool = True,
    expected_version: int | None = None,
) -> dict[str, Any]:
    tree = resolve_active_document(project_id, document)
    doc = document_to_dict(tree)
    original = get_node_by_id(doc, node_id)
    if not original:
        raise KeyError(f"target node not found: {node_id}")

    aperture = build_aperture_context(doc, node_id)
    if context:
        if context.get("preceding") and not aperture.get("preceding"):
            aperture["preceding"] = context["preceding"]
        if context.get("succeeding") and not aperture.get("succeeding"):
            aperture["succeeding"] = context["succeeding"]

    instruction = (user_instruction or "").strip()
    if ground_from_vault and not instruction:
        instruction = VAULT_INSTRUCTION
    if not instruction:
        raise ValueError("user_instruction is required")

    if ground_from_vault:
        excerpts = _vault_excerpts(project_id, substrate_file_ids)
        if excerpts:
            instruction = f"{instruction}\n\n# Substrate Vault excerpts\n{excerpts}"

    messages = _build_messages(instruction, aperture)
    governor = gov or CostGovernor()
    governor.preflight(project_id, TaskType.SURGICAL_EDIT, messages)
    node_type = str(original.get("type") or "paragraph")
    text = ""
    try:
        from ..llm.orchestrator import orchestrate_node_compilation_sync
    except ImportError:
        from llm.orchestrator import orchestrate_node_compilation_sync
    try:
        text = orchestrate_node_compilation_sync(node_type, messages).strip()
    except Exception:
        text = ""
    if not text:
        result = governor.execute_with_retry_budget(
            project_id,
            TaskType.SURGICAL_EDIT,
            messages,
            defer_budget_record=True,
        )
        text = (result.text or "").strip()
    if not text or text.startswith("ERROR:"):
        raise RuntimeError(text or "Refine failed.")

    applied = apply_refined_text(doc, node_id, text)
    applied["document"] = sanitize_jdf_node(applied["document"])
    applied["node"] = sanitize_jdf_node(applied.get("node") or {})
    if persist:
        save_last_compiled(project_id, applied["document"])
        try:
            from ..db.jdf_repository import save_jdf_revision
        except ImportError:
            from db.jdf_repository import save_jdf_revision
        save_jdf_revision(
            project_id,
            applied["document"],
            mutation_type="surgical_refine",
            target_node_id=node_id,
            change_summary="Surgical refine",
            expected_version=expected_version,
        )
    applied["ok"] = True
    applied["ground_from_vault"] = bool(ground_from_vault)
    applied["context"] = {
        "preceding": aperture.get("preceding"),
        "succeeding": aperture.get("succeeding"),
    }
    try:
        from ..services.omp_memory import remember_refine_diff
    except ImportError:
        from services.omp_memory import remember_refine_diff
    try:
        remember_refine_diff(
            project_id,
            node_id,
            before=str(original.get("content") or original.get("title") or ""),
            after=text,
        )
    except Exception:
        pass
    return applied
