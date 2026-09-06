"""Celery tasks: compile preview, workflow render, grounding."""

from __future__ import annotations

from typing import Any

from prompt_matrix.celery_app import celery_app


@celery_app.task(name="assure.compile_preview", bind=True)
def compile_preview_task(
    self,
    task: str,
    intent: str,
    context: str,
    *,
    target_ai: str = "gemini",
    audience: str = "general",
    class_id: str | None = None,
) -> dict[str, Any]:
    """Compile a deep prompt without calling the model (preview path)."""
    try:
        from prompt_matrix.pipelines import compile_deep_prompt
    except ImportError:
        from pipelines import compile_deep_prompt

    rendered = compile_deep_prompt(
        task,
        intent,
        context,
        target_ai=target_ai,
        params={"audience": audience},
        class_id=class_id,
    )
    return {
        "task_id": self.request.id,
        "prompt": rendered.prompt,
        "intent": rendered.intent,
        "target_ai": rendered.target_ai,
        "files_read": rendered.files_read,
    }


@celery_app.task(name="assure.run_workflow", bind=True)
def run_workflow_task(
    self,
    target: str,
    intent: str,
    task: str,
    context: str,
    *,
    workflow: str = "single",
    extra_targets: list[str] | None = None,
    critic: str | None = None,
    persona: str | None = None,
    ground: bool = False,
    direct: bool = True,
    class_id: str | None = None,
    local: bool = False,
    cheap: bool = False,
    audience: str = "general",
) -> dict[str, Any]:
    """Run a PEM workflow off the request thread."""
    try:
        from prompt_matrix.pipelines import run_workflow
    except ImportError:
        from pipelines import run_workflow

    result = run_workflow(
        target,
        intent,
        task,
        context,
        workflow=workflow,
        extra_targets=extra_targets or [],
        critic=critic,
        persona=persona,
        ground=ground,
        direct=direct,
        copy=False,
        class_id=class_id,
        local=local,
        lint=direct,
        cheap=cheap,
        audience=audience,
    )
    return {
        "task_id": self.request.id,
        "reply": result.reply,
        "prompt": result.prompt,
        "target_ai": result.target_ai,
        "intent": result.intent,
        "workflow": result.workflow,
        "run_hash": getattr(result, "run_hash", None),
    }


@celery_app.task(name="assure.ground_substrate", bind=True)
def ground_substrate_task(
    self,
    project_id: str,
    substrate_ids: list[str],
    query: str,
) -> dict[str, Any]:
    """Ground a query against selected substrate vault entries."""
    try:
        from prompt_matrix.db.substrate_repository import fetch_substrate_entries_by_ids
    except ImportError:
        from db.substrate_repository import fetch_substrate_entries_by_ids

    rows = fetch_substrate_entries_by_ids(project_id, substrate_ids)
    chunks = [str(r.get("extracted_text") or "") for r in rows if isinstance(r, dict)]
    context = "\n\n".join(chunks)
    return {
        "task_id": self.request.id,
        "project_id": project_id,
        "substrate_count": len(rows),
        "context_chars": len(context),
        "context_preview": context[:2000],
        "query": query,
    }
