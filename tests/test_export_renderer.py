"""PDF export: a real renderer or a 503 — never a text dump under a .pdf name.

Customer QA of 2026-09-26: the exported "dossier" was the text writer of
``exporters/pdf_ast.py`` announcing "Playwright browser missing / WeasyPrint
missing / fallback text rendering used" on page 1. The route now answers 503 when
neither engine can run, and the zip bundle leaves the PDF members out and says so
in ``manifest.json``. WeasyPrint may be absent in the venv (it needs Pango); the
rendered-PDF tests run only where an engine is present — the image check in
docs/parsure.md ("Export") is the renderer test.
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from prompt_matrix.services import verification_dossier as vd
from prompt_matrix.web import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "export.db"))
    monkeypatch.setenv("WTF_CSRF_ENABLED", "0")
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db

    init_db()
    app = create_app(require_auth=False)
    app.config["TESTING"] = True
    # The export route is limited to 10 requests a minute per address; the
    # limiter is module-global with an in-process store under test, so a file that
    # exports more than ten times would start answering 429 to itself.
    from prompt_matrix.rate_limits import limiter

    monkeypatch.setattr(limiter, "enabled", False)
    with app.test_client() as c:
        yield c


def _doc(pid: str) -> dict:
    return {
        "document_id": f"doc-{pid}",
        "meta": {"title": "Export Renderer Test"},
        "truth_ledger": {},
        "body": [
            {
                "type": "paragraph",
                "id": "p1",
                "content": "The policy renews on the first of the month.",
                "meta": {},
                "annotations": {"redhat": [], "z3": []},
            }
        ],
    }


def _project(client) -> str:
    create = client.post("/api/projects", json={"title": "Export"})
    pid = create.get_json()["id"]
    client.put(f"/api/projects/{pid}/jdf", json={"document": _doc(pid)})
    return pid


@pytest.fixture
def no_renderer(monkeypatch):
    monkeypatch.setattr(vd, "_probe_playwright", lambda: "browser not installed: test")
    monkeypatch.setattr(vd, "_probe_weasyprint", lambda: "missing: ModuleNotFoundError: No module named 'weasyprint'")


@pytest.mark.parametrize("fmt", ["pdf", "dossier-pdf", "audit-pdf", "pdf&audit_bundle=1"])
def test_pdf_formats_answer_503_without_a_renderer(client, no_renderer, fmt):
    pid = _project(client)
    res = client.get(f"/api/projects/{pid}/export?format={fmt}")
    assert res.status_code == 503, res.data[:200]
    body = res.get_json()
    assert body["ok"] is False
    assert body["error"] == "PDF renderer unavailable on this server"
    assert body["detail"]["playwright"].startswith("browser not installed")
    assert body["detail"]["weasyprint"].startswith("missing")
    assert res.mimetype == "application/json"


def test_render_pdf_raises_rather_than_falling_back(no_renderer):
    with pytest.raises(vd.PdfRendererUnavailable) as excinfo:
        vd.render_pdf("<html><body>x</body></html>")
    assert set(excinfo.value.detail) == {"playwright", "weasyprint"}
    assert vd.renderer_available() is False


def test_bundle_without_a_renderer_omits_the_pdf_and_says_so(client, no_renderer):
    pid = _project(client)
    res = client.get(f"/api/projects/{pid}/export?format=bundle")
    assert res.status_code == 200
    assert res.mimetype == "application/zip"
    with zipfile.ZipFile(io.BytesIO(res.data)) as archive:
        names = archive.namelist()
        assert not any(name.endswith(".pdf") for name in names)
        assert "verification_state.json" in names
        assert "manifest.json" in names
        manifest = json.loads(archive.read("manifest.json"))
        state = json.loads(archive.read("verification_state.json"))
    assert manifest["pdf"]["included"] is False
    assert manifest["pdf"]["reason"] == "PDF renderer unavailable on this server"
    assert manifest["pdf"]["detail"]["weasyprint"].startswith("missing")
    assert manifest["trust_state"] == state["trust_state"]
    assert manifest["title"] == state["title"]
    assert {m["name"] for m in manifest["members"]} == set(names) - {"manifest.json"}
    assert state["schema"] == vd.STATE_SCHEMA


_renderer = pytest.mark.skipif(not vd.renderer_available(), reason="no HTML-to-PDF engine in this environment")


@_renderer
def test_dossier_pdf_is_rendered_and_titled_by_trust_state(client):
    pid = _project(client)
    res = client.get(f"/api/projects/{pid}/export?format=dossier-pdf")
    assert res.status_code == 200
    assert res.mimetype == "application/pdf"
    assert res.data[:4] == b"%PDF"
    assert res.headers["X-Assure-Trust-State"] in ("verified", "review_required", "not_verified")
    assert res.headers["X-Assure-Renderer"] in ("playwright", "weasyprint")
    fitz = pytest.importorskip("fitz")
    with fitz.open(stream=res.data, filetype="pdf") as pdf:
        text = "".join(page.get_text() for page in pdf)
        fonts = {f[3] for page in pdf for f in page.get_fonts()}
    assert "Verification Dossier" in text
    assert "rendered as text" not in text and "fallback" not in text.lower()
    # An HTML engine embeds the fonts it used; the text writer names Helvetica and embeds nothing.
    assert fonts and fonts != {"Helvetica"}


@_renderer
def test_bundle_with_a_renderer_carries_pdf_and_matching_state(client):
    pid = _project(client)
    res = client.get(f"/api/projects/{pid}/export?format=bundle")
    assert res.status_code == 200
    with zipfile.ZipFile(io.BytesIO(res.data)) as archive:
        names = archive.namelist()
        pdfs = [n for n in names if n.endswith(".pdf")]
        assert len(pdfs) == 2
        for name in pdfs:
            assert archive.read(name)[:4] == b"%PDF"
        manifest = json.loads(archive.read("manifest.json"))
        state = json.loads(archive.read("verification_state.json"))
    assert manifest["pdf"]["included"] is True
    assert manifest["pdf"]["renderer"] in ("playwright", "weasyprint")
    assert manifest["trust_state"] == state["trust_state"]
    assert manifest["status_band"] == state["status_band"]
