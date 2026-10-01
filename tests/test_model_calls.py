"""The model-call ledger (``services/model_calls``, ``db/model_calls_repository``,
``GET /api/projects/<id>/pipeline-activity``).

Why: a customer reported (2026-09-28) that requests were not reaching
OpenRouter (removed 2026-10-01 for Amazon Bedrock) and nothing in the UI could
show whether a stage's request had left
the server. These tests pin what the ledger records, that the stage travels
inside the litellm call (``metadata``) rather than beside it, that the route
lists every stage in order with ``no_record`` when there is no evidence, and
that the backend probe reports what the network said — never a default.
"""

from __future__ import annotations

import urllib.error
from datetime import datetime, timedelta, timezone

import pytest

from prompt_matrix.services import model_calls as mc
from tests.test_parsure_routes import client  # noqa: F401  (fixture)


@pytest.fixture(autouse=True)
def _fresh_probe_cache():
    """The probe is cached 30 s per (backend, key); tests must not hand each
    other a cached verdict (test_health saw this file's 401, 2026-09-28)."""
    mc._probe_cache.update(at=0.0, value=None, key=None)
    yield
    mc._probe_cache.update(at=0.0, value=None, key=None)


def _repo():
    from prompt_matrix.db import model_calls_repository as repo

    return repo


def test_stage_context_nests_and_keeps_the_outer_project() -> None:
    assert mc.current_context() == {}
    with mc.stage_context("llm_grounding", project_id="p1", task="job-1"):
        with mc.stage_context("discovery"):
            assert mc.current_context() == {"stage": "discovery", "project_id": "p1", "task": "job-1"}
        assert mc.current_context()["stage"] == "llm_grounding"
    assert mc.current_context() == {}
    meta = mc.litellm_metadata({"existing": 1})
    assert meta["existing"] == 1 and meta["assure"] == {}


def test_provider_and_stage_inference(monkeypatch) -> None:
    assert mc.provider_of("bedrock/us.anthropic.claude-sonnet-5-5") == "bedrock"
    assert mc.provider_of("ollama/qwen2.5:7b") == "ollama"
    assert mc.provider_of("anthropic.claude-sonnet-5") == "bedrock" and mc.provider_of("eu.anthropic.claude-opus-5") == "bedrock"
    monkeypatch.setenv("ASSURE_LLM_BACKEND", "bedrock")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    for k in ("ASSURE_BEDROCK_MODEL", "ASSURE_BEDROCK_MODEL_PARSE", "ASSURE_BEDROCK_MODEL_DRAFT",
              "ASSURE_BEDROCK_MODEL_ANALYSIS", "ASSURE_BEDROCK_MODEL_B"):
        monkeypatch.delenv(k, raising=False)
    # Only the parse uses Opus 5.5 → inferred; draft/evidence/compare share
    # Sonnet 5.5 → not guessed (2026-10-01 defaults).
    assert mc.infer_stage("bedrock/us.anthropic.claude-opus-5-5") == "llm_grounding"
    assert mc.infer_stage("bedrock/us.anthropic.claude-sonnet-5-5") is None


def test_record_writes_a_row_and_the_rollup_reads_it(client) -> None:  # noqa: F811
    repo = _repo()
    with client.application.app_context():
        with mc.stage_context("entailment", project_id="default", task="draft-1"):
            row = mc.record(model="bedrock/us.anthropic.claude-sonnet-5-5", status="ok", ms=1830.4,
                            prompt_chars=4210, completion_chars=12, input_tokens=1100, output_tokens=4, http_status=200)
        assert row and row["stage"] == "entailment" and row["stage_source"] == "context" and row["provider"] == "bedrock"
        err = mc.record(model="bedrock/us.anthropic.claude-sonnet-5-5", status="error", ms=50,
                        http_status=403, error="AccessDeniedException: bad key", stage="entailment", project_id="default")
        assert err["http_status"] == 401
        unknown = mc.record(model="some/unknown-model", status="ok", ms=1)
        assert unknown["stage"] is None and unknown["stage_source"] == "unknown" and unknown["project_id"] is None
        rollup = repo.stage_rollup("default")
        assert rollup["entailment"]["calls"] == 2 and rollup["entailment"]["ok"] == 1 and rollup["entailment"]["failed"] == 1
        assert rollup["entailment"]["last_http_status"] == 403 and "bad key" in rollup["entailment"]["last_error"]
        summary = repo.summary(hours=24)
        assert summary["calls"] == 3 and summary["failed"] == 1 and summary["last_call_at"]
        assert repo.summary(hours=24, project_id="default")["calls"] == 2
        calls = repo.list_for_project("default", limit=10)
        assert [c["status"] for c in calls] == ["error", "ok"]  # newest first
        assert repo.list_recent(limit=1)[0]["model"] == "some/unknown-model"


