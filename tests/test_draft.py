"""Tests for progressive draft stream pipeline."""

from __future__ import annotations

import json
import os

import pytest

from prompt_matrix.models.jdf import draft_text_to_sections
from prompt_matrix.routers.draft import run_draft_pipeline, run_redhat_pipeline, verify_locks


@pytest.fixture(autouse=True)
def _disable_omp_cache_for_draft_tests(monkeypatch, request):
    if request.node.name == "test_run_draft_pipeline_omp_cache_hit":
        monkeypatch.setenv("PEM_OMP_CACHE", "1")
        return
    monkeypatch.setenv("PEM_OMP_CACHE", "0")


def test_draft_text_to_sections_headings():
    body = draft_text_to_sections(
        "## Revenue\n\nQ3 ARR reached $12M.\n\n## Growth\n\nYoY growth was 45%."
    )
    assert len(body) == 2
    assert body[0]["title"] == "Revenue"
    assert body[0]["children"][0]["content"].startswith("Q3 ARR")
    assert body[1]["title"] == "Growth"


def test_draft_text_to_sections_plain():
    body = draft_text_to_sections("Single paragraph draft.")
    assert len(body) == 1
    assert body[0]["children"][0]["content"] == "Single paragraph draft."


@pytest.mark.skipif(
    os.environ.get("CI") == "true",
    reason="Z3 intermittently segfaults on GitHub Actions Python 3.11",
)
def test_verify_locks_pass():
    locks = [{"canonical_key": "Revenue", "value": 12_000_000, "metric": "ARR"}]
    result = verify_locks(locks, "Revenue ARR is $12M this quarter.")
    assert result["status"] == "PASS"
    assert result["locks_verified"] == 1


def test_run_draft_pipeline_progressive(monkeypatch):
    """Draft pipeline ends at "verified" (Math Check gate). Red-Hat is
    opt-in and never runs automatically — see test_run_redhat_pipeline."""

    def fake_stream(_gov, _messages, *, target_ai=None, cancel_check=None):
        yield 'event: token\ndata: {"type": "token", "delta": "Hello"}\n\n'
        yield ("Hello world with Revenue=100", 10, 5, "anthropic/claude-3-5-sonnet-20241022")

    def fake_locks(_text):
        return [
            {"canonical_key": "Revenue", "value": 100, "metric": "Revenue", "confidence": 0.9}
        ], "deepseek/deepseek-chat"

    def fail_if_called_redhat(*_a, **_k):
        raise AssertionError("run_redhat_audit must not be called by run_draft_pipeline")

    monkeypatch.setattr("prompt_matrix.routers.draft._stream_model", fake_stream)
    monkeypatch.setattr("prompt_matrix.routers.draft.run_lock_inference", fake_locks)
    monkeypatch.setattr("prompt_matrix.routers.draft.run_redhat_audit", fail_if_called_redhat)

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
    verified_idx = types.index("verified")
    assert compiled_idx < verified_idx, "compiled must arrive before verified"
    assert (
        "audit_complete" not in types
    ), "audit_complete is opt-in — not part of run_draft_pipeline"

    compiled = next(data for _ev, data in events if data.get("type") == "compiled")
    assert "document" in compiled
    assert compiled["node_count"] >= 1
    assert "locks" in compiled

    # "verified" is the hybrid dock gate: Z3 has run, Red-Hat has not (and
    # will not, unless the user opts in via run_redhat_pipeline).
    verified = next(data for _ev, data in events if data.get("type") == "verified")
    assert verified["z3_status"] == "PASS"
    assert verified["redhat_count"] == 0
    assert verified["redhat_critiques"] == []
    assert verified["gate_status"] == "pass"
    assert "document" in verified

    assert any(f.strip() == "data: [DONE]" for f in frames)


def test_run_draft_pipeline_omp_cache_hit(monkeypatch):
    """OMP compile cache should skip the LLM when a prior result exists."""

    def fail_stream(*_a, **_k):
        raise AssertionError("_stream_model must not run on cache hit")

    cached = {
        "draft_text": "Cached draft.",
        "document": {
            "document_id": "doc-default",
            "meta": {},
            "truth_ledger": {},
            "body": [
                {
                    "type": "section",
                    "id": "sec-1",
                    "title": "Cached",
                    "children": [
                        {"type": "paragraph", "id": "para-1", "content": "Cached draft."},
                    ],
                }
            ],
        },
        "locks": [],
        "verified": {
            "ok": True,
            "gate_status": "pass",
            "z3_status": "PASS",
            "z3_results": {"status": "PASS"},
            "redhat_count": 0,
            "redhat_critiques": [],
            "document": {
                "document_id": "doc-default",
                "meta": {},
                "truth_ledger": {},
                "body": [],
            },
        },
    }

    monkeypatch.setattr("prompt_matrix.routers.draft._stream_model", fail_stream)
    wrapped = {
        "compiled": {
            "document": cached["document"],
            "nodes": cached["document"]["body"],
            "locks": cached["locks"],
            "node_count": 1,
            "lock_count": 0,
            "draft_text": cached["draft_text"],
        },
        "verified": cached["verified"],
    }
    monkeypatch.setattr("prompt_matrix.routers.draft.load_ast_cache", lambda _key: wrapped)
    remember_calls: list[tuple] = []
    monkeypatch.setattr(
        "prompt_matrix.routers.draft.save_ast_cache",
        lambda *args, **kwargs: remember_calls.append((args, kwargs)),
    )

    frames = list(
        run_draft_pipeline(
            "default",
            intent="Same intent as before.",
            governor=_FakeGovernor(),
        )
    )
    events = [_parse_sse(f) for f in frames if f.startswith("event:") or f.startswith("data:")]
    types = [e[1].get("type") or e[0] for e in events if isinstance(e[1], dict)]
    assert "compiled" in types
    assert "verified" in types
    compiled = next(data for _ev, data in events if data.get("type") == "compiled")
    assert compiled.get("cache_hit") is True
    assert remember_calls == []


