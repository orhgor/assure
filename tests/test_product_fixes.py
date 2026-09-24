"""Regression tests for the 2026-09-22 product-logic audit fixes.

One test class per finding; each pins the behaviour the audit found wrong:
error text persisted as verified content, unlabelled mock output, numbers
rewritten by polish, magnitude suffixes dropped by scan, a dead Ground button,
wrong status codes, a Red-Hat spinner with no broker, a "healthy" /health with
a dead dependency, and ghost projects answering 200.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.test_founder_restore import _reset_db_path

STATIC = Path(__file__).resolve().parents[1] / "prompt_matrix" / "static"


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    _reset_db_path(monkeypatch, tmp_path / "history.sqlite")
    from prompt_matrix.db.connection import init_db
    from prompt_matrix.web import create_app

    init_db()
    return create_app(require_auth=False).test_client()


def _events(raw: str) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for frame in raw.split("\n\n"):
        frame = frame.strip()
        if not frame or frame.startswith(":"):
            continue
        event, data = "", "{}"
        for line in frame.splitlines():
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data = line[len("data:"):].strip()
        out.append((event, json.loads(data)))
    return out


# --------------------------------------------------------------------------- 1
class TestFailedModelCallNotPersisted:
    def test_error_text_yields_error_event_and_no_persist(self, tmp_path, monkeypatch):
        _reset_db_path(monkeypatch, tmp_path / "history.sqlite")
        from prompt_matrix.cost_governance import ExecutionResult
        from prompt_matrix.db.connection import init_db
        from prompt_matrix.routers import inquire_stream as mod

        init_db()

        class _Gov:
            def preflight(self, *_a, **_k):
                return None

            def execute_with_retry_budget(self, *_a, **_k):
                return ExecutionResult(
                    ok=True,
                    text=(
                        "ERROR: litellm.AuthenticationError: DeepSeek is not connected.\n"
                        '  File "/app/.venv/lib/python3.11/site-packages/litellm/main.py", line 12'
                    ),
                    model_id="deepseek/deepseek-chat",
                )

            def record_usage(self, *_a, **_k):
                return None

        persisted: list[str] = []
        monkeypatch.setattr(
            mod, "persist_surgical_rewrite", lambda *a, **k: persisted.append("x") or {}
        )
        document = {
            "document_id": "doc-x",
            "meta": {"project_id": "fix1"},
            "truth_ledger": {},
            "body": [
                {
                    "type": "section",
                    "id": "sec-1",
                    "title": "S",
                    "children": [
                        {
                            "type": "paragraph",
                            "id": "para-1",
                            "content": "Revenue was $12M.",
                            "meta": {},
                            "annotations": {"redhat": [], "z3": []},
                        }
                    ],
                }
            ],
        }
        with mod._open_ledger() as ledger:
            raw = "".join(
                mod.run_inquire_pipeline(
                    "fix1",
                    user_intent="tighten this",
                    target_node_id="para-1",
                    run_redhat=True,
                    document=document,
                    governor=_Gov(),
                    ledger=ledger,
                )
            )
        events = _events(raw)
        names = [name for name, _ in events]
        assert "truth_check" not in names
        assert "jdf_node_ready" not in names
        assert "redhat_annotation" not in names
        assert persisted == []
        error = dict(events)["error"]
        assert error["ok"] is False
        assert "not connected" in error["error"]
        assert "/app/" not in error["error"] and "File " not in error["error"]
        complete = events[-1]
        assert complete[0] == "complete"
        assert complete[1]["ok"] is False
        assert complete[1]["persisted"] is False


# --------------------------------------------------------------------------- 2
class TestOrchestrateMockIsLabelled:
    def test_route_marks_mock(self, app_client, monkeypatch):
        monkeypatch.setattr(
            "prompt_matrix.routers.orchestrator_routes._provider_keys_configured", lambda: False
        )
        res = app_client.post("/api/projects/founder/orchestrate", json={"intent": "compare"})
        assert res.status_code == 200
        body = res.get_json()
        assert body["status"] == "mock"
        assert body["mock"] is True
        assert body["stack"] == "mock"
        assert "illustrative" in body["notice"].lower()
        assert body["status"] != "success"

    def test_live_failure_falls_back_to_labelled_mock(self, app_client, monkeypatch):
        monkeypatch.setattr(
            "prompt_matrix.routers.orchestrator_routes._provider_keys_configured", lambda: True
        )

        def _boom(_intent):
            raise RuntimeError("both models failed\n  File \"/srv/x.py\", line 3")

        monkeypatch.setattr(
            "prompt_matrix.routers.orchestrator_routes._live_orchestrate_payload", _boom
        )
        body = app_client.post(
            "/api/projects/founder/orchestrate", json={"intent": "compare"}
        ).get_json()
        assert body["status"] == "mock" and body["mock"] is True
        assert body["warning"].startswith("RuntimeError: both models failed")
        assert "/srv/" not in body["warning"]

    def test_js_renders_mock_badge(self):
        js = (STATIC / "orchestrator.js").read_text(encoding="utf-8")
        assert 'payload.stack === "mock" || payload.mock === true' in js
        assert "staging-mock-badge" in js
        assert "MOCK — no model keys configured" in js
        # A mock payload must not be thrown away as an error.
        assert 'payload.status !== "mock"' in js


# --------------------------------------------------------------------------- 3
class TestPolishKeepsNumbers:
    def test_regex_polish_does_not_split_numbers(self):
        from prompt_matrix.services.polish_document import _polish_prose

        out = _polish_prose("revenue was $500,000,up 2.4% in q3;margin 12.5%,flat")
        assert "$500,000," in out and "$500, 000" not in out
        assert "2.4%" in out and "2. 4%" not in out
        assert "12.5%" in out
        # Sentence punctuation still gets its space.
        assert "$500,000, up" in out
        assert "q3; margin" in out
        assert "12.5%, flat" in out

    def test_numbers_preserved_check(self):
        from prompt_matrix.services.polish_document import numbers_preserved

        assert numbers_preserved("Revenue $500,000 up 2.4%", "Up 2.4%, revenue $500,000.")
        assert not numbers_preserved("Revenue $500,000", "Revenue $500, 000")
        assert not numbers_preserved("Growth 2.4%", "Growth 2.5%")
        assert not numbers_preserved("ARR $150M", "ARR $150")

    def test_altered_number_returns_original_text(self, monkeypatch):
        from prompt_matrix.services import polish_document as mod

        doc = {
            "document_id": "d",
            "meta": {"project_id": "founder"},
            "truth_ledger": {},
            "body": [
                {
                    "type": "section",
                    "id": "s",
                    "title": "T",
                    "children": [
                        {
                            "type": "paragraph",
                            "id": "p",
                            "content": "revenue reached $500,000 in q3, up 2.4%",
                            "meta": {"lock_pills": []},
                            "annotations": {"redhat": [], "z3": []},
                            "provenance": [],
                        }
                    ],
                }
            ],
        }
        monkeypatch.setattr(mod, "_polish_with_llm", lambda _t: "Revenue reached $5,000,000 in Q3, up 2.5%.")
        result = mod.run_polish_document(doc, use_llm=True)
        assert result["preserved"] is False
        assert result["strict_preservation"] is False
        assert result["status"] == "error"
        assert result["document"]["body"][0]["children"][0]["content"] == (
            "revenue reached $500,000 in q3, up 2.4%"
        )
        assert result["preservation_note"]

    def test_regex_path_preserves_and_reports(self):
        from prompt_matrix.services.polish_document import run_polish_document

        doc = {
            "document_id": "d",
            "meta": {"project_id": "founder"},
            "truth_ledger": {},
            "body": [
                {
                    "type": "section",
                    "id": "s",
                    "title": "T",
                    "children": [
                        {
                            "type": "paragraph",
                            "id": "p",
                            "content": "revenue reached $500,000 in q3,up 2.4% [🔒 #01]",
                            "meta": {"lock_pills": []},
                            "annotations": {"redhat": [], "z3": []},
                            "provenance": [],
                        }
                    ],
                }
            ],
        }
        result = run_polish_document(doc, use_llm=False)
        assert result["preserved"] is True
        content = result["document"]["body"][0]["children"][0]["content"]
        assert "$500,000" in content and "2.4%" in content and "[🔒 #01]" in content


# --------------------------------------------------------------------------- 4
class TestScanKeepsMagnitude:
    def test_parse_dollar_claims(self):
        from prompt_matrix.services.full_context_scan import _parse_dollar_claims

        claims = _parse_dollar_claims("Raised $150M at a $2.4bn valuation; fees of $5,000,000 and $3 million.")
        assert claims == [
            (150_000_000.0, "$150M"),
            (2_400_000_000.0, "$2.4bn"),
            (5_000_000.0, "$5,000,000"),
            (3_000_000.0, "$3 million"),
        ]

    def test_scan_reports_suffix_in_claim_text(self):
        from prompt_matrix.services.full_context_scan import run_full_context_scan

        doc = {
            "document_id": "d",
            "meta": {"project_id": "founder"},
            "truth_ledger": {},
            "body": [
                {
                    "type": "section",
                    "id": "s",
                    "title": "Main",
                    "children": [
                        {
                            "type": "paragraph",
                            "id": "p",
                            "content": "The company raised $150M in Series C.",
                            "meta": {"lock_pills": []},
                            "annotations": {"redhat": [], "z3": []},
                            "provenance": [],
                        }
                    ],
                }
            ],
        }
        result = run_full_context_scan(doc)
        numeric = [r for r in result["issues"] if r["category"] == "Unverified Number"]
        assert numeric, result["issues"]
        assert "$150M" in numeric[0]["description"]
        assert not re.search(r"\$150\b(?!M)", numeric[0]["description"])


# --------------------------------------------------------------------------- 5
class TestGroundButtonWired:
    def test_ground_posts_to_refine_route(self):
        js = (STATIC / "surgical_click.js").read_text(encoding="utf-8")
        assert '"/ground"' not in js
        start = js.index("postGround: function (mode)")
        end = js.index("postRefine: function (opts)")
        body = js[start:end]
        assert "this.postRefine(" in body
        assert "ground_from_vault: true" in body

    def test_refine_route_accepts_ground_from_vault(self, app_client, monkeypatch):
        seen: dict = {}

        def _fake(project_id, **kwargs):
            seen.update(kwargs)
            return {"ok": True, "node": {"id": kwargs["node_id"], "content": "x"}}

        monkeypatch.setattr("prompt_matrix.routers.refine_node.run_refine_node", _fake)
        res = app_client.post(
            "/api/projects/founder/refine-node",
            json={
                "node_id": "para-1",
                "user_instruction": "ground it",
                "ground_from_vault": True,
                "substrate_file_ids": ["f1"],
                "ground_mode": "auto",
                "document": {"document_id": "d", "meta": {}, "truth_ledger": {}, "body": []},
            },
        )
        assert res.status_code == 200, res.get_json()
        assert seen["ground_from_vault"] is True
        assert seen["substrate_file_ids"] == ["f1"]


# --------------------------------------------------------------------------- 6
class TestErrorStatusCodes:
    def test_clean_error_message_strips_paths_and_tracebacks(self):
        from prompt_matrix.lib.http_errors import clean_error_message, error_status

        exc = RuntimeError(
            "DeepSeek API key not configured for lock inference.\n"
            'Traceback (most recent call last):\n  File "/app/prompt_matrix/x.py", line 3'
        )
        assert clean_error_message(exc) == (
            "RuntimeError: DeepSeek API key not configured for lock inference."
        )
        assert error_status(exc) == 503
        generic = ValueError("bad payload at /Users/someone/prod/assure/prompt_matrix/x.py line 4")
        assert "/Users/" not in clean_error_message(generic)
        assert error_status(generic) == 500

    def test_sandbox_missing_key_is_503(self, app_client, monkeypatch):
        def _boom(_text):
            raise RuntimeError(
                "DeepSeek API key not configured for lock inference.\n"
                '  File "/app/.venv/lib/python3.11/site-packages/litellm/main.py", line 1'
            )

        monkeypatch.setattr("prompt_matrix.routers.sandbox.run_sandbox_verify", _boom)
        res = app_client.post("/api/sandbox/verify", json={"text": "Revenue is $1M."})
        assert res.status_code == 503
        body = res.get_json()
        assert body["ok"] is False
        assert body["error"] == "RuntimeError: DeepSeek API key not configured for lock inference."

    def test_sandbox_other_failure_stays_500_but_clean(self, app_client, monkeypatch):
        def _boom(_text):
            raise RuntimeError("solver crashed\n  File \"/app/z3.py\", line 9")

        monkeypatch.setattr("prompt_matrix.routers.sandbox.run_sandbox_verify", _boom)
        res = app_client.post("/api/sandbox/verify", json={"text": "x"})
        assert res.status_code == 500
        assert res.get_json()["error"] == "RuntimeError: solver crashed"

    def test_runs_missing_key_is_503(self, app_client, monkeypatch):
        def _boom(**_kwargs):
            raise RuntimeError(
                "DeepSeek is not connected. Paste your DEEPSEEK_API_KEY on the Connect page, "
                "or export it in your shell and restart pem."
            )

        monkeypatch.setattr("prompt_matrix.routers.runs_routes._create_run_with_timeout", _boom)
        res = app_client.post("/api/runs", json={"directive": "Summarise the deck."})
        assert res.status_code == 503
        body = res.get_json()
        assert body["ok"] is False
        assert body["error"].startswith("RuntimeError: DeepSeek is not connected.")

    def test_omp_recall_error_is_502(self, app_client, monkeypatch):
        monkeypatch.setattr(
            "prompt_matrix.routers.omp_routes.omp_recall",
            lambda _key: {"error": "<urlopen error [Errno 61] Connection refused>\nmore"},
        )
        res = app_client.get("/api/omp/recall?key=revenue")
        assert res.status_code == 502
        body = res.get_json()
        assert body["ok"] is False
        assert body["error"] == "<urlopen error [Errno 61] Connection refused>"

    def test_gap_analysis_unavailable_is_503(self, app_client, monkeypatch):
        from prompt_matrix.db.jdf_repository import save_jdf_revision

        doc = {
            "document_id": "d",
            "meta": {"project_id": "gapproj"},
            "truth_ledger": {},
            "body": [
                {
                    "type": "section",
                    "id": "s",
                    "title": "T",
                    "children": [
                        {
                            "type": "paragraph",
                            "id": "para-1",
                            "content": "Churn fell to 2% in Q3.",
                            "meta": {},
                            "annotations": {"redhat": [], "z3": []},
                        }
                    ],
                }
            ],
        }
        save_jdf_revision("gapproj", doc, mutation_type="TEST")
        monkeypatch.setattr(
            "prompt_matrix.routers.retrieval_routes.analyze_gap", lambda *_a, **_k: None
        )
        res = app_client.post("/api/projects/gapproj/nodes/para-1/gap-analysis", json={})
        assert res.status_code == 503
        body = res.get_json()
        assert body["ok"] is False
        assert body["reason"] == "gap_analysis_unavailable"
        assert body["error"]


# --------------------------------------------------------------------------- 7
class TestRedhatAutoWithoutBroker:
    def test_no_task_id_is_503_and_telemetry_error(self, app_client):
        from prompt_matrix.db.drafts_repository import upsert_draft
        from prompt_matrix.db.redhat_telemetry_repository import fetch_telemetry

        upsert_draft(
            workspace_id="founder",
            content={
                "document_id": "doc-f",
                "meta": {"project_id": "founder"},
                "truth_ledger": {},
                "body": [
                    {
                        "type": "section",
                        "id": "s",
                        "title": "T",
                        "children": [
                            {
                                "type": "paragraph",
                                "id": "p",
                                "content": "Liability capped at $1M.",
                                "meta": {},
                                "annotations": {"redhat": [], "z3": []},
                            }
                        ],
                        "meta": {},
                        "annotations": {"redhat": [], "z3": []},
                    }
                ],
            },
        )
        with patch(
            "prompt_matrix.routers.redhat_routes.schedule_redhat_multipass", return_value=None
        ):
            res = app_client.post("/api/projects/founder/redhat/auto", json={})
        assert res.status_code == 503
        body = res.get_json()
        assert body["ok"] is False
        assert body["error"] == "no task broker configured"
        assert body["status"]["state"] == "error"
        assert body["status"]["error"] == "no task broker configured"

        tel = fetch_telemetry("founder")
        assert tel["status"] == "error"
        assert tel["error"] == "no task broker configured"
        status = app_client.get("/api/projects/founder/redhat/status").get_json()
        assert status["status"]["state"] == "error"
        assert status["status"]["complete"] is False


# --------------------------------------------------------------------------- 8
class TestHealthHonesty:
    def test_configured_omp_down_is_degraded(self, app_client, monkeypatch):
        monkeypatch.setenv("OMP_SERVER", "http://omp.internal:3456")
        monkeypatch.setattr(
            "prompt_matrix.omp_client.omp_health",
            lambda: {"status": "down", "error": "<urlopen error timed out>\nstack"},
        )
        res = app_client.get("/health")
        assert res.status_code == 200
        body = res.get_json()
        assert body["ok"] is True
        assert body["status"] == "degraded"
        assert body["degraded"] is True
        assert body["checks"]["omp"] == "down"
        assert body["checks"]["omp_error"] == "<urlopen error timed out>"

    def test_configured_omp_up_is_healthy(self, app_client, monkeypatch):
        monkeypatch.setenv("OMP_SERVER", "http://omp.internal:3456")
        monkeypatch.setattr(
            "prompt_matrix.omp_client.omp_health", lambda: {"status": "ok", "version": "1.2"}
        )
        body = app_client.get("/health").get_json()
        assert body["status"] == "healthy"
        assert body["degraded"] is False
        assert body["checks"]["omp"] == "ok"
        assert body["checks"]["omp_version"] == "1.2"

    def test_unconfigured_omp_is_not_configured(self, app_client, monkeypatch, tmp_path):
        monkeypatch.delenv("OMP_SERVER", raising=False)
        monkeypatch.setattr("prompt_matrix.omp_client.API_KEY_PATH", str(tmp_path / "absent"))
        called: list[int] = []
        monkeypatch.setattr(
            "prompt_matrix.omp_client.omp_health", lambda: called.append(1) or {"status": "down"}
        )
        body = app_client.get("/health").get_json()
        assert body["status"] == "healthy"
        assert body["degraded"] is False
        assert body["checks"]["omp"] == "not configured"
        assert called == []


# --------------------------------------------------------------------------- 9
class TestGhostProjects:
    def test_get_jdf_and_export_404_for_unknown_project(self, app_client):
        res = app_client.get("/api/projects/ghost-xyz/jdf")
        assert res.status_code == 404
        assert res.get_json() == {"ok": False, "error": "Project not found."}
        res = app_client.get("/api/projects/ghost-xyz/export?format=json")
        assert res.status_code == 404
        assert res.get_json()["error"] == "Project not found."
        res = app_client.get("/api/projects/ghost-xyz/history")
        assert res.status_code == 404

    def test_put_still_creates_then_reads(self, app_client):
        doc = {"document_id": "d", "meta": {}, "truth_ledger": {}, "body": []}
        put = app_client.put(
            "/api/projects/fresh-one/jdf", json={"document": doc, "mutation_type": "TEST"}
        )
        assert put.status_code == 200, put.get_json()
        got = app_client.get("/api/projects/fresh-one/jdf")
        assert got.status_code == 200
        assert got.get_json()["ok"] is True

    def test_auto_created_ids_are_exempt(self, app_client):
        for pid in ("default", "founder", "sandbox"):
            res = app_client.get(f"/api/projects/{pid}/jdf")
            assert res.status_code == 200, (pid, res.get_json())

    def test_delete_unknown_project_404(self, app_client):
        res = app_client.delete("/api/projects/ghost-xyz")
        assert res.status_code == 404
