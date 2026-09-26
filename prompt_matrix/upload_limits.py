"""Upload size and PDF page limits for Substrate Vault attachments."""

from __future__ import annotations

import mimetypes
import os
import re
from io import BytesIO
from pathlib import Path
from typing import Any

MAX_FILE_SIZE_MB = 25  # default; MAX_UPLOAD_SIZE_MB in .env overrides (was 10 until 2026-09-26: a customer's real policy PDF was refused)
MAX_PAGE_COUNT = int(os.environ.get("ASSURE_MAX_PAGES", "50"))

_FILE_HEADER = re.compile(r"^### File:\s*(.+?)\r?\n", re.MULTILINE)
_DANGEROUS_EXT = frozenset({".exe", ".js", ".html", ".htm", ".mjs", ".bat", ".cmd", ".com", ".scr"})
_ALLOWED_PREFIXES = ("application/pdf", "image/")


def max_upload_bytes() -> int:
    raw = (os.environ.get("MAX_UPLOAD_SIZE") or os.environ.get("MAX_FILE_SIZE_BYTES") or "").strip()
    if raw.isdigit():
        return max(1, int(raw))
    mb = (os.environ.get("MAX_UPLOAD_SIZE_MB") or "").strip()
    if mb.isdigit():
        return max(1, int(mb)) * 1024 * 1024
    return MAX_FILE_SIZE_MB * 1024 * 1024


MAX_FILE_SIZE_BYTES = max_upload_bytes()


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


def _filename_is_dangerous(name: str) -> bool:
    lowered = name.lower().strip()
    parts = Path(lowered).suffixes
    if any(ext in _DANGEROUS_EXT for ext in parts):
        return True
    if lowered.count(".") >= 2:
        inner = "".join(parts[:-1]) if parts else ""
        if any(ext in inner for ext in _DANGEROUS_EXT):
            return True
    return False


def _sniff_mime(filename: str, data: bytes) -> str:
    if data[:5] == b"%PDF-":
        return "application/pdf"
    if data[:2] == b"MZ":
        return "application/x-msdownload"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    guessed, _ = mimetypes.guess_type(filename)
    return (guessed or "application/octet-stream").lower()


def _open_pdf_or_raise(data: bytes) -> None:
    try:
        import fitz

        fitz.open(stream=data, filetype="pdf").close()
        return
    except ImportError:
        pass
    except Exception as exc:
        raise UploadRejectedError("Invalid or malformed PDF", http_status=400) from exc
    try:
        count_pdf_pages(data)
    except UploadRejectedError:
        raise
    except Exception as exc:
        raise UploadRejectedError("Invalid or malformed PDF", http_status=400) from exc


def validate_upload_bytes(filename: str, data: bytes) -> dict[str, Any]:
    """Validate raw upload bytes (size, type, PDF integrity)."""
    name = (filename or "upload").strip() or "upload"
    size = len(data or b"")
    limit = max_upload_bytes()
    if size > limit:
        mb = max(1, limit // (1024 * 1024))
        raise UploadRejectedError(f"File exceeds {mb} MB limit. Please upload a smaller document.")

    if _filename_is_dangerous(name):
        raise UploadRejectedError("File type is not allowed.", http_status=400)

    mime = _sniff_mime(name, data)
    allowed = mime.startswith(_ALLOWED_PREFIXES) or mime == "application/pdf"
    looks_pdf = name.lower().endswith(".pdf") or mime == "application/pdf"
    looks_image = mime.startswith("image/") or Path(name).suffix.lower() in {
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".gif",
        ".tif",
        ".tiff",
    }
    looks_text = Path(name).suffix.lower() in {".txt", ".md"}
    if looks_pdf:
        if mime == "application/x-msdownload" or data[:2] == b"MZ":
            raise UploadRejectedError("Invalid or malformed PDF", http_status=400)
        _open_pdf_or_raise(data)
    elif not looks_image and not looks_text:
        raise UploadRejectedError("Only PDF and image uploads are allowed.", http_status=400)

    page_count: int | None = None
    if looks_pdf:
        try:
            page_count = count_pdf_pages(data)
        except UploadRejectedError:
            raise
        except Exception as exc:
            raise UploadRejectedError("Invalid or malformed PDF", http_status=400) from exc
        if page_count > MAX_PAGE_COUNT:
            raise UploadRejectedError(
                f"PDF exceeds {MAX_PAGE_COUNT} page limit. Please upload a shorter extract."
            )

    return {
        "filename": name,
        "size_bytes": size,
        "page_count": page_count,
        "mime": mime,
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


def pdf_has_visual_content(data: bytes) -> bool:
    """True when PDF pages embed images or form XObjects (charts/diagrams)."""
    if not data:
        return False
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover
        return False
    try:
        reader = PdfReader(BytesIO(data))
    except Exception:
        return False
    for page in reader.pages:
        resources = page.get("/Resources") or {}
        xobjects = resources.get("/XObject") or {}
        if not xobjects:
            continue
        for ref in xobjects.values():
            try:
                obj = ref.get_object()
            except Exception:
                continue
            subtype = str(obj.get("/Subtype") or "")
            if subtype in {"/Image", "/Form"}:
                return True
    return False