def test_litellm_callback_reads_the_stage_from_metadata(client) -> None:  # noqa: F811
    """litellm's success handler runs on a helper thread where the contextvar is
    gone; the stage must come from the call's own ``metadata``."""
    logger = mc._build_logger_class()()
    start = datetime.now(timezone.utc)
    end = start + timedelta(milliseconds=640)

    class _Msg:
        content = "VERIFIED"

    class _Choice:
        message = _Msg()

    class _Usage:
        prompt_tokens = 900
        completion_tokens = 2

    class _Resp:
        choices = [_Choice()]
        usage = _Usage()

    # litellm hands the callback the bare id and the provider apart (live run
    # 2026-09-28: rows read "meta-llama/…" with provider "meta-llama").
    kwargs = {
        "model": "us.anthropic.claude-sonnet-5-5",
        "messages": [{"role": "user", "content": "x" * 300}],
        "stream": False,
        "litellm_params": {"custom_llm_provider": "bedrock",
                           "metadata": {"assure": {"stage": "entailment", "project_id": "default", "task": None}}},
    }
    with client.application.app_context():
        assert mc.current_context() == {}
        logger.log_success_event(kwargs, _Resp(), start, end)
        rows = _repo().list_for_project("default", limit=5)
        assert rows and rows[0]["stage"] == "entailment" and rows[0]["ms"] == 640 and rows[0]["input_tokens"] == 900
        assert rows[0]["prompt_chars"] == 300 and rows[0]["completion_chars"] == 8 and rows[0]["http_status"] == 200
        assert rows[0]["model"] == "bedrock/us.anthropic.claude-sonnet-5-5" and rows[0]["provider"] == "bedrock"

        class _Exc(Exception):
            status_code = 429

        logger.log_failure_event({**kwargs, "exception": _Exc("RateLimitError: slow down")}, None, start, end)
        rows = _repo().list_for_project("default", limit=5)
        assert rows[0]["status"] == "error" and rows[0]["http_status"] == 429 and "slow down" in rows[0]["error"]


def test_probe_reports_what_the_backend_answered(monkeypatch) -> None:
    """Bedrock is ``not_probed`` (the ledger is the evidence); Ollama's
    ``/api/tags`` answer is reported as-is. The OpenRouter key probe went with
    OpenRouter on 2026-10-01."""
    monkeypatch.setenv("ASSURE_LLM_BACKEND", "bedrock")
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: (_ for _ in ()).throw(AssertionError("Bedrock probed")))
    probe = mc.probe_backend(force=True)
    assert probe["status"] == "not_probed" and probe["backend"] == "bedrock" and probe["checked_at"]

    monkeypatch.setenv("ASSURE_LLM_BACKEND", "ollama")

    class _Resp:
        status = 200

        def read(self, _n=None):
            return b'{"models": []}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: _Resp())
    assert mc.probe_backend(force=True)["status"] == "reachable"

    def _refused(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", _refused)
    assert mc.probe_backend(force=True)["status"] == "unreachable"
    # Cached: the same answer without a second request inside the TTL.
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: (_ for _ in ()).throw(AssertionError("probed again")))
    assert mc.probe_backend()["status"] == "unreachable"
    status = mc.llm_status(probe=False)
    assert status["backend"] == "ollama" and status["key_present"] is None and status["key_hint"] is None
    assert status["probe"]["status"] == "not_probed"


def test_pipeline_activity_lists_every_stage_in_order(client, monkeypatch) -> None:  # noqa: F811
    with client.application.app_context():
        with mc.stage_context("entailment", project_id="default"):
            mc.record(model="bedrock/us.anthropic.claude-sonnet-5-5", status="ok", ms=900, http_status=200)
        with mc.stage_context("compile_draft", project_id="default"):
            mc.record(model="bedrock/us.anthropic.claude-sonnet-5-5", status="error", ms=120, http_status=403,
                      error="AccessDeniedException: No auth credentials found")
    res = client.get("/api/projects/default/pipeline-activity?probe=0")
    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    assert [s["stage"] for s in body["stages"]] == list(mc.STAGE_NAMES)
    by = {s["stage"]: s for s in body["stages"]}
    assert by["parse"]["status"] == "no_record" and by["z3"]["status"] == "no_record"
    assert by["entailment"]["status"] == "ran" and by["entailment"]["calls"] == 1 and by["entailment"]["last_http_status"] == 200
    assert by["compile_draft"]["status"] == "failed" and by["compile_draft"]["last_http_status"] == 403
    assert "No auth credentials" in by["compile_draft"]["last_error"]
    assert body["llm"]["probe"]["status"] == "not_probed" and body["llm"]["calls_24h"] == 2 and body["llm"]["failed_24h"] == 1
    assert [c["stage"] for c in body["calls"]] == ["compile_draft", "entailment"]
    assert client.get("/api/projects/no-such-project/pipeline-activity?probe=0").status_code == 404
    recent = client.get("/api/model-calls?limit=5")
    assert recent.status_code == 200 and len(recent.get_json()["calls"]) == 2


def test_health_carries_the_llm_check(client, monkeypatch) -> None:  # noqa: F811
    """On Bedrock the llm block is reported and never degrades the box on its
    own (no probe, no key); a leftover ``openrouter`` value reads as Bedrock."""
    monkeypatch.setenv("ASSURE_LLM_BACKEND", "openrouter")
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: (_ for _ in ()).throw(AssertionError("network")))
    mc._probe_cache["value"] = None
    body = client.get("/health").get_json()
    llm = body["checks"]["llm"]
    assert llm["backend"] == "bedrock" and llm["key_present"] is None and llm["probe"]["status"] == "not_probed"
    assert body["status"] != "degraded" or body["checks"]["models"].get("status") in ("missing", "unreachable")
