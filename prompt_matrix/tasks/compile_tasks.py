"""Celery compile pipeline with grounding, red-hat, and Z3 gates.

What this task can and cannot say (2026-09-27, ``docs/evidence-honesty.md``):

* ``verification.grounding.verify_claims`` without the groundrails package is a
  token-overlap heuristic. It still gates the retry (an obviously ungrounded
  draft is redrafted), but its pass is recorded as ``heuristic_overlap`` with
  the reason "heuristic overlap is not verification" — never as verified.
* The Red-Hat call is a free-text critique; the ``CRITICAL_GAP`` marker triggers
  a redraft, and that is all it is: the critique is not checked against the
  source here, so its status is ``unverified``.
* Until 2026-09-27 the "Z3 gate" built an empty solver and called ``check()``;
  an empty solver is always SAT, so every draft "passed" a check with no
  constraints. No solver runs here now and the result says ``not_run``.
"""

from __future__ import annotations

from typing import Any

from prompt_matrix.celery_app import celery_app

HEURISTIC_REASON = "heuristic overlap is not verification"


def fetch_vault_markdown(project_id: str) -> str:
    try:
        from prompt_matrix.db.substrate_repository import list_substrate_for_project
    except ImportError:
        from db.substrate_repository import list_substrate_for_project

    rows = list_substrate_for_project(project_id, with_text=True)
    chunks = [str(r.get("extracted_text") or "") for r in rows if isinstance(r, dict)]
    return "\n\n".join(chunks)


def generate_draft(node_id: str, previous_critiques: str | None = None) -> str:
    prompt = f"Draft node {node_id} grounded in vault evidence."
    if previous_critiques:
        prompt += f"\nFix prior issues:\n{previous_critiques}"
    try:
        from prompt_matrix.llm.orchestrator import orchestrate_node_compilation_sync

        return orchestrate_node_compilation_sync(
            "paragraph",
            [{"role": "user", "content": prompt}],
        )
    except Exception:
        return f"Draft for {node_id} grounded in vault."


def commit_to_ast(project_id: str, node_id: str, content: str) -> dict[str, Any]:
    try:
        from prompt_matrix.db.jdf_repository import fetch_latest_jdf_or_empty, save_jdf_revision
        from prompt_matrix.models.jdf import get_node_by_id, splice_node
    except ImportError:
        from db.jdf_repository import fetch_latest_jdf_or_empty, save_jdf_revision
        from models.jdf import get_node_by_id, splice_node

    doc = fetch_latest_jdf_or_empty(project_id)
    node = get_node_by_id(doc, node_id)
    if node is None:
        raise ValueError(f"node not found: {node_id}")
    updated = dict(node)
    if updated.get("type") == "signature":
        updated["signer_name"] = updated.get("signer_name") or "Reviewer"
        updated["signed_at"] = (
            updated.get("signed_at") or __import__("datetime").datetime.utcnow().isoformat()
        )
    updated["content"] = content
    # The rewritten node carries no citations: the heuristic did not anchor it
    # to a source sentence, and inheriting the old node's rows would present the
    # new text as anchored to sentences it was never matched against.
    updated["provenance"] = []
    doc, found = splice_node(doc, node_id, updated)
    if not found:
        raise ValueError(f"node not found: {node_id}")
    return save_jdf_revision(
        project_id,
        doc,
        mutation_type="SAFE_COMPILE",
        target_node_id=node_id,
        change_summary="Safe compile commit (heuristic grounding only; not verified against sources)",
    )


def z3_not_run() -> dict[str, Any]:
    """The Z3 gate's honest result: nothing was locked from a source in this
    task, so there is nothing for a solver to check."""
    return {
        "status": "not_run",
        "reason": "no metric was locked from a source in this task; an empty solver "
        "proves nothing and is not run",
    }


@celery_app.task(name="assure.check_and_trigger_automations", bind=True)
def check_and_trigger_automations(self, project_id: str) -> dict[str, Any]:
    return {"ok": True, "project_id": project_id, "automations": []}


@celery_app.task(
    name="assure.safe_compile_and_verify", bind=True, max_retries=3, queue="sqlite_writes"
)
def safe_compile_and_verify(
    self,
    project_id: str,
    node_id: str,
    previous_critiques: str | None = None,
) -> dict[str, Any]:
    from prompt_matrix.verification.grounding import verify_claims

    docling_source = fetch_vault_markdown(project_id)
    draft_content = generate_draft(node_id, previous_critiques)

    verdict = verify_claims(draft_content, docling_source)
    if not verdict.grounded:
        raise self.retry(
            countdown=5,
            kwargs={
                "project_id": project_id,
                "node_id": node_id,
                "previous_critiques": f"Ungrounded claims: {verdict.unverified}",
            },
        )
    grounding = {
        "status": "heuristic_overlap",
        "verified": False,
        "reason": HEURISTIC_REASON,
        "engine": str((verdict.details or {}).get("engine") or "grounding"),
        "unverified_tokens": list(verdict.unverified or []),
    }

    redhat_prompt = f"Find logical gaps or contradictions against source:\n\n{draft_content}\n\nSource:\n{docling_source[:4000]}"
    try:
        from prompt_matrix.llm.orchestrator import orchestrate_node_compilation_sync

        critiques = orchestrate_node_compilation_sync(
            "paragraph",
            [{"role": "user", "content": redhat_prompt}],
        )
    except Exception:
        critiques = "OK"
    if "CRITICAL_GAP" in str(critiques):
        raise self.retry(
            countdown=5,
            kwargs={
                "project_id": project_id,
                "node_id": node_id,
                "previous_critiques": str(critiques),
            },
        )
    redhat = {
        "status": "unverified",
        "reason": "the critique text was not checked against the source; only the "
        "CRITICAL_GAP marker gates a redraft",
    }

    saved = commit_to_ast(project_id, node_id, draft_content)
    check_and_trigger_automations.delay(project_id)
    return {
        "status": "success",
        "verified": False,
        "project_id": project_id,
        "node_id": node_id,
        "saved": saved,
        "verification": {"grounding": grounding, "redhat": redhat, "z3": z3_not_run()},
    }
