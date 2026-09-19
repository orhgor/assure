"""Copy that makes a claim about the product, checked against the product.

``prototype/about.html`` states what the shell does. Two of its sentences were
true when they were written and are not any more, and nothing reads the page
against the code, so a reader is told something the file contradicts:

* Q10 — "Red-Hat findings are not yet carried by the export". Since 9d03bab the
  export's Red-Hat section reads the project's own findings (the document's
  ``annotations.redhat`` and the ``redhat_findings`` rows); the section was empty
  before that only because every caller passed no list.
* Step 4 — "If the compile produces zero anchors — a mismatched source, for
  instance — the document is refused outright." The refusal is real; the
  parenthetical is not. A mismatched source does not reliably produce zero
  anchors: anchoring is lexical overlap, so a source that shares wording with the
  claim anchors it whatever the ask was about.

These are tests of the page, not of the product: each one proves the product
behaviour first, so the assertion is about the claim and not about wording.
"""

from __future__ import annotations

from pathlib import Path

import pytest

PROTOTYPE = Path(__file__).resolve().parents[1] / "prototype"


def _page() -> str:
    return (PROTOTYPE / "about.html").read_text(encoding="utf-8")


def _reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "about-truth.sqlite"))
    monkeypatch.setenv("WTF_CSRF_ENABLED", "0")
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db

    init_db()


def _tree(*, finding: str | None) -> dict:
    node = {
        "type": "paragraph",
        "id": "p1",
        "content": "The memo states the cyber exclusion without its carve-out.",
        "entities_referenced": [],
        "provenance": [],
        "meta": {},
    }
    if finding:
        node["annotations"] = {
            "redhat": [{"id": "crit-1", "node_id": "p1", "text": finding, "status": "open"}]
        }
    return {
        "document_id": "doc-redhat",
        "meta": {"title": "Red-Hat"},
        "truth_ledger": {},
        "body": [
            {
                "type": "section",
                "id": "sec-1",
                "title": "Coverage",
                "children": [node],
                "meta": {},
            }
        ],
    }


FINDING = (
    "The memo states cyber liability is excluded. The source states cyber liability is "
    "excluded except for data breach response costs up to $250,000. The memo omits the "
    "exception."
)


def test_the_export_carries_a_redhat_finding_the_document_records(tmp_path, monkeypatch) -> None:
    """The product behaviour the page's Q10 sentence denies.

    ``project_redhat_findings`` is what the export calls and what the page's claim
    is about, so the proof is taken through it.
    """
    _reset(tmp_path, monkeypatch)
    from prompt_matrix.db.redhat_findings_repository import insert_findings
    from prompt_matrix.db.redhat_telemetry_repository import upsert_telemetry
    from prompt_matrix.services.audit_bundle import (
        build_audit_bundle_html,
        project_redhat_findings,
    )

    from prompt_matrix.db.jdf_repository import ensure_project, save_jdf_revision

    ensure_project("redhat-truth", "Red-Hat")
    tree = _tree(finding=FINDING)
    save_jdf_revision("redhat-truth", tree, mutation_type="redhat")
    upsert_telemetry("redhat-truth", status="complete", findings=[])

    view = project_redhat_findings("redhat-truth", tree)
    assert view["count"] == 1
    assert view["ran"] is True
    assert view["items"][0]["message"] == FINDING

    html = build_audit_bundle_html("redhat-truth", tree)
    assert FINDING[:60] in html or "cyber liability" in html

    assert (
        "Red-Hat findings are not yet carried by the export" not in _page()
    ), "about.html still denies a finding the export carries (false since 9d03bab)"


def test_the_zero_anchor_refusal_is_not_attributed_to_a_mismatched_source() -> None:
    """A mismatched source is not what produces zero anchors — the ask is.

    Anchoring is lexical overlap, so a source about something else still anchors a
    paragraph that quotes it. The refusal belongs to a draft none of whose
    paragraphs cite anything, and the page's parenthetical names the wrong cause.
    """
    from prompt_matrix.models.jdf import attach_substrate_provenance_to_tree

    tree = _tree(finding=None)
    tree["body"][0]["children"][0]["content"] = (
        "The renewal increases the wind and hail deductible to twenty five thousand dollars."
    )
    # A source about a different subject that nevertheless shares the wording.
    rows = [
        {
            "id": "sub-1",
            "filename": "endorsement.pdf",
            "page_number": 1,
            "extracted_text": (
                "The renewal increases the wind and hail deductible to twenty five thousand "
                "dollars for the scheduled premises."
            ),
        }
    ]
    anchored = attach_substrate_provenance_to_tree(tree, [], rows)
    assert anchored["body"][0]["children"][0]["provenance"], (
        "a mismatched source anchored nothing, so this test cannot show the parenthetical is wrong"
    )

    assert "a mismatched source, for instance" not in _page(), (
        "about.html attributes the zero-anchor refusal to a mismatched source, "
        "which does not reliably produce zero anchors"
    )
