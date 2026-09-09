"""Orchestrator SSE stream — token order and verification_complete payload."""

from __future__ import annotations

import json

import pytest

from prompt_matrix.services.orchestrator import generate_run_stream, orchestrate_sourced_run


def _parse_sse_frames(frames: list[str]) -> list[dict]:
    parsed: list[dict] = []
    for frame in frames:
        for line in frame.splitlines():
            if not line.startswith("data: "):
                continue
            try:
                parsed.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                continue
    return parsed


def test_generate_run_stream_yields_tokens_before_verification():
    source_text = "The device samples electrodermal activity at 4Hz."

    def fake_stream(_messages, _model):
        yield "The device samples electrodermal activity at 4Hz "
        yield "[claim: 1]"

    frames = list(
        generate_run_stream(
            "Summarize sampling rate",
            source_text,
            stream_fn=fake_stream,
            run_id="run_test_01",
        )
    )
    assert frames

    token_indices = [i for i, f in enumerate(frames) if '"token"' in f]
    verify_indices = [i for i, f in enumerate(frames) if "verification_complete" in f]
    assert token_indices
    assert verify_indices
    assert max(token_indices) < min(verify_indices)

    payloads = _parse_sse_frames(frames)
    verify_payload = next(p for p in payloads if p.get("event") == "verification_complete")
    assert verify_payload["data"]["run_id"] == "run_test_01"
    locks = verify_payload["data"]["locks"]
    assert len(locks) == 1
    assert locks[0]["claim_id"] == "claim_1"
    assert locks[0]["status"] in ("grounded", "amber")
    assert "confidence_score" in locks[0]
    assert "engine" in locks[0]
    assert "evidence" in locks[0]


def test_generate_run_stream_parses_multiple_claim_tags():
    source_text = "Fact A is true. Fact B is also true."

    def fake_stream(_messages, _model):
        yield "Fact A is true [claim: 1]. Fact B is also true [Claim: 2]."

    frames = list(
        generate_run_stream(
            "List facts",
            source_text,
            stream_fn=fake_stream,
        )
    )
    payloads = _parse_sse_frames(frames)
    verify_payload = next(p for p in payloads if p.get("event") == "verification_complete")
    assert len(verify_payload["data"]["locks"]) == 2
    assert verify_payload["data"]["locks"][0]["claim_id"] == "claim_1"
    assert verify_payload["data"]["locks"][1]["claim_id"] == "claim_2"


def test_orchestrate_sourced_run_emits_complete_last(monkeypatch):
    def fake_stream(_messages, _model):
        yield "Revenue reached $12M in Q3 [claim: 1]."

    monkeypatch.setattr(
        "prompt_matrix.services.orchestrator.default_token_stream",
        fake_stream,
    )
    monkeypatch.setattr(
        "prompt_matrix.services.orchestrator.insert_run",
        lambda **kwargs: {
            "id": "run_orch_1",
            "workspace_id": kwargs.get("workspace_id"),
            "directive": kwargs.get("directive"),
            "content": kwargs.get("content") or {},
            "model": kwargs.get("model"),
            "sources_used": kwargs.get("sources_used") or [],
            "extracted_locks": kwargs.get("extracted_locks") or [],
            "status": kwargs.get("status"),
            "unanchored": False,
            "created_at": "2026-01-01",
            "updated_at": "2026-01-01",
        },
    )

    sources = [
        {
            "id": "s1",
            "name": "brief.pdf",
            "excerpt": "Revenue reached $12M in Q3.",
        }
    ]
    frames = list(
        orchestrate_sourced_run(
            "Summarize revenue",
            sources=sources,
            sources_block="Revenue reached $12M in Q3.",
            workspace_id="default",
            model="gemini",
        )
    )

    assert any("verification_complete" in f for f in frames)
    assert any(f.startswith("event: complete") for f in frames)
    complete_idx = next(i for i, f in enumerate(frames) if f.startswith("event: complete"))
    verify_idx = next(i for i, f in enumerate(frames) if "verification_complete" in f)
    assert verify_idx < complete_idx


@pytest.fixture()
def wb_client(tmp_path, monkeypatch):
    db_path = tmp_path / "orchestrator.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db
    from prompt_matrix.web import create_app

    init_db()
    return create_app(require_auth=False).test_client()


def test_post_api_runs_execute_sse(wb_client, monkeypatch):
    def fake_stream(_messages, _model):
        yield "Revenue was $12M [claim: 1]."

    monkeypatch.setattr(
        "prompt_matrix.routers.runs_routes.detect_sources",
        lambda *_a, **_k: [{"id": "s1", "name": "brief.pdf", "excerpt": "Revenue was $12M."}],
    )
    monkeypatch.setattr(
        "prompt_matrix.services.orchestrator.default_token_stream",
        fake_stream,
    )
    monkeypatch.setattr(
        "prompt_matrix.services.orchestrator.insert_run",
        lambda **kwargs: {
            "id": "run_exec_1",
            "workspace_id": kwargs.get("workspace_id"),
            "directive": kwargs.get("directive"),
            "content": kwargs.get("content") or {},
            "model": kwargs.get("model"),
            "sources_used": kwargs.get("sources_used") or [],
            "extracted_locks": kwargs.get("extracted_locks") or [],
            "status": kwargs.get("status"),
            "unanchored": False,
            "created_at": "2026-01-01",
            "updated_at": "2026-01-01",
        },
    )

    res = wb_client.post(
        "/api/runs/execute",
        json={"directive": "Summarize revenue", "workspace_id": "default"},
        headers={"Accept": "text/event-stream"},
    )
    assert res.status_code == 200
    assert res.mimetype == "text/event-stream"
    body = res.get_data(as_text=True)
    assert '"token"' in body
    assert "verification_complete" in body
    assert "event: complete" in body
    token_pos = body.find('"token"')
    verify_pos = body.find("verification_complete")
    assert token_pos >= 0 and verify_pos > token_pos
