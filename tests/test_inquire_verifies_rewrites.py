"""A surgical rewrite is verified against the sources before it is shown (2026-09-27).

Until then ``routers/inquire_stream.run_inquire_pipeline`` persisted a rewritten
node with ``provenance: []`` and, when the text held no ``key: value`` figure,
emitted ``truth_check {status: PASS, detail: "Z3 Verified"}`` — a verdict for a
check that never ran. Now the rewritten node goes through the compile's lexical
anchoring and the entailment pass (the checker is injected here), carries
``meta.provenance``, and a text with no figures gets ``truth_check SKIPPED``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from prompt_matrix.routers import inquire_stream
from prompt_matrix.routers.inquire_stream import run_inquire_pipeline, verify_rewritten_node

SOURCE_ROWS = [
    {
        "id": "src-1",
        "filename": "renewal.pdf",
        "page_count": 2,
        "extracted_text": (
            "The renewal policy carries a 35% minimum earned premium. "
            "Coverage begins on the first of the month. "
            "The insured operates three warehouses in Ohio."
        ),
    }
]


def _tree(project_id: str) -> dict:
    return {
        "document_id": f"doc-{project_id}",
        "meta": {"project_id": project_id, "title": "Test Doc"},
        "truth_ledger": {},
        "body": [
            {
                "type": "section",
                "id": "sec-1",
                "title": "Overview",
                "children": [
                    {
                        "type": "paragraph",
                        "id": "p-1",
                        "content": "Old text about the premium.",
                        "entities_referenced": [],
                        "provenance": [],
                        "meta": {},
                        "annotations": {"redhat": [], "z3": []},
                    }
                ],
                "meta": {},
                "annotations": {"redhat": [], "z3": []},
            }
        ],
    }


def _yes_checker(claim: str, source: str, **_kw) -> dict:
    return {"verdict": "yes", "reasoning": f"source carries: {source[:40]}", "model": "fake-entailer"}


def test_rewritten_node_is_anchored_and_entailed(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    def checker(claim: str, source: str, **_kw) -> dict:
        calls.append((claim, source))
        return _yes_checker(claim, source)

    node = {
        "type": "paragraph",
        "id": "p-1",
        "content": "The renewal policy carries a 35% minimum earned premium.",
        "provenance": [],
        "meta": {},
        "annotations": {"redhat": [], "z3": []},
    }
    verified, info = verify_rewritten_node(
        "proj", node, substrate_rows=SOURCE_ROWS, entailment_checker=checker
    )
    assert info["anchored"] is True
    assert info["citations"] >= 1
    rows = verified["provenance"]
    assert rows and rows[0]["source_id"] == "src-1"
    assert rows[0]["source_name"] == "renewal.pdf"
    assert "35% minimum earned premium" in rows[0]["extracted_quote"]
    assert calls, "the injected entailment checker was not consulted"
    prov = verified["meta"]["provenance"]
    assert prov["excerpt"] == rows[0]["extracted_quote"]
    assert prov["entailment"]["verdict"] == "yes"
    assert prov["confidence"] is None
    assert info["entailment"] == "yes"


def test_unanchored_rewrite_says_so_and_calls_no_checker() -> None:
    def checker(*_a, **_k):
        raise AssertionError("no anchored sentence, so nothing to entail")

    node = {
        "type": "paragraph",
        "id": "p-1",
        "content": "Quarterly board dinner was held in Lisbon with the new investors.",
        "provenance": [],
        "meta": {},
        "annotations": {"redhat": [], "z3": []},
    }
    verified, info = verify_rewritten_node(
        "proj", node, substrate_rows=SOURCE_ROWS, entailment_checker=checker
    )
    assert verified["provenance"] == []
    assert info["anchored"] is False
    assert info["entailment"] is None
    assert "provenance" not in verified["meta"]


def test_rewrite_without_sources_is_unanchored_not_verified() -> None:
    node = {"type": "paragraph", "id": "p-1", "content": "Anything.", "provenance": [], "meta": {}}
    verified, info = verify_rewritten_node("proj", node, substrate_rows=[])
    assert verified["provenance"] == []
    assert info["sources"] == 0
    assert info["reason"]


class _FakeGovernor:
    def __init__(self, text: str) -> None:
        self._text = text
        self.recorded: list[dict] = []

    def preflight(self, *_a, **_k):
        return object()

    def execute_with_retry_budget(self, project_id, task_type, messages, **kwargs):
        build = kwargs.get("build_node_fn")
        node = build(self._text) if build else None
        return SimpleNamespace(
            ok=True,
            text=self._text,
            node=node,
            status="ok",
            error=None,
            input_tokens=10,
            output_tokens=5,
            retries=0,
            model_id="fake-model",
        )

    def record_usage(self, *args, **kwargs):
        self.recorded.append(kwargs)


def _events(frames) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for frame in frames:
        name = "message"
        data = ""
        for line in frame.splitlines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        if data:
            out.append((name, json.loads(data)))
    return out


@pytest.fixture()
def pipeline_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "inquire_verify.sqlite"))
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db

    init_db()


def test_stream_carries_provenance_and_skips_z3_without_figures(monkeypatch, pipeline_db) -> None:
    monkeypatch.setattr(inquire_stream, "_substrate_rows", lambda _pid: SOURCE_ROWS)
    gov = _FakeGovernor("The renewal policy carries a 35% minimum earned premium.")

    frames = list(
        run_inquire_pipeline(
            "proj-verify",
            user_intent="Restate the premium clause",
            target_node_id=None,
            run_redhat=False,
            document=_tree("proj-verify"),
            governor=gov,
            entailment_checker=_yes_checker,
        )
    )
    events = _events(frames)
    names = [n for n, _ in events]
    assert "jdf_node_ready" in names and "truth_check" in names

    truth = next(d for n, d in events if n == "truth_check")
    assert truth["status"] == "SKIPPED"
    assert truth["detail"] == "no figures to check"
    assert "Verified" not in json.dumps(truth)

    anchoring = next(d for n, d in events if n == "anchoring")
    assert anchoring["anchored"] is True

    ready = next(d for n, d in events if n == "jdf_node_ready")
    node = ready["node"]
    assert node["provenance"] and node["provenance"][0]["source_id"] == "src-1"
    assert node["meta"]["provenance"]["entailment"]["verdict"] == "yes"
    assert node["meta"]["provenance"]["confidence"] is None

    order = [names.index("anchoring"), names.index("jdf_node_ready")]
    assert order == sorted(order)


def test_stream_with_figures_still_runs_z3(monkeypatch, pipeline_db) -> None:
    monkeypatch.setattr(inquire_stream, "_substrate_rows", lambda _pid: [])
    gov = _FakeGovernor("revenue: 12000000 this quarter.")
    tree = _tree("proj-z3")
    tree["truth_ledger"] = {"revenue": 12_000_000}
    events = _events(
        run_inquire_pipeline(
            "proj-z3",
            user_intent="State revenue",
            run_redhat=False,
            document=tree,
            governor=gov,
        )
    )
    truth = next(d for n, d in events if n == "truth_check")
    assert truth["status"] == "PASS"
    assert truth["checked"] == 1
    assert truth["detail"] != "Z3 Verified"
