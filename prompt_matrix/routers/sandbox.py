"""Public paste-test sandbox — no auth, no document persistence."""

from __future__ import annotations

import json
from typing import Any

from flask import jsonify, request
from pydantic import BaseModel, Field

try:
    from ..cost_governance import CostGovernor, TaskType
    from ..lib.http_errors import clean_error_message, error_status
    from ..models.jdf import (
        apply_redhat_critiques_to_tree,
        apply_z3_violations_to_tree,
        build_document_from_draft,
        document_to_dict,
        flatten_nodes,
        parse_document,
    )
    from ..routers.draft import run_lock_inference, run_redhat_audit, verify_locks
    from ..services.audit_summary import build_audit_summary
except ImportError:
    from cost_governance import CostGovernor, TaskType
    from lib.http_errors import clean_error_message, error_status
    from models.jdf import (
        apply_redhat_critiques_to_tree,
        apply_z3_violations_to_tree,
        build_document_from_draft,
        document_to_dict,
        flatten_nodes,
        parse_document,
    )
    from routers.draft import run_lock_inference, run_redhat_audit, verify_locks
    from services.audit_summary import build_audit_summary

SANDBOX_PROJECT_ID = "sandbox"


def ensure_sandbox_project() -> None:
    """Seed the sandbox's ``projects`` row.

    ``project_budgets.project_id`` carries ``REFERENCES projects(id)`` (the
    migrated stores have it, and ``history.get_db`` turns ``PRAGMA
    foreign_keys`` on), and the sandbox is a fixed identifier with no creation
    path of its own — nothing ever creates a project for it. So the first
    ``POST /api/sandbox/verify`` failed at ``run_sandbox_verify``'s first act,
    ``BudgetStore.ensure_project``, with ``FOREIGN KEY constraint failed`` and an
    HTTP 500, before any model call. Measured on staging 2026-09-19: ``{"error":
    "FOREIGN KEY constraint failed", "ok": false}``.

    The row is created at boot rather than in the request path so the fixed
    identifier stays in one place and the failure cannot come back with the next
    cold start.
    """
    try:
        from ..db.jdf_repository import ensure_project
    except ImportError:
        from db.jdf_repository import ensure_project
    ensure_project(SANDBOX_PROJECT_ID, "Sandbox")
    # The founder workbench hardcodes workspace "founder" (static/founder_mode.js,
    # founder_draft.js; routers/drafts_routes.py, redhat_routes.py default to it)
    # and `drafts`/`runs` reference projects(id): without this row every founder
    # autosave and Red-Hat trigger failed at the foreign key with an HTTP 500.
    ensure_project("founder", "Founder Workspace")


class SandboxVerifyPayload(BaseModel):
    text: str = Field(default="")


def run_sandbox_verify(
    text: str,
    *,
    governor: CostGovernor | None = None,
) -> dict[str, Any]:
    """Compile pasted text through lock inference, Z3, and Red-Hat — no JDF persistence."""
    gov = governor or CostGovernor()
    gov.budget_store.ensure_project(SANDBOX_PROJECT_ID)

    full_text = (text or "").strip()
    locks, lock_model = run_lock_inference(full_text)

    ledger: dict[str, float] = {}
    for lock in locks:
        key = str(lock.get("canonical_key") or lock.get("metric") or "").strip()
        if key:
            try:
                ledger[key] = float(lock.get("value"))
            except (TypeError, ValueError):
                pass

    lock_in = gov.accountant.count(full_text[:12000])
    lock_out = gov.accountant.count(json.dumps({"candidates": locks}))
    gov.record_usage(
        SANDBOX_PROJECT_ID,
        input_tokens=lock_in,
        output_tokens=lock_out,
        model_id=lock_model,
        task_type=TaskType.SUMMARIZE_NODE,
        meta={"pipeline": "sandbox_lock_inference"},
    )

    document = build_document_from_draft(SANDBOX_PROJECT_ID, full_text, truth_ledger=ledger)
    doc_dict = document_to_dict(document)

    z3_results = verify_locks(locks, full_text)
    redhat_critiques, red_usage = run_redhat_audit(SANDBOX_PROJECT_ID, full_text, gov=gov)

    if red_usage.get("model_id"):
        gov.record_usage(
            SANDBOX_PROJECT_ID,
            input_tokens=int(red_usage.get("input_tokens") or 0),
            output_tokens=int(red_usage.get("output_tokens") or 0),
            model_id=str(red_usage["model_id"]),
            task_type=TaskType.REDHAT,
            meta={"pipeline": "sandbox_redhat_audit"},
        )

    annotated = doc_dict
    if z3_results.get("violations"):
        annotated = apply_z3_violations_to_tree(annotated, z3_results["violations"])
    if redhat_critiques:
        annotated = apply_redhat_critiques_to_tree(annotated, redhat_critiques)
    parse_document(annotated)

    nodes = flatten_nodes(annotated)

    return build_audit_summary(
        z3_results=z3_results,
        redhat_critiques=redhat_critiques,
        nodes=nodes,
        locks=locks,
        document=annotated,
        node_count=len(nodes),
        lock_count=len(locks),
    )


def register_sandbox_routes(app) -> None:
    @app.post("/api/sandbox/verify")
    def sandbox_verify():
        data = request.get_json(silent=True) or {}
        try:
            payload = SandboxVerifyPayload.model_validate(data)
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

        text = (payload.text or "").strip()
        if not text:
            return jsonify({"ok": False, "error": "text is required"}), 400

        try:
            result = run_sandbox_verify(text)
        except Exception as exc:
            # A missing provider key is a deployment condition, not a server
            # fault: 503 with one clean line (the message used to be the raw
            # ``str(exc)`` — a litellm traceback fragment — behind a 500).
            return jsonify({"ok": False, "error": clean_error_message(exc)}), error_status(exc)

        return jsonify(result)
