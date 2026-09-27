"""Multi-pass Red-Hat audit — AST diff, cache, and tiered pipeline tests."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from prompt_matrix.db.connection import init_db
from prompt_matrix.db.redhat_cache_repository import fetch_cache, save_cache
from prompt_matrix.history import get_db
from prompt_matrix.lib.ast_diff import get_ast_deltas, hash_block
from prompt_matrix.tasks.redhat import (
    PASS1_MODEL,
    run_redhat_multipass_audit,
    run_redhat_pass1,
    run_redhat_pass2,
    should_run_pass2,
    synthesize_redhat_findings,
)


def _paragraph(node_id: str, text: str) -> dict:
    return {
        "type": "paragraph",
        "id": node_id,
        "content": text,
        "entities_referenced": [],
        "provenance": [],
        "meta": {},
        "annotations": {"redhat": [], "z3": []},
    }


def _doc(*children: dict, section_id: str = "sec-1", title: str = "Section") -> dict:
    return {
        "document_id": "doc-test",
        "meta": {"project_id": "founder"},
        "truth_ledger": {},
        "body": [
            {
                "type": "section",
                "id": section_id,
                "title": title,
                "children": list(children),
                "meta": {},
                "annotations": {"redhat": [], "z3": []},
            }
        ],
    }


def test_hash_block_stable_and_annotation_ignored():
    node = _paragraph("p1", "Revenue reached $12M.")
    clone = _paragraph("p1", "Revenue reached $12M.")
    clone["annotations"]["redhat"] = [{"id": "rh-1", "text": "note", "status": "open"}]
    assert hash_block(node) == hash_block(clone)


def test_get_ast_deltas_added_modified_deleted():
    previous = _doc(_paragraph("p1", "Original text."))
    current = _doc(
        _paragraph("p1", "Updated text."),
        _paragraph("p2", "Brand new block."),
    )
    deltas = get_ast_deltas(current, previous)
    by_id = {d["node_id"]: d for d in deltas}
    assert by_id["p1"]["change"] == "modified"
    assert by_id["p1"]["parent"]["section_title"] == "Section"
    assert by_id["p2"]["change"] == "added"

    reverse = get_ast_deltas(previous, current)
    deleted = [d for d in reverse if d["change"] == "deleted"]
    assert any(d["node_id"] == "p2" for d in deleted)


def test_should_run_pass2_gating():
    assert should_run_pass2([{"severity": "high", "content": "ok"}], "plain text") is True
    assert should_run_pass2([{"severity": "low", "content": "ok"}], "indemnify clause") is True
    assert should_run_pass2([{"severity": "low", "content": "ok"}], "routine summary") is False


def test_synthesize_redhat_findings_dedupes_and_ranks():
    merged = synthesize_redhat_findings(
        [
            {"title": "Low", "content": "minor", "severity": "low"},
            {"title": "High", "content": "critical", "severity": "high"},
        ],
        [
            {"title": "High", "content": "critical", "severity": "high"},
            {"title": "Med", "content": "watch", "severity": "medium"},
        ],
    )
    assert len(merged) == 3
    assert merged[0]["severity"] == "high"


def test_redhat_cache_roundtrip():
    init_db()
    block_hash = "abc123" + "0" * 58
    save_cache(
        block_hash,
        [{"title": "Cached", "content": "from cache", "severity": "medium"}],
        pass1_model=PASS1_MODEL,
    )
    cached = fetch_cache(block_hash)
    assert cached is not None
    assert cached["findings"][0]["title"] == "Cached"
    db = get_db()
    db.execute("DELETE FROM redhat_cache WHERE block_hash = ?", (block_hash,))
    db.commit()


def _reset_db_path(monkeypatch, db_path):
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()


@pytest.fixture
def founder_run(tmp_path, monkeypatch):
    _reset_db_path(monkeypatch, tmp_path / "redhat_multipass.sqlite")
    init_db()
    db = get_db()
    # runs.workspace_id references projects(id) (PostgreSQL DDL): the project
    # row must exist before a run is written for it.
    from prompt_matrix.db.jdf_repository import ensure_project

    ensure_project("founder", "Founder Workspace")
    db.execute(
        """
        INSERT INTO runs (id, workspace_id, directive, content, model, sources_used,
                          extracted_locks, status, created_at, updated_at)
        VALUES ('run_mp_test', 'founder', 'test', '{}', 'gemini', '[]', '[]', 'stamped',
                datetime('now'), datetime('now'))
        """
    )
    db.commit()
    return "run_mp_test"


def test_run_redhat_pass1_cache_hit_skips_llm(founder_run):
    delta = get_ast_deltas(
        _doc(_paragraph("p-cache", "Cached liability clause.")),
        _doc(_paragraph("p-cache", "Old text.")),
    )[0]
    save_cache(
        delta["block_hash"],
        [{"title": "Cached gap", "content": "Missing cite", "severity": "medium"}],
        pass1_model=PASS1_MODEL,
    )

    with patch("prompt_matrix.tasks.redhat._invoke_model") as mock_llm:
        result = run_redhat_pass1([delta], "founder", run_id=founder_run)
        mock_llm.assert_not_called()

    assert result["ok"] is True
    assert result["blocks"][0]["cached"] is True
    assert result["findings"][0]["title"] == "Cached gap"


def test_run_redhat_pass2_skipped_without_triggers(founder_run):
    findings = [{"title": "Minor", "content": "style", "severity": "low"}]
    with patch("prompt_matrix.tasks.redhat._invoke_model") as mock_llm:
        result = run_redhat_pass2(
            "hash-low",
            findings,
            "founder",
            block_text="benign operational summary",
            run_id=founder_run,
        )
        mock_llm.assert_not_called()
    assert result["skipped"] is True
    assert result["findings"] == []


def test_run_redhat_pass2_runs_for_high_severity(founder_run):
    findings = [{"title": "Critical", "content": "contradiction", "severity": "high"}]
    llm_payload = json.dumps(
        [{"title": "Stress", "content": "deeper issue", "severity": "high", "suggested_fix": ""}]
    )
    with patch(
        "prompt_matrix.tasks.redhat._invoke_model",
        return_value=(llm_payload, "anthropic/claude-sonnet-4-5"),
    ):
        result = run_redhat_pass2(
            "hash-high",
            findings,
            "founder",
            block_text="critical clause",
            run_id=founder_run,
            pass1_model=PASS1_MODEL,
        )
    assert result["skipped"] is False
    assert result["findings"][0]["title"] == "Stress"


def test_run_redhat_multipass_orchestration(founder_run):
    current = _doc(_paragraph("p-multi", "Termination and liability for $1M."))
    previous = _doc(_paragraph("p-multi", "Old termination terms."))
    pass1_json = json.dumps(
        [{"title": "P1", "content": "missing indemnity", "severity": "high", "suggested_fix": ""}]
    )
    pass2_json = json.dumps(
        [{"title": "P2", "content": "cross-exam gap", "severity": "high", "suggested_fix": ""}]
    )

    with patch(
        "prompt_matrix.tasks.redhat._invoke_model",
        side_effect=[(pass1_json, PASS1_MODEL), (pass2_json, "anthropic/claude-sonnet-4-5")],
    ):
        result = run_redhat_multipass_audit(
            "founder",
            current,
            previous,
            run_id=founder_run,
        )

    assert result["ok"] is True
    assert result["deltas"] >= 1
    titles = {f["title"] for f in result["findings"]}
    assert "P1" in titles or "P2" in titles


def test_pass1_findings_carry_evidence_kind(founder_run):
    """Every finding says what it stands on: ``quoted`` with a verbatim source
    quote, ``observation`` without one (2026-09-27)."""
    node = _paragraph("p-kind", "The premium is 35%.")
    node["provenance"] = [
        {"source_id": "s1", "source_name": "renewal.pdf", "extracted_quote": "The renewal policy carries a 35% minimum earned premium."}
    ]
    delta = get_ast_deltas(_doc(node), None)[0]
    answer = json.dumps(
        [
            {"title": "Q", "content": "figure", "severity": "low", "quote": "The renewal policy carries a 35% minimum earned premium."},
            {"title": "O", "content": "style", "severity": "low", "quote": ""},
        ]
    )
    with patch("prompt_matrix.tasks.redhat._invoke_model", return_value=(answer, PASS1_MODEL)):
        result = run_redhat_pass1([delta], "founder", run_id=founder_run)
    kinds = {f["title"]: f["evidence_kind"] for f in result["findings"]}
    assert kinds == {"Q": "quoted", "O": "observation"}
    assert any("returned no quote" in n for n in result["notes"])


def test_cached_findings_without_the_label_are_observations(founder_run):
    delta = get_ast_deltas(_doc(_paragraph("p-old", "Old cached clause.")), None)[0]
    save_cache(
        delta["block_hash"],
        [{"title": "Legacy", "content": "written before evidence_kind", "severity": "medium"}],
        pass1_model=PASS1_MODEL,
    )
    with patch("prompt_matrix.tasks.redhat._invoke_model") as llm:
        result = run_redhat_pass1([delta], "founder", run_id=founder_run)
        llm.assert_not_called()
    assert result["findings"][0]["evidence_kind"] == "observation"
