"""Tests for public paste-test sandbox API."""

from __future__ import annotations

import os

import pytest

from prompt_matrix.routers.sandbox import run_sandbox_verify


class _FakeGovernor:
    class _Acct:
        def count(self, text):
            return max(1, len(text) // 4)

    class _BudgetStore:
        def ensure_project(self, *_a, **_k):
            return None

    accountant = _Acct()
    budget_store = _BudgetStore()

    def record_usage(self, *_a, **_k):
        return None


@pytest.fixture
def client():
    from prompt_matrix.web import create_app

    app = create_app(require_auth=False)
    app.config["TESTING"] = True
    return app.test_client()


def test_sandbox_verify_empty_text_400(client):
    res = client.post("/api/sandbox/verify", json={"text": ""})
    assert res.status_code == 400
    data = res.get_json()
    assert data["ok"] is False
    assert "text" in data["error"].lower()


@pytest.mark.skipif(
    os.environ.get("CI") == "true",
    reason="Z3 intermittently segfaults on GitHub Actions Python 3.11",
)
def test_sandbox_verify_success(monkeypatch, client):
    def fake_locks(_text):
        return [
            {"canonical_key": "Revenue", "value": 4_200_000, "metric": "Revenue", "confidence": 0.9}
        ], "deepseek/deepseek-chat"

    def fake_redhat(*_a, **_k):
        return [
            {
                "title": "Red-hat review",
                "content": "No major gaps.",
                "model": "deepseek/deepseek-reasoner",
            }
        ], {
            "input_tokens": 20,
            "output_tokens": 10,
            "model_id": "deepseek/deepseek-reasoner",
            "task_type": "redhat",
        }

    monkeypatch.setattr("prompt_matrix.routers.sandbox.run_lock_inference", fake_locks)
    monkeypatch.setattr("prompt_matrix.routers.sandbox.run_redhat_audit", fake_redhat)

    text = "Q3 revenue reached $4.2M, representing a 15% increase year-over-year."
    res = client.post("/api/sandbox/verify", json={"text": text})
    assert res.status_code == 200
    data = res.get_json()

    assert data["ok"] is False
    # Ungrounded fixture (no matching substrate) → gate returns review.
    assert data["ok"] is False
    assert data["gate_status"] == "review"
    assert data["unverified"] is True
    assert data["node_count"] >= 1
    assert data["lock_count"] == 1
    assert data["gate_status"] == "review"
    assert data["z3_status"] == "PASS"
    assert data["redhat_count"] == 1
    assert "redhat_critiques" in data
    assert data["redhat_results"] == data["redhat_critiques"]
    assert "document" in data
    assert data["z3_results"]["status"] == "PASS"
    assert len(data["redhat_results"]) == 1
    assert any(n.get("type") == "paragraph" for n in data["nodes"])


@pytest.mark.skipif(
    os.environ.get("CI") == "true",
    reason="Z3 intermittently segfaults on GitHub Actions Python 3.11",
)
def test_run_sandbox_verify_unit(monkeypatch):
    monkeypatch.setattr(
        "prompt_matrix.routers.sandbox.run_lock_inference",
        lambda _t: ([], "deepseek/deepseek-chat"),
    )
    monkeypatch.setattr(
        "prompt_matrix.routers.sandbox.run_redhat_audit",
        lambda *_a, **_k: ([], {"input_tokens": 0, "output_tokens": 0, "model_id": ""}),
    )

    result = run_sandbox_verify("Plain paragraph text.", governor=_FakeGovernor())
    assert result["ok"] is False
    assert result["gate_status"] == "review"
    assert result["node_count"] >= 1
    assert result["document"]["meta"]["project_id"] == "sandbox"
