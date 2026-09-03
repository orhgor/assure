"""Tests for progressive draft stream pipeline."""

from __future__ import annotations

import json

import pytest

from prompt_matrix.models.jdf import draft_text_to_sections
from prompt_matrix.routers.draft import run_draft_pipeline, verify_locks


def test_draft_text_to_sections_headings():
    body = draft_text_to_sections("## Revenue\n\nQ3 ARR reached $12M.\n\n## Growth\n\nYoY growth was 45%.")
    assert len(body) == 2
    assert body[0]["title"] == "Revenue"
    assert body[0]["children"][0]["content"].startswith("Q3 ARR")
    assert body[1]["title"] == "Growth"


def test_draft_text_to_sections_plain():
    body = draft_text_to_sections("Single paragraph draft.")
    assert len(body) == 1
    assert body[0]["children"][0]["content"] == "Single paragraph draft."


def test_verify_locks_pass():
    locks = [{"canonical_key": "Revenue", "value": 12_000_000, "metric": "ARR"}]
    result = verify_locks(locks, "Revenue ARR is $12M this quarter.")
    assert result["status"] == "PASS"
    assert result["locks_verified"] == 1


def test_run_draft_pipeline_progressive(monkeypatch):
    def fake_stream(_gov, _messages, *, cancel_check=None):
        yield 'event: token\ndata: {"type": "token", "delta": "Hello"}\n\n'
        yield ("Hello world with Revenue=100", 10, 5, "anthropic/claude-3-5-sonnet-20241022")

    def fake_locks(_text):
        return [{"canonical_key": "Revenue", "value": 100, "metric": "Revenue", "confidence": 0.9}], "deepseek/deepseek-chat"

    def fake_redhat(*_a, **_k):
        return [{"title": "Red-hat review", "content": "Looks good.", "model": "deepseek/deepseek-reasoner"}], {
            "input_tokens": 50,
            "output_tokens": 20,
            "model_id": "deepseek/deepseek-reasoner",
            "task_type": "redhat",
        }

    monkeypatch.setattr("prompt_matrix.routers.draft._stream_claude", fake_stream)
    monkeypatch.setattr("prompt_matrix.routers.draft.run_lock_inference", fake_locks)
    monkeypatch.setattr("prompt_matrix.routers.draft.run_redhat_audit", fake_redhat)

    frames = list(
        run_draft_pipeline(
            "default",
            intent="Draft a one-line summary.",
            governor=_FakeGovernor(),
        )
    )
    events = [_parse_sse(f) for f in frames if f.startswith("event:") or f.startswith("data:")]
    types = [e[1].get("type") or e[0] for e in events if isinstance(e[1], dict)]

    compiled_idx = types.index("compiled")
    audit_idx = types.index("audit_complete")
    assert compiled_idx < audit_idx, "compiled must arrive before audit_complete"

    compiled = next(data for _ev, data in events if data.get("type") == "compiled")
    assert "document" in compiled
    assert compiled["node_count"] >= 1
    assert "locks" in compiled

    audit = next(data for _ev, data in events if data.get("type") == "audit_complete")
    assert "z3_results" in audit
    assert audit["redhat_count"] == 1
    assert audit["gate_status"] == "review"
    assert audit["z3_status"] == "PASS"
    assert audit["redhat_critiques"]

    assert any(f.strip() == "data: [DONE]" for f in frames)


def _parse_sse(frame: str) -> tuple[str, dict]:
    if frame.strip() == "data: [DONE]":
        return "done", {"type": "done"}
    event = "message"
    data = ""
    for line in frame.strip().split("\n"):
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data = line[5:].strip()
    if data == "[DONE]":
        return "done", {"type": "done"}
    return event, json.loads(data)


class _FakeGovernor:
    class _Acct:
        def count_messages(self, _):
            return 10

        def count(self, text):
            return max(1, len(text) // 4)

    accountant = _Acct()

    def preflight(self, *_a, **_k):
        return None

    def policy_for(self, _task):
        class P:
            litellm_model = "anthropic/claude-3-5-sonnet-20241022"
            max_output_tokens = 256
            model_id = "anthropic/claude-3-5-sonnet-20241022"

        return P()

    def record_usage(self, *_a, **_k):
        return None

    def execute_with_retry_budget(self, *_a, **_k):
        class R:
            text = "critique"
            input_tokens = 10
            output_tokens = 5
            model_id = "deepseek/deepseek-reasoner"

        return R()


@pytest.fixture
def client():
    from prompt_matrix.web import create_app

    app = create_app(require_auth=False)
    app.config["TESTING"] = True
    return app.test_client()


def test_list_projects(client):
    res = client.get("/api/projects")
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert any(p["id"] == "default" for p in data["projects"])


def test_draft_stream_requires_intent(client):
    res = client.post("/api/projects/default/draft/stream", json={"intent": ""})
    assert res.status_code == 400
