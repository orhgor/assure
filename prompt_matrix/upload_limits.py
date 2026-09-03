"""Upload size and PDF page limits for Substrate Vault attachments."""

from __future__ import annotations

import re
from io import BytesIO
from typing import Any

MAX_FILE_SIZE_MB = 10
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
MAX_PAGE_COUNT = 50

_FILE_HEADER = re.compile(r"^### File:\s*(.+?)\r?\n", re.MULTILINE)


class UploadRejectedError(Exception):
    """Raised when an attachment exceeds configured limits."""

    http_status = 413

    def __init__(self, message: str, *, http_status: int | None = None) -> None:
        super().__init__(message)
        if http_status is not None:
            self.http_status = http_status


def count_pdf_pages(data: bytes) -> int:
    """Return PDF page count using pypdf."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency declared in pyproject
        raise UploadRejectedError(
            "PDF validation unavailable on server.",
            http_status=500,
        ) from exc
    reader = PdfReader(BytesIO(data))
    return len(reader.pages)


def validate_upload_bytes(filename: str, data: bytes) -> dict[str, Any]:
    """Validate raw upload bytes (size + PDF page cap)."""
    name = (filename or "upload").strip() or "upload"
    size = len(data or b"")
    if size > MAX_FILE_SIZE_BYTES:
        raise UploadRejectedError(
            f"File exceeds {MAX_FILE_SIZE_MB} MB limit. Please upload a smaller document."
        )

    page_count: int | None = None
    if name.lower().endswith(".pdf"):
        try:
            page_count = count_pdf_pages(data)
        except UploadRejectedError:
            raise
        except Exception as exc:
            raise UploadRejectedError("Corrupted or unsupported PDF.") from exc
        if page_count > MAX_PAGE_COUNT:
            raise UploadRejectedError(
                f"PDF exceeds {MAX_PAGE_COUNT} page limit. Please upload a shorter extract."
            )

    return {
        "filename": name,
        "size_bytes": size,
        "page_count": page_count,
    }


def extract_upload_filename(file_context: str) -> str | None:
    match = _FILE_HEADER.search(file_context or "")
    if not match:
        return None
    return match.group(1).strip()


def validate_file_context_payload(
    file_context: str,
    upload_meta: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Validate client-attached file context before model calls."""
    text = (file_context or "").strip()
    if not text:
        return None

    encoded_size = len(text.encode("utf-8"))
    if encoded_size > MAX_FILE_SIZE_BYTES:
        raise UploadRejectedError(
            f"Attached file exceeds {MAX_FILE_SIZE_MB} MB limit. Please upload a smaller document."
        )

    meta = dict(upload_meta or {})
    filename = str(meta.get("filename") or meta.get("name") or extract_upload_filename(text) or "")
    size_bytes = meta.get("size_bytes")
    if size_bytes is not None:
        try:
            reported = int(size_bytes)
        except (TypeError, ValueError):
            reported = 0
        if reported > MAX_FILE_SIZE_BYTES:
            raise UploadRejectedError(
                f"File exceeds {MAX_FILE_SIZE_MB} MB limit. Please upload a smaller document."
            )

    page_count = meta.get("page_count")
    is_pdf = filename.lower().endswith(".pdf")
    if is_pdf and page_count is not None:
        try:
            pages = int(page_count)
        except (TypeError, ValueError):
            pages = 0
        if pages > MAX_PAGE_COUNT:
            raise UploadRejectedError(
                f"PDF exceeds {MAX_PAGE_COUNT} page limit. Please upload a shorter extract."
            )

    return {
        "filename": filename or None,
        "size_bytes": int(size_bytes) if size_bytes is not None else encoded_size,
        "page_count": int(page_count) if page_count is not None else None,
    }


def limits_snapshot() -> dict[str, int]:
    return {
        "max_file_size_mb": MAX_FILE_SIZE_MB,
        "max_pages": MAX_PAGE_COUNT,
    }
