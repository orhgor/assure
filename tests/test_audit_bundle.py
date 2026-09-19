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


def _gate(project_id: str, plan: dict) -> None:
    """Persist a compile's gate block, the way the compile does."""
    import json

    from prompt_matrix.db.connection import init_db
    from prompt_matrix.db.jdf_repository import ensure_project
    from prompt_matrix.history import get_db

    init_db()
    ensure_project(project_id, "Audit")
    db = get_db()
    row = db.execute(
        "SELECT last_compiled_json FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    data = json.loads(row[0]) if (row and row[0]) else {}
    # A fresh project's column holds a list; the compile's own gate write normalizes
    # it the same way before adding the block.
    if not isinstance(data, dict):
        data = {}
    data["gate"] = {"sources": plan}
    db.execute(
        "UPDATE projects SET last_compiled_json = ? WHERE id = ?",
        (json.dumps(data), project_id),
    )
    db.commit()


def test_the_dossier_states_the_sources_the_compile_carried(tmp_path, monkeypatch):
    """The counts and the names of the sources the prompt left behind.

    The prompt cannot hold every attachment, and nothing used to say which ones it
    left behind: measured on a deployed project, 24 sources attached, 18 numbered
    into the prompt, and the manifest listed all 24 as included. The dossier is where
    a client reads coverage, so the count and the dropped names are stated beside
    every other number — and this reads the same gate block the export reads.
    """
    import prompt_matrix.history as history_mod

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "carry.db"))
    history_mod.DB_PATH = history_mod._resolve_db_path()

    plan = {
        "limit_chars": 400_000,
        "per_file_limit_chars": 200_000,
        "attached": 3,
        "carried": 2,
        "dropped": 1,
        "truncated": 0,
        "carried_chars": 12_345,
        "sources": [
            {"source_id": "s1", "filename": "expiring.pdf", "included": True},
            {"source_id": "s2", "filename": "renewal.pdf", "included": True},
            {
                "source_id": "s3",
                "filename": "cover-note.pdf",
                "included": False,
                "dropped_reason": "the context cap (400,000 characters) was reached before this source",
            },
        ],
    }
    _gate("carry-truth", plan)

    html = build_audit_bundle_html("carry-truth", _doc("carry-truth"))
    assert "Sources carried into the model" in html
    assert "<strong>2 of 3</strong>" in html
    assert "12,345" in html
    assert "cover-note.pdf" in html
    assert "context cap" in html
    # A recorded plan is a record, not a re-derivation, and the dossier says so.
    assert "recomputed from the sources in the vault" not in html


@pytest.mark.xfail(
    reason=(
        "audit_bundle.py:535 (open): with no Red-Hat item to list and a pass recorded, "
        "the section states 'Red-Hat ran for this document and recorded no findings'. "
        "The findings that exist are read from the tree the caller passes and from "
        "`redhat_findings` rows — a node-scoped audit writes neither to that table nor "
        "to an earlier revision's tree, so exporting a revision whose tree predates the "
        "annotation denies a finding the product holds on the document. Proposed design: "
        "when the passed tree carries none, consult the project's current document "
        "(`fetch_latest_jdf_or_empty`) for `annotations.redhat` and report them as "
        "'recorded on revision N' rather than silently as none; the denial sentence must "
        "not be reachable while the project holds a finding."
    ),
    strict=True,
)
def test_the_dossier_does_not_deny_a_redhat_finding_the_project_records(tmp_path, monkeypatch):
    import prompt_matrix.history as history_mod
    from prompt_matrix.db.jdf_repository import ensure_project, save_jdf_revision
    from prompt_matrix.db.redhat_telemetry_repository import upsert_telemetry

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "redhat.db"))
    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db

    init_db()
    ensure_project("redhat-truth", "Red-Hat")

    earlier = _doc("redhat-truth")
    save_jdf_revision("redhat-truth", earlier, mutation_type="compile")
    current = _doc("redhat-truth")
    current["body"][0]["annotations"]["redhat"] = [
        {
            "id": "crit-1",
            "node_id": "p1",
            "text": "The memo omits the carve-out the source states.",
            "status": "open",
        }
    ]
    save_jdf_revision("redhat-truth", current, mutation_type="redhat")
    upsert_telemetry("redhat-truth", status="complete", findings=[])

    html = build_audit_bundle_html("redhat-truth", earlier)
    assert "recorded no findings" not in html
    assert "carve-out" in html
