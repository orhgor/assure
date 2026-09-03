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
                    }
                ],
                "meta": {},
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


@pytest.fixture
def temp_db():
    tmp = tempfile.TemporaryDirectory()
    db_path = Path(tmp.name) / "history.sqlite"

    def _getter():
        import sqlite3

        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    with patch("prompt_matrix.history.DB_PATH", db_path), patch(
        "prompt_matrix.db.connection.get_db", side_effect=_getter
    ), patch("prompt_matrix.cost_governance.get_db", side_effect=_getter), patch(
        "prompt_matrix.db.jdf_repository.get_db", side_effect=_getter
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
            "redhat_callout",
            "jdf_node_ready",
            "usage",
            "complete",
        ):
            assert expected in names
        order = [names.index(e) for e in ("status", "token", "truth_check", "jdf_node_ready", "usage", "complete")]
        assert order == sorted(order)


@pytest.mark.asyncio
async def test_surgical_diff_context(client):
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
    put = await client.put("/api/projects/roundtrip/jdf", json={"document": tree, "mutation_type": "seed"})
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
    await client.put("/api/projects/export-me/jdf", json={"document": tree, "mutation_type": "seed"})
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
