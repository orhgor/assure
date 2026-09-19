"""Async SSE integration tests for inquire stream."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest
from asgiref.wsgi import WsgiToAsgi
from httpx import ASGITransport, AsyncClient

from prompt_matrix.cost_governance import QuotaExceededError


def _sample_tree(project_id: str = "test-proj") -> dict:
    return {
        "document_id": f"doc-{project_id}",
        "meta": {"project_id": project_id, "title": "Test Doc"},
        "truth_ledger": {"revenue": 12_000_000},
        "body": [
            {
                "type": "section",
                "id": "sec-1",
                "title": "Overview",
                "children": [
                    {
                        "type": "paragraph",
                        "id": "p-1",
                        "content": "Revenue is $12M this quarter.",
                        "entities_referenced": ["revenue"],
                        "meta": {},
                        "annotations": {"redhat": [], "z3": []},
                    }
                ],
                "meta": {},
                "annotations": {"redhat": [], "z3": []},
            }
        ],
    }


def _mock_result(**overrides):
    base = {
        "ok": True,
        "text": "Revenue is $12M this quarter, verified.",
        "node": {
            "type": "paragraph",
            "id": "p-1",
            "content": "Revenue is $12M this quarter, verified.",
            "entities_referenced": [],
            "meta": {},
            "annotations": {"redhat": [], "z3": []},
        },
        "status": "ok",
        "error": None,
        "input_tokens": 10,
        "output_tokens": 5,
        "retries": 0,
        "model_id": "anthropic.claude-3-5-haiku-20241022-v1:0",
    }
    base.update(overrides)
    return type("R", (), base)()


async def _collect_events(client, project_id: str, payload: dict) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    async with client.stream(
        "POST",
        f"/api/projects/{project_id}/inquire/stream",
        json=payload,
    ) as response:
        assert response.status_code == 200
        buffer = ""
        async for chunk in response.aiter_text():
            buffer += chunk
            while "\n\n" in buffer:
                block, buffer = buffer.split("\n\n", 1)
                if not block.strip():
                    continue
                event_name = "message"
                data_raw = ""
                for line in block.split("\n"):
                    if line.startswith("event:"):
                        event_name = line[6:].strip()
                    if line.startswith("data:"):
                        data_raw += line[5:].strip()
                if data_raw:
                    events.append((event_name, json.loads(data_raw)))
    return events


async def _seed_project(client, project_id: str) -> None:
    """Create the project row the stream's compile counter references.

    ``daily_compile_limits.project_id`` carries an FK to ``projects(id)``, so a
    stream for a project that was never created fails the counter insert — the
    pre-FK suite never needed the row. Seed it through the app's own path.
    """
    response = await client.put(
        f"/api/projects/{project_id}/jdf",
        json={"document": _sample_tree(project_id), "mutation_type": "seed"},
    )
    assert response.status_code == 200


@pytest.fixture
def temp_db():
    tmp = tempfile.TemporaryDirectory()
    db_path = Path(tmp.name) / "history.sqlite"

    def _getter():
        import sqlite3

        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    with (
        patch("prompt_matrix.history.DB_PATH", db_path),
        patch("prompt_matrix.db.connection.get_db", side_effect=_getter),
        patch("prompt_matrix.cost_governance.get_db", side_effect=_getter),
        patch("prompt_matrix.db.jdf_repository.get_db", side_effect=_getter),
    ):
        conn = _getter()
        yield conn
        conn.close()
        tmp.cleanup()


@pytest.fixture
async def client(temp_db):
    from prompt_matrix.db.connection import init_db
    from prompt_matrix.web import create_app

    init_db(temp_db)
    app = create_app(require_auth=False)
    transport = ASGITransport(app=WsgiToAsgi(app))
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest.mark.asyncio
async def test_full_stream_lifecycle(client):
    await _seed_project(client, "lifecycle")

    with patch("prompt_matrix.routers.inquire_stream.CostGovernor") as Gov:
        instance = Gov.return_value
        instance.preflight.return_value = object()
        instance.execute_with_retry_budget.side_effect = [
            _mock_result(),
            _mock_result(
                text="Critique: numbers look consistent.",
                node={
                    "type": "callout",
                    "id": "c-red",
                    "variant": "adversarial_redhat",
                    "title": "Red-hat",
                    "content": "Critique: numbers look consistent.",
                },
                model_id="anthropic.claude-3-5-sonnet-20240620-v1:0",
            ),
        ]

        events = await _collect_events(
            client,
            "lifecycle",
            {
                "user_intent": "Verify revenue",
                "target_node_id": "p-1",
                "run_redhat": True,
                "document": _sample_tree("lifecycle"),
            },
        )
        names = [name for name, _ in events]
        for expected in (
            "status",
            "token",
            "truth_check",
            "redhat_annotation",
            "jdf_node_ready",
            "usage",
            "complete",
        ):
            assert expected in names
        order = [
            names.index(e)
            for e in ("status", "token", "truth_check", "jdf_node_ready", "usage", "complete")
        ]
        assert order == sorted(order)


@pytest.mark.asyncio
async def test_surgical_diff_context(client):
    await _seed_project(client, "surgical")

    with patch("prompt_matrix.routers.inquire_stream.CostGovernor") as Gov:
        instance = Gov.return_value
        instance.preflight.return_value = object()
        instance.execute_with_retry_budget.return_value = _mock_result()

        events = await _collect_events(
            client,
            "surgical",
            {
                "user_intent": "Tighten wording",
                "target_node_id": "p-1",
                "run_redhat": False,
                "document": _sample_tree("surgical"),
            },
        )
        ready = next(data for name, data in events if name == "jdf_node_ready")
        assert ready.get("is_mutation") is True
        assert ready.get("original_content")
        assert ready.get("target_node_id") == "p-1"


@pytest.mark.asyncio
async def test_z3_violation_event(client):
    await _seed_project(client, "z3")

    with patch("prompt_matrix.routers.inquire_stream.CostGovernor") as Gov:
        instance = Gov.return_value
        instance.preflight.return_value = object()
        instance.execute_with_retry_budget.return_value = _mock_result(
            ok=False,
            text="Revenue is $15M this quarter.",
            status="VALIDATION_FAILED",
            error="Metric 'revenue'=15000000 contradicts locked == 12000000.0",
        )

        events = await _collect_events(
            client,
            "z3",
            {
                "user_intent": "Change revenue",
                "target_node_id": "p-1",
                "run_redhat": False,
                "document": _sample_tree("z3"),
                "incoming_metrics": [["revenue", 12_000_000]],
            },
        )
        truth = next(data for name, data in events if name == "truth_check")
        assert truth.get("status") == "VIOLATION"
        assert truth.get("detail") == "Z3 Conflict"


@pytest.mark.asyncio
async def test_quota_429(client):
    await _seed_project(client, "quota")

    with patch("prompt_matrix.routers.inquire_stream.CostGovernor") as Gov:
        instance = Gov.return_value
        instance.preflight.side_effect = QuotaExceededError("quota exceeded")

        events = await _collect_events(
            client,
            "quota",
            {"user_intent": "test", "run_redhat": False, "document": _sample_tree("quota")},
        )
        complete = next(data for name, data in events if name == "complete")
        assert complete.get("http_status") == 429


@pytest.mark.asyncio
async def test_jdf_get_put_roundtrip(client):
    tree = _sample_tree("roundtrip")
    put = await client.put(
        "/api/projects/roundtrip/jdf", json={"document": tree, "mutation_type": "seed"}
    )
    assert put.status_code == 200
    body = put.json()
    assert body.get("ok") is True
    assert body.get("document")

    got = await client.get("/api/projects/roundtrip/jdf")
    assert got.status_code == 200
    payload = got.json()
    assert payload["document"]["document_id"] == tree["document_id"]


@pytest.mark.asyncio
async def test_export_docx(client):
    tree = _sample_tree("export-me")
    await client.put(
        "/api/projects/export-me/jdf", json={"document": tree, "mutation_type": "seed"}
    )
    resp = await client.get("/api/projects/export-me/export?format=docx")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert len(resp.content) > 100


class ResolveDocumentTests(unittest.TestCase):
    def test_payload_overrides_database(self):
        from prompt_matrix.models.jdf import JDFDocumentTree
        from prompt_matrix.routers.inquire_stream import resolve_active_document

        inline = JDFDocumentTree(document_id="inline", body=[], truth_ledger={})
        resolved = resolve_active_document("ignored", inline)
        self.assertEqual(resolved.document_id, "inline")


def _convicted_tree(project_id: str = "convicted") -> dict:
    """One claim paragraph a Red-Hat audit has convicted, with an open finding.

    The state the demo document was in at v5: the paragraph quotes a source
    sentence the audit read as not supporting the claim, the entailment verdict
    is ``no``, and ``crit-open`` is the finding on the node.
    """
    tree = _sample_tree(project_id)
    para = tree["body"][0]["children"][0]
    para["content"] = "The renewal policy has a minimum earned premium of 35.00% of the total premium."
    para["provenance"] = [
        {
            "source_type": "internal_doc",
            "source_name": "brim-cp-media43.pdf",
            "url_or_doi": "",
            "source_id": "",
            "page_number": "1",
            "extracted_quote": "35 00% Minimum Earned Premium",
            "accessed_date": "",
        }
    ]
    para["meta"] = {"provenance": {"entailment": {"verdict": "no", "contradicted": False}}}
    para["annotations"] = {
        "redhat": [
            {
                "id": "crit-open",
                "node_id": "p-1",
                "text": "**Finding** — the supplied source does not support the claim as written.",
                "status": "open",
            }
        ],
        "z3": [],
    }
    return tree


@pytest.mark.asyncio
async def test_a_rewrite_keeps_the_finding_it_closes(client, temp_db):
    """Applying a finding leaves the document still showing that it existed.

    Measured before this fix, on the demo project: the audit read ``8 anchored /
    7 supported / 1 unsupported`` with one open finding; after Apply the same
    document read ``7 anchored / 7 supported / 0 unsupported / 1 unanchored``
    with the finding gone from the tree. The paragraph genuinely lost its anchor
    — that movement is correct and stays — but the warning that prompted the
    rewrite must survive it, naming the revision that answered it.
    """
    from prompt_matrix.db.jdf_repository import fetch_latest_jdf

    # Seed the project the way the app does, so the document the rewrite acts on
    # is a stored revision rather than only an inline payload.
    seeded = await client.put(
        "/api/projects/convicted/jdf",
        json={"document": _convicted_tree("convicted"), "mutation_type": "seed"},
    )
    assert seeded.status_code == 200

    with patch("prompt_matrix.routers.inquire_stream.CostGovernor") as Gov:
        instance = Gov.return_value
        instance.preflight.return_value = object()
        instance.execute_with_retry_budget.return_value = _mock_result(
            node={
                "type": "paragraph",
                "id": "p-1",
                "content": "The source states a “35 00% Minimum Earned Premium”.",
                "entities_referenced": [],
                "provenance": [],
                "meta": {},
                "annotations": {"redhat": [], "z3": []},
            }
        )

        events = await _collect_events(
            client,
            "convicted",
            {
                "user_intent": "Fix this claim",
                "target_node_id": "p-1",
                "run_redhat": False,
                "document": _convicted_tree("convicted"),
            },
        )

    ready = next(data for name, data in events if name == "jdf_node_ready")
    finding = ready["node"]["annotations"]["redhat"][0]
    complete = next(data for name, data in events if name == "complete")
    assert complete.get("persisted") is True

    # The reader's copy of the node — the frame the pane renders — carries the
    # finding, closed, and names the revision that closed it.
    assert finding["id"] == "crit-open"
    assert finding["status"] == "resolved"
    assert finding["resolved_by_revision_id"] == complete["revision_id"]
    assert finding["resolved_by_version"] == complete["version"]
    assert finding["resolved_by_mutation_type"] == "surgical_rewrite"
    assert finding["resolved_at"]
    # What it was raised against, which the rewritten paragraph no longer holds.
    assert finding["prior_anchor_quote"] == "35 00% Minimum Earned Premium"
    assert finding["prior_verdict"] == "no"
    assert "does not support the claim" in finding["text"]

    # The paragraph keeps no citations: those sentences supported the text that
    # was replaced, and a rewrite that inherited them would read as anchored to
    # sources its new wording was never matched against.
    assert ready["node"]["provenance"] == []

    # And the same is true of what was written, not only of what was streamed.
    stored = fetch_latest_jdf("convicted")
    assert stored is not None
    node = stored["body"][0]["children"][0]
    assert node["content"].startswith("The source states")
    assert node["provenance"] == []
    persisted = node["annotations"]["redhat"][0]
    assert persisted["status"] == "resolved"
    assert persisted["resolved_by_revision_id"] == complete["revision_id"]
    assert persisted["resolved_by_version"] == complete["version"]
    assert persisted["prior_anchor_quote"] == "35 00% Minimum Earned Premium"


@pytest.mark.asyncio
async def test_the_counters_say_a_finding_was_remediated(client, temp_db):
    """The unsupported count falls to 0 — and the document says why.

    ``provenance_stats`` counts claims as the tree stands, so a remediated
    paragraph reads there only as one that stopped being unsupported: the number
    drops to zero and nothing distinguishes "the audit came back clean" from "the
    one bad paragraph was rewritten". The findings block is that distinction, and
    the lost anchor stays visible in ``unanchored``.
    """
    from prompt_matrix.db.jdf_repository import fetch_latest_jdf
    from prompt_matrix.services.audit_summary import provenance_gate_fields

    seeded = await client.put(
        "/api/projects/counters/jdf",
        json={"document": _convicted_tree("counters"), "mutation_type": "seed"},
    )
    assert seeded.status_code == 200

    before = provenance_gate_fields(
        document=_convicted_tree("counters"),
        z3_status="PASS",
        redhat_count=0,
        has_substrate=True,
    )
    assert before["provenance_stats"]["unsupported"] == 1
    assert before["provenance_stats"]["unanchored"] == 0
    assert before["findings"] == {
        "open": 1,
        "resolved": 0,
        "remediated_unsupported": 0,
        "dismissed": 0,
        "remediated_unanchored": 0,
    }

    with patch("prompt_matrix.routers.inquire_stream.CostGovernor") as Gov:
        instance = Gov.return_value
        instance.preflight.return_value = object()
        instance.execute_with_retry_budget.return_value = _mock_result(
            node={
                "type": "paragraph",
                "id": "p-1",
                "content": "The source states a “35 00% Minimum Earned Premium”.",
                "entities_referenced": [],
                "provenance": [],
                "meta": {},
                "annotations": {"redhat": [], "z3": []},
            }
        )
        await _collect_events(
            client,
            "counters",
            {
                "user_intent": "Fix this claim",
                "target_node_id": "p-1",
                "run_redhat": False,
                "document": _convicted_tree("counters"),
            },
        )

    after = provenance_gate_fields(
        document=fetch_latest_jdf("counters"),
        z3_status="PASS",
        redhat_count=0,
        has_substrate=True,
    )
    # The paragraph lost its anchor and that is not hidden.
    assert after["provenance_stats"]["unsupported"] == 0
    assert after["provenance_stats"]["anchored"] == 0
    assert after["provenance_stats"]["unanchored"] == 1
    # Nor is the finding that was answered.
    assert after["findings"] == {
        "open": 0,
        "resolved": 1,
        "remediated_unsupported": 1,
        "dismissed": 0,
        "remediated_unanchored": 1,
    }