def test_run_redhat_pipeline_opt_in(monkeypatch):
    """Opt-in Stage 4: only runs when explicitly invoked, over an
    already-verified document, and attaches findings without recomputing
    Z3."""

    def fake_redhat(*_a, **_k):
        return [
            {
                "title": "Red-hat review",
                "content": "Looks good.",
                "model": "deepseek/deepseek-reasoner",
            }
        ], {
            "input_tokens": 50,
            "output_tokens": 20,
            "model_id": "deepseek/deepseek-reasoner",
            "task_type": "redhat",
        }

    monkeypatch.setattr("prompt_matrix.routers.draft.run_redhat_audit", fake_redhat)

    verified_document = {
        "document_id": "doc-default",
        "meta": {},
        "truth_ledger": {"Revenue": 100},
        "body": [
            {
                "type": "section",
                "id": "sec-1",
                "title": "Draft",
                "children": [
                    {
                        "type": "paragraph",
                        "id": "para-1",
                        "content": "Hello world with Revenue=100",
                    }
                ],
            }
        ],
    }

    frames = list(
        run_redhat_pipeline(
            "default",
            draft_text="Hello world with Revenue=100",
            document=verified_document,
            z3_results={"status": "PASS", "violations": [], "lock_results": []},
            governor=_FakeGovernor(),
        )
    )
    events = [_parse_sse(f) for f in frames if f.startswith("event:") or f.startswith("data:")]

    audit = next(data for _ev, data in events if data.get("type") == "audit_complete")
    assert audit["z3_status"] == "PASS"
    assert audit["redhat_count"] == 1
    assert audit["gate_status"] == "review"
    assert audit["redhat_critiques"]
    assert audit["document"]["body"][0]["children"][0]["annotations"]["redhat"]

    assert any(f.strip() == "data: [DONE]" for f in frames)


def test_run_redhat_pipeline_survives_model_error(monkeypatch):
    """Red-Hat failures must still emit audit_complete so the UI can recover."""

    def boom(*_a, **_k):
        raise RuntimeError("API timeout")

    monkeypatch.setattr("prompt_matrix.routers.draft.run_redhat_audit", boom)

    doc = {
        "document_id": "doc-default",
        "meta": {},
        "truth_ledger": {},
        "body": [
            {
                "type": "section",
                "id": "sec-1",
                "title": "Draft",
                "children": [
                    {"type": "paragraph", "id": "para-1", "content": "Revenue was $4.2M."},
                ],
            }
        ],
    }

    frames = list(
        run_redhat_pipeline(
            "default",
            draft_text="Revenue was $4.2M.",
            document=doc,
            governor=_FakeGovernor(),
        )
    )
    events = [_parse_sse(f) for f in frames if f.startswith("event:") or f.startswith("data:")]
    audit = next(data for _ev, data in events if data.get("type") == "audit_complete")
    assert audit["redhat_count"] == 1
    assert "Audit failed" in audit["redhat_critiques"][0]["content"]


def test_run_redhat_pipeline_target_node_id(monkeypatch):
    """On-demand surgical-canvas audit: scoped to one node via
    target_node_id, so the finding must attach to that node, not the
    first node in document order."""

    def fake_redhat(*_a, **_k):
        return [
            {
                "title": "Red-hat review",
                "content": "Unsupported claim.",
                "model": "deepseek/deepseek-reasoner",
            }
        ], {
            "input_tokens": 10,
            "output_tokens": 5,
            "model_id": "deepseek/deepseek-reasoner",
            "task_type": "redhat",
        }

    monkeypatch.setattr("prompt_matrix.routers.draft.run_redhat_audit", fake_redhat)

    doc = {
        "document_id": "doc-default",
        "meta": {},
        "truth_ledger": {},
        "body": [
            {
                "type": "section",
                "id": "sec-1",
                "title": "Draft",
                "children": [
                    {"type": "paragraph", "id": "para-1", "content": "First paragraph."},
                    {"type": "paragraph", "id": "para-2", "content": "Second paragraph."},
                ],
            }
        ],
    }

    frames = list(
        run_redhat_pipeline(
            "default",
            draft_text="Second paragraph.",
            document=doc,
            target_node_id="para-2",
            governor=_FakeGovernor(),
        )
    )
    events = [_parse_sse(f) for f in frames if f.startswith("event:") or f.startswith("data:")]
    audit = next(data for _ev, data in events if data.get("type") == "audit_complete")

    children = audit["document"]["body"][0]["children"]
    para1 = next(c for c in children if c["id"] == "para-1")
    para2 = next(c for c in children if c["id"] == "para-2")
    assert not (para1.get("annotations") or {}).get("redhat")
    assert (para2.get("annotations") or {}).get("redhat")


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


def test_draft_payload_selection_compile_type():
    from prompt_matrix.routers.draft import DraftPayload

    payload = DraftPayload.model_validate(
        {
            "intent": "",
            "compileType": "selection",
            "content": "Revenue grew 12% year over year.",
        }
    )
    assert payload.compile_type == "selection"
    assert payload.content.startswith("Revenue")


def test_draft_stream_selection_requires_content(client):
    res = client.post(
        "/api/projects/default/draft/stream",
        json={"intent": "", "compileType": "selection", "content": ""},
    )
    assert res.status_code == 400


def test_draft_redhat_stream_requires_draft_text(client):
    res = client.post(
        "/api/projects/default/draft/redhat/stream",
        json={"draft_text": "", "document": {}},
    )
    assert res.status_code == 400
