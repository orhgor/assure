"""Upload MIME, size, and PDF integrity checks."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest

from prompt_matrix.upload_limits import UploadRejectedError, max_upload_bytes, validate_upload_bytes


def _pdf_bytes() -> bytes:
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Hello Assure")
    data = doc.tobytes()
    doc.close()
    return data


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "up.sqlite"))
    monkeypatch.setenv("WTF_CSRF_ENABLED", "0")
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db
    from prompt_matrix.web import create_app

    init_db()
    return create_app(require_auth=False).test_client()


def test_oversized_file() -> None:
    with pytest.raises(UploadRejectedError) as exc:
        validate_upload_bytes("big.pdf", b"%PDF-1.4\n" + b"x" * (max_upload_bytes() + 1))
    assert exc.value.http_status in {400, 413}


def test_malformed_pdf() -> None:
    with pytest.raises(UploadRejectedError):
        validate_upload_bytes("bad.pdf", b"%PDF-1.4 definitely-not-a-pdf")


def test_renamed_exe() -> None:
    with pytest.raises(UploadRejectedError):
        validate_upload_bytes("payload.pdf", b"MZ" + b"\x00" * 64)


def test_valid_pdf_passes(tmp_path, monkeypatch) -> None:
    data = _pdf_bytes()
    meta = validate_upload_bytes("ok.pdf", data)
    assert meta["page_count"] == 1
    client = _client(tmp_path, monkeypatch)
    res = client.post(
        "/api/projects/default/import-pdf",
        data={"file": (BytesIO(data), "ok.pdf")},
        content_type="multipart/form-data",
    )
    assert res.status_code in {200, 201}
