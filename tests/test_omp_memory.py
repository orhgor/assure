"""Compile cache + Red-Hat memory: SQLite first, OMP best-effort, never crash."""

from __future__ import annotations

import json

from prompt_matrix.omp_client import sanitize_omp_tag
from prompt_matrix.services import omp_memory as mem


def test_compile_cache_key_is_omp_safe():
    key = mem.compile_cache_key("Default Project!", "hello world")
    assert key.startswith("ast:")
    assert key == sanitize_omp_tag(key)
    assert len(key) <= 50


def test_ast_cache_roundtrip_sqlite(monkeypatch):
    monkeypatch.setenv("PEM_OMP_CACHE", "1")
    monkeypatch.setattr(mem, "safe_omp_recall", lambda *_a, **_k: None)
    monkeypatch.setattr(mem, "safe_omp_remember", lambda *_a, **_k: None)
    key = mem.compile_cache_key("cache-test", "same source")
    payload = {
        "compiled": {"document": {"body": [{"id": "n1"}]}, "node_count": 1, "lock_count": 0},
        "verified": {"z3_status": "PASS"},
    }
    mem.save_ast_cache(key, "cache-test", payload)
    loaded = mem.load_ast_cache(key)
    assert loaded is not None
    assert loaded["compiled"]["node_count"] == 1
    assert loaded["verified"]["z3_status"] == "PASS"


def test_load_ast_cache_survives_omp_down(monkeypatch):
    monkeypatch.setenv("PEM_OMP_CACHE", "1")

    def boom(*_a, **_k):
        raise ConnectionError("connection refused")

    monkeypatch.setattr(mem, "safe_omp_recall", boom)
    monkeypatch.setattr(mem, "fetch_pipeline_cache", lambda _key: None)
    assert mem.load_ast_cache("ast:x:deadbeef") is None


def test_save_ast_cache_survives_omp_down(monkeypatch):
    monkeypatch.setenv("PEM_OMP_CACHE", "1")
    saved: dict = {}

    def fake_sqlite(cache_key, project_id, kind, payload):
        saved["payload"] = payload

    monkeypatch.setattr(mem, "save_pipeline_cache", fake_sqlite)

    def boom(*_a, **_k):
        raise ConnectionError("connection refused")

    monkeypatch.setattr(mem, "safe_omp_remember", boom)
    mem.save_ast_cache("ast:x:deadbeef", "x", {"compiled": {"node_count": 2}, "verified": {}})
    assert saved["payload"]["compiled"]["node_count"] == 2


def test_redhat_memory_roundtrip(monkeypatch):
    monkeypatch.setenv("PEM_OMP_CACHE", "1")
    monkeypatch.setattr(mem, "safe_omp_recall", lambda *_a, **_k: None)
    monkeypatch.setattr(mem, "safe_omp_remember", lambda *_a, **_k: None)
    mem.save_redhat_critique("rh-proj", "Unsupported ARR claim.")
    assert mem.load_redhat_critique("rh-proj") == "Unsupported ARR claim."


def test_redhat_injects_previous_into_prompt(monkeypatch):
    from prompt_matrix.routers.draft import run_redhat_audit

    captured: dict = {}

    class Gov:
        def execute_with_retry_budget(self, _pid, _task, messages, **_k):
            captured["content"] = messages[0]["content"]

            class R:
                text = "New critique"
                input_tokens = 1
                output_tokens = 1
                model_id = "deepseek/deepseek-reasoner"

            return R()

    monkeypatch.setenv("PEM_OMP_CACHE", "1")
    critiques, _usage = run_redhat_audit(
        "p1",
        "Revenue was $4M.",
        gov=Gov(),
        previous_context="Prior: missing citation.",
    )
    assert "Previous critique" in captured["content"] or "Previous context" in captured["content"]
    assert "Prior: missing citation." in captured["content"]
    assert "Revenue was $4M." in captured["content"]
    assert critiques[0]["content"] == "New critique"


def test_draft_pipeline_cache_hit_skips_claude(monkeypatch):
    monkeypatch.setenv("PEM_OMP_CACHE", "1")
    monkeypatch.setattr(
        "prompt_matrix.routers.draft.load_ast_cache",
        lambda _key: {
            "compiled": {
                "document": {"body": [{"id": "n1"}], "document_id": "doc-x"},
                "nodes": [{"id": "n1"}],
                "locks": [],
                "node_count": 1,
                "lock_count": 0,
                "draft_text": "cached draft",
            },
            "verified": {"z3_status": "PASS", "redhat_count": 0, "gate_status": "pass"},
        },
    )

    def fail_stream(*_a, **_k):
        raise AssertionError("Claude must not run on cache hit")

    monkeypatch.setattr("prompt_matrix.routers.draft._stream_model", fail_stream)
    # The pre-flight refuses a compile with no source attached before the cache
    # probe, so this project carries one — a cache hit is a compile that was
    # grounded when it was first made.
    monkeypatch.setattr(
        "prompt_matrix.routers.draft.fetch_substrate_entries_by_ids",
        lambda _pid, _ids: [
            {"id": "sub-1", "filename": "policy.pdf", "extracted_text": "Limit 5,000,000."}
        ],
    )

    class _Gov:
        class _Acct:
            def count_messages(self, _):
                return 1

            def count(self, text):
                return 1

        accountant = _Acct()

        def preflight(self, *_a, **_k):
            return None

        def record_usage(self, *_a, **_k):
            return None

    from prompt_matrix.routers.draft import run_draft_pipeline

    frames = list(
        run_draft_pipeline(
            "cache-hit",
            intent="same text twice",
            substrate_file_ids=["sub-1"],
            governor=_Gov(),
        )
    )
    joined = "".join(frames)
    assert "omp_cached" in joined
    assert "cached draft" in joined
    assert '"type": "compiled"' in joined or "compiled" in joined


def test_safe_omp_recall_none_on_connection_refused(monkeypatch):
    from prompt_matrix import omp_client

    def boom(*_a, **_k):
        raise ConnectionRefusedError("connection refused")

    monkeypatch.setattr(omp_client, "omp_recall", boom)
    monkeypatch.setattr(omp_client, "omp_remember", boom)
    assert omp_client.safe_omp_recall("ast:x:1") is None
    assert omp_client.safe_omp_remember("k", "v") is None
