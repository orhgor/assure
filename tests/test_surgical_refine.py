"""Surgical click-to-fix refine-node loop."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from prompt_matrix.i18n import CATALOGS, LOCALES
from prompt_matrix.services.refine_node import (
    apply_refined_text,
    neighbor_context_from_tree,
    run_refine_node,
)

ROOT = Path(__file__).resolve().parents[1]

KEYS = (
    "surgical.click.title",
    "surgical.click.refine_ai",
    "surgical.click.ground_vault",
    "surgical.click.instruction_label",
    "surgical.click.instruction_placeholder",
    "surgical.click.apply",
    "surgical.click.cancel",
    "surgical.click.failed",
    "surgical.click.done",
    "surgical.click.no_node",
    "surgical.click.vault_instruction",
)

DOC = {
    "document_id": "doc-1",
    "meta": {},
    "truth_ledger": {"Revenue": 12_000_000},
    "body": [
        {
            "type": "section",
            "id": "s1",
            "title": "Overview",
            "children": [
                {
                    "type": "paragraph",
                    "id": "p0",
                    "content": "Intro sentence.",
                    "annotations": {"redhat": [], "z3": []},
                },
                {
                    "type": "paragraph",
                    "id": "p1",
                    "content": "Revenue reached 12 million. The moon is cheese.",
                    "annotations": {"redhat": [], "z3": []},
                },
                {
                    "type": "paragraph",
                    "id": "p2",
                    "content": "Closing sentence.",
                    "annotations": {"redhat": [], "z3": []},
                },
            ],
        }
    ],
}


def test_surgical_i18n_keys() -> None:
    for locale in LOCALES:
        cat = CATALOGS[locale]
        for key in KEYS:
            assert key in cat, f"missing {locale} {key}"
            assert str(cat[key]).strip()
    assert CATALOGS["en"]["surgical.click.refine_ai"] == "✏️ Polish"
    assert CATALOGS["tr"]["surgical.click.refine_ai"] == "YZ ile iyileştir"


def test_surgical_markup_and_js() -> None:
    html = (ROOT / "prompt_matrix" / "templates" / "index.html").read_text(encoding="utf-8")
    assert 'id="jdf-surgical-popover"' in html
    assert 'id="surgical-refine-ai-btn"' in html
    # v1.3 replaced the vault button with the Ground sandwich menu
    assert 'id="surgical-ground-wrap"' in html
    assert 'id="surgical-ground-btn"' in html
    assert "surgical_click.js" in html
    js = (ROOT / "prompt_matrix" / "static" / "surgical_click.js").read_text(encoding="utf-8")
    assert "/refine-node" in js
    assert "skipViewSwitch: true" in js
    canvas = (ROOT / "prompt_matrix" / "static" / "jdf_canvas.js").read_text(encoding="utf-8")
    assert "applyRefinedNode" in canvas
    tiptap = (ROOT / "prompt_matrix" / "static" / "jdf_tiptap.js").read_text(encoding="utf-8")
    assert "AssureSurgicalClick" in tiptap


def test_neighbor_context_n_minus_one_plus_one() -> None:
    ctx = neighbor_context_from_tree(DOC, "p1")
    assert ctx["preceding"]["id"] == "p0"
    assert ctx["succeeding"]["id"] == "p2"
    assert ctx["target"]["id"] == "p1"


@pytest.mark.skipif(
    os.environ.get("CI") == "true",
    reason="Z3 intermittently segfaults on GitHub Actions Python 3.11",
)
def test_apply_refined_text_reruns_z3_on_node() -> None:
    result = apply_refined_text(DOC, "p1", "Revenue reached 12 million.")
    assert result["node"]["id"] == "p1"
    assert "moon" not in result["node"]["content"]
    assert result["z3_results"]["status"] == "PASS"
    node_spans = result["nodeSpans"]
    assert node_spans
    assert all(s["nodeId"] == "p1" for s in node_spans)
    assert any(s["score"] > 0.1 for s in node_spans)


@pytest.mark.skipif(
    os.environ.get("CI") == "true",
    reason="Z3 intermittently segfaults on GitHub Actions Python 3.11",
)
def test_run_refine_node_returns_only_updated_node(monkeypatch) -> None:
    gov = MagicMock()
    gov.preflight.return_value = None
    gov.execute_with_retry_budget.return_value = type(
        "R",
        (),
        {"text": "Revenue reached 12 million, locked to the ledger.", "ok": True},
    )()
    monkeypatch.setattr(
        "prompt_matrix.llm.orchestrator.orchestrate_node_compilation_sync",
        lambda *_a, **_k: "",
    )
    out = run_refine_node(
        "proj-x",
        node_id="p1",
        user_instruction="Rewrite this to be more neutral",
        document=DOC,
        gov=gov,
        persist=False,
    )
    assert out["ok"] is True
    assert out["node"]["id"] == "p1"
    assert "12 million" in out["node"]["content"]
    others = [
        child["content"] for child in out["document"]["body"][0]["children"] if child["id"] != "p1"
    ]
    assert others == ["Intro sentence.", "Closing sentence."]
    gov.execute_with_retry_budget.assert_called_once()


def _convicted_doc() -> dict:
    """``DOC`` with the middle paragraph convicted by Red-Hat."""
    doc = json.loads(json.dumps(DOC))
    para = doc["body"][0]["children"][1]
    para["provenance"] = [
        {
            "source_type": "internal_doc",
            "source_name": "ledger.pdf",
            "extracted_quote": "Revenue reached 12 million.",
            "url_or_doi": "",
            "source_id": "",
            "page_number": "1",
            "accessed_date": "",
        }
    ]
    para["meta"] = {"provenance": {"entailment": {"verdict": "no", "contradicted": False}}}
    para["annotations"] = {
        "redhat": [
            {
                "id": "crit-open",
                "node_id": "p1",
                "text": "The claim is not supported as written.",
                "status": "open",
            }
        ],
        "z3": [],
    }
    return doc


@pytest.mark.skipif(
    os.environ.get("CI") == "true",
    reason="Z3 intermittently segfaults on GitHub Actions Python 3.11",
)
def test_a_refined_node_keeps_the_finding_it_closes(tmp_path, monkeypatch) -> None:
    """Refine rewrites the same paragraph the stream does, so it closes findings too.

    ``run_refine_node`` used to write the rewritten node with its finding left
    exactly as it found it — the paragraph was replaced and the warning on it
    stopped meaning anything (nothing read ``status``). Both rewrite paths now
    mark it ``resolved`` and name the revision, through the same helper.
    """
    import sqlite3

    from prompt_matrix.db import jdf_repository
    from prompt_matrix.db.connection import init_db
    from prompt_matrix.db.jdf_repository import fetch_latest_jdf, save_jdf_revision

    db_path = tmp_path / "history.sqlite"

    def _getter():
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr("prompt_matrix.history.DB_PATH", db_path)
    for target in (
        "prompt_matrix.db.connection.get_db",
        "prompt_matrix.db.jdf_repository.get_db",
        "prompt_matrix.history.get_db",
    ):
        monkeypatch.setattr(target, _getter)
    init_db(_getter())
    save_jdf_revision("proj-refine", _convicted_doc(), mutation_type="seed")

    gov = MagicMock()
    gov.preflight.return_value = None
    gov.execute_with_retry_budget.return_value = type(
        "R", (), {"text": "The ledger states revenue of 12 million.", "ok": True}
    )()
    monkeypatch.setattr(
        "prompt_matrix.llm.orchestrator.orchestrate_node_compilation_sync",
        lambda *_a, **_k: "",
    )
    out = run_refine_node(
        "proj-refine",
        node_id="p1",
        user_instruction="Rewrite this so it states only what the ledger shows",
        document=_convicted_doc(),
        gov=gov,
        persist=True,
    )

    finding = out["node"]["annotations"]["redhat"][0]
    assert finding["status"] == "resolved"
    assert finding["resolved_by_mutation_type"] == "surgical_refine"
    assert finding["resolved_by_version"] == 2
    assert finding["resolved_by_revision_id"]
    assert finding["prior_anchor_quote"] == "Revenue reached 12 million."
    assert finding["prior_verdict"] == "no"
    assert "not supported as written" in finding["text"]

    stored = jdf_repository.fetch_latest_jdf("proj-refine")
    persisted = stored["body"][0]["children"][1]["annotations"]["redhat"][0]
    assert persisted["status"] == "resolved"
    assert persisted["resolved_by_revision_id"] == finding["resolved_by_revision_id"]
    assert persisted["resolved_by_version"] == 2
