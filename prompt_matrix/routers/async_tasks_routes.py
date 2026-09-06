"""Async task queue API (Celery + SQS)."""

from __future__ import annotations

from celery.result import AsyncResult
from flask import jsonify, request

try:
    from ..celery_app import celery_app
    from ..tasks.llm_tasks import compile_preview_task, ground_substrate_task, run_workflow_task
except ImportError:
    from celery_app import celery_app
    from tasks.llm_tasks import compile_preview_task, ground_substrate_task, run_workflow_task


def _task_status(result: AsyncResult) -> dict:
    normalized = "pending"
    if result.status == "PENDING":
        normalized = "pending"
    elif result.status in ("STARTED", "RETRY"):
        normalized = "processing"
    elif result.successful():
        payload = result.result if isinstance(result.result, dict) else {}
        normalized = str(payload.get("status") or "success").lower()
        if normalized not in ("success", "failure"):
            normalized = "success"
    elif result.failed():
        normalized = "failure"

    body: dict = {
        "task_id": result.id,
        "status": normalized,
        "celery_status": result.status,
        "ready": result.ready(),
    }
    if result.successful():
        body["result"] = result.result
    elif result.failed():
        body["error"] = str(result.result)
    return body


def register_async_task_routes(app) -> None:
    @app.post("/api/tasks/compile")
    def enqueue_compile():
        data = request.get_json(silent=True) or {}
        task_text = str(data.get("task") or "").strip()
        intent = str(data.get("intent") or "research").strip()
        context = str(data.get("context") or "")
        target = str(data.get("target_ai") or data.get("target") or "gemini").strip()
        audience = str(data.get("audience") or "general").strip()
        class_id = (data.get("class_id") or "").strip() or None
        if not task_text:
            return jsonify({"error": "task required"}), 400
        async_result = compile_preview_task.delay(
            task_text,
            intent,
            context,
            target_ai=target,
            audience=audience,
            class_id=class_id,
        )
        return jsonify({"ok": True, "task_id": async_result.id, "status": "PENDING"}), 202

    @app.post("/api/tasks/render")
    def enqueue_render():
        data = request.get_json(silent=True) or {}
        target = str(data.get("target_ai") or data.get("target") or "").strip()
        intent = str(data.get("intent") or "").strip()
        task_text = str(data.get("task") or "").strip()
        context = str(data.get("context") or "")
        if not target or not intent or not task_text:
            return jsonify({"error": "target, intent, and task required"}), 400
        async_result = run_workflow_task.delay(
            target,
            intent,
            task_text,
            context,
            workflow=str(data.get("workflow") or "single"),
            extra_targets=data.get("extra_targets") or [],
            critic=data.get("critic"),
            persona=data.get("persona"),
            ground=bool(data.get("ground")),
            direct=bool(data.get("direct", True)),
            class_id=(data.get("class_id") or "").strip() or None,
            local=bool(data.get("local")),
            cheap=bool(data.get("cheap")),
            audience=str(data.get("audience") or "general"),
        )
        return jsonify({"ok": True, "task_id": async_result.id, "status": "PENDING"}), 202

    @app.post("/api/projects/<project_id>/tasks/ground")
    def enqueue_ground(project_id: str):
        data = request.get_json(silent=True) or {}
        substrate_ids = data.get("substrate_file_ids") or data.get("substrate_ids") or []
        query = str(data.get("query") or data.get("intent") or "").strip()
        if not query:
            return jsonify({"error": "query required"}), 400
        if not isinstance(substrate_ids, list):
            return jsonify({"error": "substrate_file_ids must be a list"}), 400
        async_result = ground_substrate_task.delay(
            project_id,
            [str(x) for x in substrate_ids],
            query,
        )
        return jsonify({"ok": True, "task_id": async_result.id, "status": "PENDING"}), 202

    @app.get("/api/tasks/<task_id>")
    def task_status(task_id: str):
        result = AsyncResult(task_id, app=celery_app)
        return jsonify(_task_status(result))

    @app.post("/api/projects/<project_id>/compile/safe")
    def enqueue_safe_compile(project_id: str):
        data = request.get_json(silent=True) or {}
        node_id = str(data.get("node_id") or data.get("nodeId") or "").strip()
        if not node_id:
            return jsonify({"error": "node_id required"}), 400
        try:
            from ..tasks.compile_tasks import safe_compile_and_verify
        except ImportError:
            from tasks.compile_tasks import safe_compile_and_verify
        async_result = safe_compile_and_verify.delay(project_id, node_id)
        return jsonify({"ok": True, "task_id": async_result.id, "status": "pending"}), 202
