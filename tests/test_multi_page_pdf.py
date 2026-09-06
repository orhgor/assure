"""Multi-page PDF vault extraction tests."""

from __future__ import annotations

from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from prompt_matrix.lib.textract import TextractClient
from prompt_matrix.web import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "mpdf.db"))
    monkeypatch.setenv("WTF_CSRF_ENABLED", "0")
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db

    init_db()
    app = create_app(require_auth=False)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _make_multipage_pdf() -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(3):
        writer.add_blank_page(width=200, height=200)
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_textract_multipage_extraction():
    client_obj = TextractClient(client=MagicMock())
    pdf = _make_multipage_pdf()

    with patch.object(
        client_obj, "_detect_page_text", side_effect=["Page one text", "Page two", "Page three"]
    ):
        result = client_obj.extract_text(pdf, "three.pdf")

    assert result["page_count"] == 3
    assert "--- Page 1 ---" in result["text"]
    assert "--- Page 3 ---" in result["text"]
    assert len(result.get("pages") or []) == 3


def test_substrate_upload_accepts_multipage(client):
    pid = client.post("/api/projects", json={"title": "Multi PDF"}).get_json()["id"]
    pdf = _make_multipage_pdf()

    with patch("prompt_matrix.routers.substrate.TextractClient") as mock_cls:
        instance = mock_cls.return_value
        instance._get_page_count.return_value = 3
        instance.extract_text.return_value = {
            "text": "--- Page 1 ---\nA\n\n--- Page 2 ---\nB\n\n--- Page 3 ---\nC",
            "tables": [],
            "forms": [],
            "page_count": 3,
            "pages": [
                {"page": 1, "text": "A"},
                {"page": 2, "text": "B"},
                {"page": 3, "text": "C"},
            ],
        }
        res = client.post(
            f"/api/projects/{pid}/substrate/upload",
            data={"file": (BytesIO(pdf), "three.pdf")},
            content_type="multipart/form-data",
        )

    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True


def test_z3_page_number_in_reason():
    from prompt_matrix.ledger.z3_ledger import check_claim

    context = "--- Page 1 ---\nintro\n\n--- Page 4 ---\ndeductible_pct equals two percent"
    result = check_claim("deductible two percent", context=context, source_label="policy.pdf")
    assert "page 4" in result["reason"] or "page" in result["reason"].lower()
