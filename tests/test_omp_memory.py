"""Compile cache + Red-Hat memory: SQLite first, OMP best-effort, never crash."""

from __future__ import annotations

import json

import pytest

from prompt_matrix.omp_client import sanitize_omp_tag
from prompt_matrix.services import omp_memory as mem


@pytest.fixture
def cache_db(tmp_path, monkeypatch):
    """A throwaway database for the SQLite round-trip tests.

    pipeline_cache.project_id declares the FK to projects, so a cache row names a
    project that has to exist. Pointing these tests at their own file also makes
    the round trip prove itself instead of reading a row an earlier run left in
    the shared database.
    """
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "history.sqlite"))
    import prompt_matrix.history as history_mod

    monkeypatch.setattr(history_mod, "DB_PATH", history_mod._resolve_db_path())
    from prompt_matrix.db.connection import init_db

    init_db()
    yield


def test_compile_cache_key_is_omp_safe():
    key = mem.compile_cache_key("Default Project!", "hello world")
    assert key.startswith("ast:")
    assert key == sanitize_omp_tag(key)
    assert len(key) <= 50


def test_ast_cache_roundtrip_sqlite(monkeypatch, cache_db):
    monkeypatch.setenv("PEM_OMP_CACHE", "1")
    monkeypatch.setattr(mem, "safe_omp_recall", lambda *_a, **_k: None)
    monkeypatch.setattr(mem, "safe_omp_remember", lambda *_a, **_k: None)
    from prompt_matrix.db.jdf_repository import ensure_project

    ensure_project("cache-test")
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


def test_redhat_memory_roundtrip(monkeypatch, cache_db):
    monkeypatch.setenv("PEM_OMP_CACHE", "1")
    monkeypatch.setattr(mem, "safe_omp_recall", lambda *_a, **_k: None)
    monkeypatch.setattr(mem, "safe_omp_remember", lambda *_a, **_k: None)
    from prompt_matrix.db.jdf_repository import ensure_project

    ensure_project("rh-proj")
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
                model_id = "openrouter/qwen/qwen3-next-80b-a3b-instruct"

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
                # The replay runs the same grounding gate a cold compile runs,
                # so the cached draft must be a draft the source actually
                # carries: it opens on a token from the source sentence and
                # its one claim-eligible paragraph anchors to a quote from it.
                "draft_text": "Limit 5,000,000 is carried by the policy for the term.",
            },
            "verified": {
                "z3_status": "PASS",
                "redhat_count": 0,
                "gate_status": "pass",
                # The replay recount (_recount_cached_verified) reads the
                # document from the verified payload and recounts the
                # provenance layer from it — a fixture without an anchored
                # paragraph would be refused as zero_anchored_claims.
                "document": {
                    "document_id": "doc-x",
                    "meta": {},
                    "body": [
                        {
                            "type": "section",
                            "id": "s1",
                            "title": "A",
                            "children": [
                                {
                                    "type": "paragraph",
                                    "id": "p1",
                                    "content": "The Limit 5,000,000 is carried by the policy for the term.",
                                    "provenance": [
                                        {"extracted_quote": "Limit 5,000,000."}
                                    ],
                                }
                            ],
                        }
                    ],
                },
            },
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
    # The replayed frame carries the cached draft text — the grounded fixture
    # draft, not the literal string "cached draft" the fixture used to carry.
    assert "Limit 5,000,000 is carried by the policy" in joined
    assert '"type": "compiled"' in joined or "compiled" in joined


def test_safe_omp_recall_none_on_connection_refused(monkeypatch):
    from prompt_matrix import omp_client

    def boom(*_a, **_k):
        raise ConnectionRefusedError("connection refused")

    monkeypatch.setattr(omp_client, "omp_recall", boom)
    monkeypatch.setattr(omp_client, "omp_remember", boom)
    assert omp_client.safe_omp_recall("ast:x:1") is None
    assert omp_client.safe_omp_remember("k", "v") is None
