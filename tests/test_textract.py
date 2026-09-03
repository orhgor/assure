"""AWS Textract client and Substrate Vault upload tests."""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import pytest
from pypdf import PdfWriter

from prompt_matrix.lib.textract import TextractClient


def _single_page_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _multi_page_pdf(pages: int = 2) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_get_page_count_image_returns_one_without_pdf_reader():
    client = TextractClient()
    with patch("pypdf.PdfReader", side_effect=AssertionError("PdfReader must not run for images")):
        assert client._get_page_count(b"\x89PNG\r\n", "scan.png") == 1
        assert client._get_page_count(b"fake", "photo.JPG") == 1
        assert client._get_page_count(b"fake", "scan.tiff") == 1


def test_get_page_count_pdf():
    client = TextractClient()
    assert client._get_page_count(_single_page_pdf(), "one.pdf") == 1
    assert client._get_page_count(_multi_page_pdf(3), "three.pdf") == 3


def test_extract_text_success_with_mock_textract():
    mock_client = MagicMock()
    mock_client.analyze_document.return_value = {
        "Blocks": [
            {"BlockType": "LINE", "Text": "Revenue", "Id": "l1"},
            {"BlockType": "LINE", "Text": "$4.2M", "Id": "l2"},
            {
                "BlockType": "TABLE",
                "Id": "t1",
                "Relationships": [{"Type": "CHILD", "Ids": ["c1"]}],
            },
            {
                "BlockType": "CELL",
                "Id": "c1",
                "RowIndex": 1,
                "ColumnIndex": 1,
                "Relationships": [{"Type": "CHILD", "Ids": ["w1"]}],
            },
            {"BlockType": "WORD", "Id": "w1", "Text": "Q3"},
        ]
    }
    textract = TextractClient(client=mock_client)
    result = textract.extract_text(_single_page_pdf(), "brief.pdf")
    assert "Revenue" in result["text"]
    assert result["page_count"] == 1
    assert len(result["tables"]) == 1
    assert result["tables"][0]["rows"][0][0] == "Q3"
    mock_client.analyze_document.assert_called_once()


def test_analyze_document_retries_throttling():
    mock_client = MagicMock()
    throttled = MagicMock()
    throttled.response = {"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}}
    try:
        from botocore.exceptions import ClientError

        throttled = ClientError(throttled.response, "AnalyzeDocument")
    except ImportError:
        pass

    mock_client.analyze_document.side_effect = [
        throttled,
        throttled,
        {"Blocks": [{"BlockType": "LINE", "Text": "OK", "Id": "l1"}]},
    ]
    textract = TextractClient(client=mock_client)
    with patch("prompt_matrix.lib.textract.time.sleep"):
        response = textract._analyze_document_with_retry(_single_page_pdf(), max_retries=4)
    assert response["Blocks"][0]["Text"] == "OK"
    assert mock_client.analyze_document.call_count == 3


def test_extract_text_falls_back_to_detect_document_text():
    mock_client = MagicMock()
    mock_client.analyze_document.side_effect = RuntimeError("AccessDenied")
    mock_client.detect_document_text.return_value = {
        "Blocks": [{"BlockType": "LINE", "Text": "Fallback line", "Id": "l1"}]
    }
    textract = TextractClient(client=mock_client)
    result = textract.extract_text(_single_page_pdf(), "brief.pdf")
    assert result["text"] == "Fallback line"
    mock_client.detect_document_text.assert_called_once()


def test_extract_tables_fallback_on_broken_grid():
    blocks = [
        {"BlockType": "LINE", "Text": "Col A", "Id": "1", "Geometry": {"BoundingBox": {"Top": 0.1}}},
        {"BlockType": "LINE", "Text": "Col B", "Id": "2", "Geometry": {"BoundingBox": {"Top": 0.1}}},
        {"BlockType": "LINE", "Text": "Row 2", "Id": "3", "Geometry": {"BoundingBox": {"Top": 0.3}}},
    ]
    textract = TextractClient(client=MagicMock())
    tables = textract._extract_tables(blocks)
    assert len(tables) == 1
    assert tables[0].get("fallback") is True


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    from prompt_matrix.db.connection import init_db
    from prompt_matrix.web import create_app

    init_db()
    app = create_app(require_auth=False)
    return app.test_client()


def test_substrate_upload_rejects_multi_page_pdf(app_client):
    data = {"file": (io.BytesIO(_multi_page_pdf(2)), "report.pdf")}
    response = app_client.post(
        "/api/projects/default/substrate/upload",
        data=data,
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    payload = response.get_json()
    assert payload["ok"] is False
    assert payload["page_count"] == 2
    assert "2 pages" in payload["error"]


def test_substrate_upload_single_page_success(app_client):
    mock_client = MagicMock()
    mock_client.analyze_document.return_value = {
        "Blocks": [
            {"BlockType": "LINE", "Text": "Net income grew 12%.", "Id": "l1"},
        ]
    }
    with patch("prompt_matrix.routers.substrate.TextractClient") as mock_cls:
        instance = mock_cls.return_value
        instance._get_page_count.return_value = 1
        instance.extract_text.return_value = {
            "text": "Net income grew 12%.",
            "tables": [],
            "forms": [],
            "page_count": 1,
            "filename": "brief.pdf",
        }
        data = {"file": (io.BytesIO(_single_page_pdf()), "brief.pdf")}
        response = app_client.post(
            "/api/projects/default/substrate/upload",
            data=data,
            content_type="multipart/form-data",
        )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["text"] == "Net income grew 12%."
    assert payload["tables"] == []
    assert payload["page_count"] == 1

    from prompt_matrix.db.substrate_repository import fetch_latest_substrate

    stored = fetch_latest_substrate("default")
    assert stored is not None
    assert stored["text"] == "Net income grew 12%."


def test_substrate_upload_image_success(app_client):
    with patch("prompt_matrix.routers.substrate.TextractClient") as mock_cls:
        instance = mock_cls.return_value
        instance._get_page_count.return_value = 1
        instance.extract_text.return_value = {
            "text": "Invoice #123",
            "tables": [],
            "forms": [{"key": "Total", "value": "$99"}],
            "page_count": 1,
            "filename": "invoice.png",
        }
        data = {"file": (io.BytesIO(b"\x89PNG\r\n\x1a\n"), "invoice.png")}
        response = app_client.post(
            "/api/projects/default/substrate/upload",
            data=data,
            content_type="multipart/form-data",
        )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["is_image"] is True
    assert payload["text"] == "Invoice #123"
