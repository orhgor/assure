"""Red-Hat runs after a compile without a click, and quotes only what the source says.

``ASSURE_REDHAT_AUTO`` (default on) schedules the multipass audit from the
compile stream's ``redhat`` frame (``routers/draft._redhat_auto_frame`` →
``routers/redhat_routes.schedule_redhat_after_compile``), so ``redhat/status``
moves idle → pending → complete. Findings that quote text the source does not
carry verbatim are dropped (``services/redhat_verbatim.verbatim_gate``).
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from prompt_matrix.routers import redhat_routes
from prompt_matrix.routers.draft import _redhat_auto_frame
from prompt_matrix.routers.redhat_routes import redhat_auto_enabled, schedule_redhat_after_compile
from prompt_matrix.services.redhat_verbatim import anchored_quotes, verbatim_gate


def _reset_db_path(monkeypatch, db_path) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()


@pytest.fixture()
def redhat_db(tmp_path, monkeypatch):
    _reset_db_path(monkeypatch, tmp_path / "redhat_auto.sqlite")
    from prompt_matrix.db.connection import init_db
    from prompt_matrix.db.jdf_repository import ensure_project

    init_db()
    ensure_project("founder", "Founder Workspace")
    return "founder"


def _doc(text: str, provenance: list[dict] | None = None) -> dict:
    return {
        "document_id": "doc-auto",
        "meta": {},
        "truth_ledger": {},
        "body": [
            {
                "type": "section",
                "id": "s1",
                "title": "Terms",
                "children": [
                    {
                        "type": "paragraph",
                        "id": "p1",
                        "content": text,
                        "provenance": provenance or [],
                        "meta": {},
                        "annotations": {"redhat": [], "z3": []},
                    }
                ],
                "meta": {},
                "annotations": {"redhat": [], "z3": []},
            }
        ],
    }


# --- env var ------------------------------------------------------------------


def test_redhat_auto_defaults_on(monkeypatch) -> None:
    monkeypatch.delenv("ASSURE_REDHAT_AUTO", raising=False)
    assert redhat_auto_enabled() is True
    monkeypatch.setenv("ASSURE_REDHAT_AUTO", "0")
    assert redhat_auto_enabled() is False
    monkeypatch.setenv("ASSURE_REDHAT_AUTO", "true")
    assert redhat_auto_enabled() is True


# --- scheduling ---------------------------------------------------------------


def test_schedule_after_compile_moves_telemetry_to_pending(redhat_db, monkeypatch) -> None:
    from prompt_matrix.db.redhat_telemetry_repository import fetch_telemetry

    with patch.object(redhat_routes, "schedule_redhat_multipass", return_value="task-auto-1") as sched:
        task_id = schedule_redhat_after_compile(redhat_db, None, current_jdf=_doc("Termination for $1M."))
    assert task_id == "task-auto-1"
    sched.assert_called_once()
    tel = fetch_telemetry(redhat_db)
    assert tel["status"] == "pending"
    assert tel["task_id"] == "task-auto-1"


def test_schedule_after_compile_runs_the_audit_under_eager_celery(redhat_db, monkeypatch) -> None:
    """With a broker configured and Celery eager (the test configuration), the
    scheduled task runs to completion and the telemetry says ``complete``."""
    from prompt_matrix import signals
    from prompt_matrix.db.redhat_telemetry_repository import fetch_telemetry

    pass1 = json.dumps([{"title": "P1", "content": "missing indemnity", "severity": "low"}])
    with (
        patch("prompt_matrix.celery_app.celery_broker_disabled", return_value=False),
        patch("prompt_matrix.tasks.redhat._invoke_model", return_value=(pass1, "fake-redhat")),
        patch.object(signals, "_revoke_task", lambda *_a, **_k: None),
    ):
        task_id = schedule_redhat_after_compile(redhat_db, None, current_jdf=_doc("Payment is due in 30 days."))
    assert task_id
    tel = fetch_telemetry(redhat_db)
    assert tel["status"] == "complete"
    assert tel["pass1_complete"] is True
    assert [f["title"] for f in tel["findings"]] == ["P1"]


def test_schedule_after_compile_without_broker_records_the_error(redhat_db) -> None:
    from prompt_matrix.db.redhat_telemetry_repository import fetch_telemetry

    with patch.object(redhat_routes, "schedule_redhat_multipass", return_value=None):
        task_id = schedule_redhat_after_compile(redhat_db, None, current_jdf=_doc("x y z"))
    assert task_id is None
    tel = fetch_telemetry(redhat_db)
    assert tel["status"] == "error"
    assert tel["error"] == "no task broker configured"


def test_schedule_after_compile_with_nothing_to_audit(redhat_db) -> None:
    with patch.object(redhat_routes, "schedule_redhat_multipass") as sched:
        assert schedule_redhat_after_compile(redhat_db, None, current_jdf={"body": []}) is None
        sched.assert_not_called()


# --- the compile stream's redhat frame ------------------------------------------


def test_compile_frame_is_scheduled_with_task_id(redhat_db, monkeypatch) -> None:
    monkeypatch.delenv("ASSURE_REDHAT_AUTO", raising=False)
    with patch.object(redhat_routes, "schedule_redhat_multipass", return_value="task-frame-1"):
        payload = _redhat_auto_frame(redhat_db, "Termination and liability for $1M.", {}, "memo")
    assert payload["status"] == "scheduled"
    assert payload["task_id"] == "task-frame-1"
    assert payload["findings_count"] == 0
    assert payload["skip_reason"] is None


def test_compile_frame_is_skipped_when_auto_is_off(redhat_db, monkeypatch) -> None:
    monkeypatch.setenv("ASSURE_REDHAT_AUTO", "0")
    with patch.object(redhat_routes, "schedule_redhat_multipass") as sched:
        payload = _redhat_auto_frame(redhat_db, "Termination for $1M.", {}, "memo")
        sched.assert_not_called()
    assert payload["status"] == "skipped"
    assert "ASSURE_REDHAT_AUTO" in payload["skip_reason"]
    assert payload["task_id"] is None


def test_compile_frame_never_says_ran(redhat_db, monkeypatch) -> None:
    monkeypatch.delenv("ASSURE_REDHAT_AUTO", raising=False)
    with patch.object(redhat_routes, "schedule_redhat_multipass", return_value=None):
        payload = _redhat_auto_frame(redhat_db, "Termination for $1M.", {}, "memo")
    assert payload["status"] == "skipped"
    assert "broker" in payload["skip_reason"]


# --- verbatim quotes -------------------------------------------------------------

SOURCE = "The renewal policy carries a 35% minimum earned premium."


def test_gate_drops_a_finding_whose_quote_is_not_in_the_source() -> None:
    kept, notes = verbatim_gate(
        [{"title": "Premium", "content": "Source says a 25% premium.", "quote": "a 25% minimum earned premium"}],
        [SOURCE],
    )
    assert kept == []
    assert notes and "not found verbatim" in notes[0]


def test_gate_keeps_a_verbatim_quote_and_marks_it() -> None:
    kept, notes = verbatim_gate(
        [{"title": "Premium", "content": "The clause reads “35%  minimum earned PREMIUM”.", "severity": "low"}],
        [SOURCE],
    )
    assert notes == []
    assert kept[0]["quote"] == "35% minimum earned premium"
    assert kept[0]["quote_verbatim"] is True


def test_gate_passes_a_finding_that_quotes_nothing() -> None:
    kept, notes = verbatim_gate([{"title": "Style", "content": "Passive voice throughout."}], [SOURCE])
    assert kept[0]["quote"] is None
    assert kept[0]["quote_verbatim"] is False
    assert kept[0]["evidence_kind"] == "observation"
    # The empty quote is counted, not treated as a drop.
    assert notes == ["1 finding(s) returned no quote and are recorded as observations, not evidence"]


def test_pass1_prompt_carries_every_anchored_quote_and_drops_invented_ones(redhat_db) -> None:
    from prompt_matrix.lib.ast_diff import get_ast_deltas
    from prompt_matrix.tasks.redhat import _scrutinizer_prompt, run_redhat_pass1

    provenance = [
        {"source_id": "s1", "source_name": "renewal.pdf", "page_number": 2, "extracted_quote": SOURCE},
        {"source_id": "s1", "source_name": "renewal.pdf", "page_number": 3, "extracted_quote": "Coverage begins on the first of the month."},
    ]
    delta = get_ast_deltas(_doc("The premium is 35% and coverage starts monthly.", provenance), None)[0]
    prompt = _scrutinizer_prompt(delta)
    assert SOURCE in prompt
    assert "Coverage begins on the first of the month." in prompt
    assert len(anchored_quotes(delta["node"])) == 2

    answer = json.dumps(
        [
            {"title": "Kept", "content": "ok", "severity": "low", "quote": "Coverage begins on the first of the month."},
            {"title": "Invented", "content": "bad", "severity": "high", "quote": "Coverage begins next year."},
        ]
    )
    with patch("prompt_matrix.tasks.redhat._invoke_model", return_value=(answer, "fake")):
        result = run_redhat_pass1([delta], redhat_db, run_id=None)
    titles = [f["title"] for f in result["findings"]]
    assert titles == ["Kept"]
    assert result["findings"][0]["quote_verbatim"] is True
    assert any("Invented" in n for n in result["notes"])


# --- evidence_kind: every finding says what it stands on -------------------------


def test_gate_labels_quoted_and_observation_findings() -> None:
    kept, notes = verbatim_gate(
        [
            {"title": "Quoted", "content": "x", "quote": SOURCE},
            {"title": "Observed", "content": "Passive voice.", "quote": ""},
            {"title": "Invented", "content": "y", "quote": "a 25% minimum earned premium"},
        ],
        [SOURCE],
    )
    by_title = {f["title"]: f for f in kept}
    assert set(by_title) == {"Quoted", "Observed"}
    assert by_title["Quoted"]["evidence_kind"] == "quoted"
    assert by_title["Quoted"]["quote_verbatim"] is True
    assert by_title["Observed"]["evidence_kind"] == "observation"
    assert by_title["Observed"]["quote"] is None
    assert any("1 finding(s) returned no quote" in n for n in notes)
    assert any("Invented" in n for n in notes)


def test_multipass_prompts_require_a_quote_key() -> None:
    from prompt_matrix.lib.ast_diff import get_ast_deltas
    from prompt_matrix.tasks.redhat import _adversarial_prompt, _scrutinizer_prompt

    delta = get_ast_deltas(_doc("Payment is due in 30 days."), None)[0]
    assert 'MUST include a "quote" key' in _scrutinizer_prompt(delta)
    assert 'MUST include a "quote" key' in _adversarial_prompt("h", [], "text", [SOURCE])


# --- a superseded pass never touches the newer generation's telemetry -------------


def test_stale_pass_leaves_telemetry_alone(redhat_db) -> None:
    from prompt_matrix.db.redhat_audit_lock_repository import bump_generation
    from prompt_matrix.db.redhat_telemetry_repository import fetch_telemetry, reset_telemetry
    from prompt_matrix.tasks.redhat import run_redhat_multipass_audit

    old_gen, _ = bump_generation(redhat_db)
    bump_generation(redhat_db)  # a newer compile scheduled its own audit
    reset_telemetry(redhat_db, task_id="newer-task")

    with patch("prompt_matrix.tasks.redhat._invoke_model") as llm:
        result = run_redhat_multipass_audit(
            redhat_db, _doc("Termination for $1M."), None, generation=old_gen
        )
        llm.assert_not_called()
    assert result == {"ok": False, "stale": True}
    tel = fetch_telemetry(redhat_db)
    assert tel["status"] == "pending"
    assert tel["task_id"] == "newer-task"
    assert tel["error"] in ("", None)


def test_pass_superseded_mid_run_writes_nothing_after(redhat_db) -> None:
    from prompt_matrix.db.redhat_audit_lock_repository import bump_generation
    from prompt_matrix.db.redhat_telemetry_repository import fetch_telemetry, reset_telemetry
    from prompt_matrix.tasks.redhat import run_redhat_multipass_audit

    gen, _ = bump_generation(redhat_db)
    reset_telemetry(redhat_db, task_id="this-task")

    def superseded_model(*_a, **_k):
        # A newer compile lands while Pass 1 is at the model.
        bump_generation(redhat_db)
        return json.dumps([{"title": "Late", "content": "late finding", "severity": "low", "quote": ""}]), "fake"

    with patch("prompt_matrix.tasks.redhat._invoke_model", side_effect=superseded_model):
        result = run_redhat_multipass_audit(
            redhat_db, _doc("Payment is due in 30 days."), None, generation=gen
        )
    assert result.get("stale") is True or result.get("ok") is True
    tel = fetch_telemetry(redhat_db)
    assert tel["status"] not in ("complete", "error")
    assert tel["findings"] == []
    assert tel["error"] in ("", None)
