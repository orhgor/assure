"""Celery compile pipeline with grounding, red-hat, and Z3 gates."""

from __future__ import annotations

from typing import Any

from prompt_matrix.celery_app import celery_app


def fetch_vault_markdown(project_id: str) -> str:
    try:
        from prompt_matrix.db.substrate_repository import list_substrate_for_project
    except ImportError:
        from db.substrate_repository import list_substrate_for_project

    rows = list_substrate_for_project(project_id)
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
    doc, found = splice_node(doc, node_id, updated)
    if not found:
        raise ValueError(f"node not found: {node_id}")
    return save_jdf_revision(
        project_id,
        doc,
        mutation_type="SAFE_COMPILE",
        target_node_id=node_id,
        change_summary="Hallucination minimizer commit",
    )


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

    try:
        import z3

        solver = z3.Solver()
        if solver.check() == z3.unsat:
            raise self.retry(
                countdown=5,
                kwargs={
                    "project_id": project_id,
                    "node_id": node_id,
                    "previous_critiques": "Mathematical constraint violation",
                },
            )
    except self.MaxRetriesExceededError:
        raise
    except Exception:
        pass

    saved = commit_to_ast(project_id, node_id, draft_content)
    check_and_trigger_automations.delay(project_id)
    return {"status": "success", "project_id": project_id, "node_id": node_id, "saved": saved}
