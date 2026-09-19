"""Audit bundle PDF export tests."""

from __future__ import annotations

import pytest

from prompt_matrix.services.audit_bundle import build_audit_bundle_html, export_audit_bundle_pdf
from prompt_matrix.web import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setenv("WTF_CSRF_ENABLED", "0")
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db

    init_db()
    app = create_app(require_auth=False)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _doc(pid: str) -> dict:
    return {
        "document_id": f"doc-{pid}",
        "meta": {"title": "Audit Bundle Test"},
        "truth_ledger": {"deductible_pct": 2},
        "body": [
            {
                "type": "paragraph",
                "id": "p1",
                "content": "Policy compliance summary.",
                "meta": {},
                "annotations": {"redhat": [], "z3": []},
            }
        ],
    }


def test_audit_bundle_html_sections():
    html = build_audit_bundle_html("prj_test", _doc("prj_test"))
    assert "Compliance Audit Report" in html
    assert "Table of Contents" in html
    assert "Z3 Verification Results" in html
    assert "Sign-Offs" in html
    assert "Document Lock" in html
    assert "Disclaimer" in html
    assert "Human sign-off is the liability transfer" in html


def test_audit_bundle_pdf_bytes():
    pdf = export_audit_bundle_pdf("prj_test", _doc("prj_test"))
    assert pdf[:4] == b"%PDF"


def test_export_audit_pdf_route(client):
    create = client.post("/api/projects", json={"title": "Audit Export"})
    pid = create.get_json()["id"]
    client.put(f"/api/projects/{pid}/jdf", json={"document": _doc(pid)})
    res = client.get(f"/api/projects/{pid}/export?format=audit-pdf")
    assert res.status_code == 200
    assert res.mimetype == "application/pdf"
    assert res.data[:4] == b"%PDF"
