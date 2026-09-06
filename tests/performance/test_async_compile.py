"""Celery async compile / render task tests (eager mode)."""

from __future__ import annotations

import pytest

from prompt_matrix.celery_app import celery_app


@pytest.fixture
def client():
    from prompt_matrix.web import create_app

    app = create_app(require_auth=False)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def eager_celery(monkeypatch, tmp_path):
    monkeypatch.setenv("CELERY_TASK_ALWAYS_EAGER", "1")
    monkeypatch.setenv(
        "CELERY_RESULT_BACKEND",
        f"db+sqlite:///{tmp_path / 'celery-results.sqlite'}",
    )
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    celery_app.conf.task_store_eager_result = True
    celery_app.conf.result_backend = f"db+sqlite:///{tmp_path / 'celery-results.sqlite'}"
    yield


def test_compile_task_queues_and_returns_result():
    from prompt_matrix.tasks.llm_tasks import compile_preview_task

    async_result = compile_preview_task.delay(
        "Summarize compliance risks",
        "research",
        "Sample policy excerpt.",
        target_ai="gemini",
    )
    assert async_result.id
    assert async_result.ready()
    payload = async_result.get(timeout=30)
    assert payload["task_id"] == async_result.id
    assert "prompt" in payload
    assert payload["intent"] == "research"


def test_async_api_enqueue_and_poll(client):
    enq = client.post(
        "/api/tasks/compile",
        json={
            "task": "Draft executive summary",
            "intent": "research",
            "context": "Quarterly report.",
            "target_ai": "gemini",
        },
    )
    assert enq.status_code == 202
    body = enq.get_json()
    task_id = body["task_id"]
    assert body["status"] == "PENDING"

    status = client.get(f"/api/tasks/{task_id}")
    assert status.status_code == 200
    st = status.get_json()
    assert st["task_id"] == task_id
    assert st["ready"] is True
    assert st["status"] == "success"
    assert "result" in st
    assert st["result"]["prompt"]
