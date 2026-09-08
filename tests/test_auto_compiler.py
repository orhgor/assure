"""Auto-Compiler Phase 3 — Fast Router, Prompt Compiler, SSE multiplexing."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from prompt_matrix.services.auto_compiler import run_auto_compiler_pipeline
from prompt_matrix.services.compiler import PromptCompiler
from prompt_matrix.services.fast_router import (
    classify_intent,
    detect_sources,
)
from prompt_matrix.services.parser import extract_claims_from_stream
from prompt_matrix.services.router import route_intent
from prompt_matrix.services.vault_tfidf_cache import invalidate_workspace_cache
from prompt_matrix.services.perplexity_agent import (
    complete_perplexity_agent,
    perplexity_available,
    web_sources_from_text,
)
from prompt_matrix.services.prompt_compiler import compile_prompt
from prompt_matrix.services.verifier import claims_from_text, verify_claims


def test_classify_intent_extract():
    assert classify_intent("Extract revenue figures from the brief") == "extract"


def test_classify_intent_compare():
    assert classify_intent("Compare Q2 versus Q3 growth") == "compare"


def test_classify_intent_audit():
    assert classify_intent("Audit runway assumptions and verify claims") == "audit"


def test_classify_intent_draft_default():
    assert classify_intent("Write investor update") == "draft"
    assert classify_intent("") == "unknown"


def test_detect_sources_ranks_by_relevance(monkeypatch):
    rows = [
        {"id": "a", "filename": "revenue.pdf", "extracted_text": "Revenue was $12M in Q3."},
        {"id": "b", "filename": "hr.pdf", "extracted_text": "Headcount planning notes."},
    ]

    monkeypatch.setattr(
        "prompt_matrix.services.fast_router.list_included_vault_text",
        lambda _ws: rows,
    )
    invalidate_workspace_cache("ws-test")
    ranked = detect_sources("Summarize Q3 revenue", "ws-test")
    assert ranked
    assert ranked[0]["id"] == "a"
    assert ranked[0]["score"] >= ranked[1]["score"]


def test_compile_prompt_includes_constraints():
    prompt = compile_prompt(
        "Draft update",
        "draft",
        [{"id": "s1", "name": "brief.pdf", "excerpt": "Revenue $12M."}],
    )
    assert "ONLY the provided sources" in prompt
    assert "NOT FOUND IN SOURCES" in prompt
    assert "brief.pdf" in prompt
    assert "assure-jdf" in prompt.lower() or "JDF" in prompt or "document_id" in prompt


def test_prompt_compiler_injects_sources():
    compiler = PromptCompiler()
    messages = compiler.compile(
        "Summarize Q3 revenue",
        "--- Document 1: brief.pdf ---\nRevenue was $12M in Q3.",
    )
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "Revenue was $12M in Q3." in messages[0]["content"]
    assert "[claim: N]" in messages[0]["content"]
    assert "NO EXTERNAL KNOWLEDGE" in messages[0]["content"]
    assert "DIRECTIVE: Summarize Q3 revenue" in messages[1]["content"]


def test_prompt_compiler_format_sources_from_rows():
    block = PromptCompiler.format_sources_from_rows(
        [{"id": "s1", "filename": "brief.pdf", "extracted_text": "Revenue $12M."}]
    )
    assert "brief.pdf" in block
    assert "Revenue $12M." in block


def test_extract_claims_from_stream_parses_tags():
    text = "This is a fact [claim: 1]. This is another [Claim: 2]."
    claims = extract_claims_from_stream(text)
    assert len(claims) == 2
    assert claims[0]["claim_id"] == "claim_1"
    assert claims[0]["text"] == "This is a fact"
    assert claims[1]["claim_id"] == "claim_2"
    assert claims[1]["text"] == "This is another"


def test_extract_claims_from_stream_ignores_spacing_and_casing():
    text = "Sampling runs at 4Hz[claim:1]. Resolution is 24-bit [Claim: 2]."
    claims = extract_claims_from_stream(text)
    assert len(claims) == 2
    assert claims[0]["text"].endswith("4Hz")
    assert claims[1]["claim_id"] == "claim_2"


def test_route_intent_heuristic():
    assert route_intent("Write investor update", has_sources=True) == {
        "task": "draft",
        "has_sources": True,
        "intent_type": "draft",
    }
    assert route_intent("Extract metrics", has_sources=False)["task"] == "extract"
    assert route_intent("Extract metrics", has_sources=False)["has_sources"] is False


def test_verify_claims_fallback():
    locks = verify_claims(
        ["Revenue reached twelve million in Q3"],
        [{"id": "s1", "name": "brief.pdf", "excerpt": "Revenue reached twelve million in Q3."}],
    )
    assert locks
    assert locks[0].get("lock_hash")


def test_verify_claims_web_pill():
    locks = verify_claims(
        ["Market grew rapidly in 2025"],
        [{"id": "web-1", "name": "Web", "excerpt": "Market grew rapidly in 2025.", "web": True}],
    )
    assert locks
    assert locks[0].get("web") is True
    assert locks[0].get("pill") == "🌐"


def test_claims_from_text_splits_sentences():
    claims = claims_from_text("First sentence here. Second sentence with enough length.")
    assert len(claims) >= 1


def test_perplexity_fallback_without_key(monkeypatch):
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    monkeypatch.delenv("PERPLEXITYAI_API_KEY", raising=False)
    assert perplexity_available() is False
    out = complete_perplexity_agent("Research AI trends")
    assert "PERPLEXITY_API_KEY" in out or "Web research unavailable" in out


def test_web_sources_from_text():
    sources = web_sources_from_text("See https://example.com/report for details.")
    assert sources
    assert sources[0]["web"] is True
    assert "example.com" in sources[0].get("url", "")


def _collect_sse_events(frames: list[str]) -> dict[str, list[dict]]:
    events: dict[str, list[dict]] = {}
    for frame in frames:
        if not frame.startswith("event:"):
            continue
        lines = frame.strip().splitlines()
        event_type = lines[0].split(":", 1)[1].strip()
        payload = {}
        for line in lines[1:]:
            if line.startswith("data: "):
                payload = json.loads(line[6:])
        events.setdefault(event_type, []).append(payload)
    return events


def test_sse_multiplexing_token_and_lock(monkeypatch):
    monkeypatch.setattr(
        "prompt_matrix.services.auto_compiler.detect_sources",
        lambda *_a, **_k: [{"id": "s1", "name": "brief.pdf", "excerpt": "Revenue was $12M in Q3."}],
    )

    def fake_stream(_messages, _model):
        yield "Revenue was $12M in Q3. [claim: 1]"

    monkeypatch.setattr("prompt_matrix.services.orchestrator.default_token_stream", fake_stream)
    monkeypatch.setattr("prompt_matrix.services.auto_compiler._stream_litellm", fake_stream)
    monkeypatch.setattr(
        "prompt_matrix.services.auto_compiler._run_lock_inference",
        lambda _text: ([], "mock"),
    )
    monkeypatch.setattr(
        "prompt_matrix.services.auto_compiler._verify_locks",
        lambda _locks, _text: {"status": "PASS"},
    )
    monkeypatch.setattr(
        "prompt_matrix.services.auto_compiler.insert_run",
        lambda **kwargs: {
            "id": "run_sse_1",
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

    frames = list(
        run_auto_compiler_pipeline(
            "Summarize revenue",
            workspace_id="default",
            source_ids=["s1"],
            model="gemini",
        )
    )
    events = _collect_sse_events(frames)
    assert "token" in events
    assert events["token"][0].get("delta")
    assert "complete" in events
    assert events["complete"][0]["ok"] is True


@pytest.fixture()
def wb_client(tmp_path, monkeypatch):
    db_path = tmp_path / "auto_compiler.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db
    from prompt_matrix.web import create_app

    init_db()
    return create_app(require_auth=False).test_client()


def test_post_api_runs_sse_stream(wb_client, monkeypatch):
    frames = [
        'event: status\ndata: {"type":"status","stage":"router"}\n\n',
        'event: token\ndata: {"type":"token","delta":"Hello"}\n\n',
        'event: complete\ndata: {"type":"complete","ok":true,"run":{"id":"run_x","directive":"Hi","status":"draft","content":{},"sources_used":[],"extracted_locks":[],"model":"gemini","workspace_id":"default","unanchored":True,"created_at":"t","updated_at":"t"}}\n\n',
        "data: [DONE]\n\n",
    ]

    def fake_pipeline(*_a, **_k):
        yield from frames

    monkeypatch.setattr(
        "prompt_matrix.routers.runs_routes.run_auto_compiler_pipeline",
        fake_pipeline,
    )
    res = wb_client.post(
        "/api/runs",
        json={"directive": "Hi", "stream": True},
        headers={"Accept": "text/event-stream"},
    )
    assert res.status_code == 200
    assert res.mimetype == "text/event-stream"
    body = res.get_data(as_text=True)
    assert "event: token" in body
    assert "event: complete" in body


def test_post_api_runs_sync_json(wb_client, monkeypatch):
    monkeypatch.setattr(
        "prompt_matrix.routers.runs_routes.create_run_from_directive",
        lambda directive, **kw: {
            "id": "run_sync",
            "workspace_id": kw.get("workspace_id"),
            "directive": directive,
            "content": {},
            "model": kw.get("model", "gemini"),
            "sources_used": [],
            "extracted_locks": [],
            "status": "draft",
            "unanchored": True,
            "created_at": "2026-01-01",
            "updated_at": "2026-01-01",
            "lock_count": 0,
            "title": directive[:72],
        },
    )
    res = wb_client.post("/api/runs", json={"directive": "Sync path"})
    assert res.status_code == 201
    data = res.get_json()
    assert data["ok"] is True
    assert data["run"]["id"] == "run_sync"
