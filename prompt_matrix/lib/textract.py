"""AWS Textract integration for Substrate Vault extraction (single- and multi-page)."""

from __future__ import annotations

import logging
import os
import time
from collections import defaultdict
from io import BytesIO
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".tiff", ".tif"})


class TextractError(Exception):
    """Raised when Textract extraction fails."""


class TextractClient:
    """Extract text, tables, and forms from documents via Textract."""

    TEXTRACT_MAX_PAGES = int(os.environ.get("ASSURE_MAX_PAGES", "50"))

    @staticmethod
    def mode() -> str:
        """``detect`` (DetectDocumentText, 0.0015 USD/page, default) or
        ``analyze`` (AnalyzeDocument TABLES+FORMS, 0.065 USD/page) —
        ``ASSURE_TEXTRACT_MODE``. Single-page documents used to go through
        AnalyzeDocument unconditionally, 43× the price of the text this
        fallback exists for."""
        raw = os.environ.get("ASSURE_TEXTRACT_MODE", "").strip().lower()
        return "analyze" if raw == "analyze" else "detect"

    @staticmethod
    def _charge(pages: int, api: str) -> None:
        """Hard monthly spend cap (services/textract_budget); raises before boto3."""
        try:
            from ..services.textract_budget import reserve
        except ImportError:
            from services.textract_budget import reserve
        reserve(pages, api)

    def __init__(self, *, client: Any | None = None, region: str | None = None) -> None:
        self._client = client
        self._region = region or os.environ.get("AWS_REGION", "us-east-1")

    def _boto_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import boto3
        except ImportError as exc:
            raise TextractError("boto3 is not installed.") from exc
        return boto3.client("textract", region_name=self._region)

    @staticmethod
    def is_image(filename: str) -> bool:
        return Path(filename or "").suffix.lower() in IMAGE_EXTENSIONS

    def _get_page_count(self, file_bytes: bytes, filename: str) -> int:
        if self.is_image(filename):
            return 1
        ext = Path(filename or "").suffix.lower()
        if ext == ".pdf":
            try:
                from pypdf import PdfReader
            except ImportError as exc:
                raise TextractError("PDF page counting unavailable.") from exc
            try:
                return len(PdfReader(BytesIO(file_bytes)).pages)
            except Exception as exc:
                raise TextractError("Corrupted or unsupported PDF.") from exc
        return 1

    def _analyze_document_with_retry(
        self,
        file_bytes: bytes,
        *,
        max_retries: int = 4,
    ) -> dict[str, Any]:
        client = self._boto_client()
        last_exc: Exception | None = None
        for attempt in range(max_retries):
            try:
                return client.analyze_document(
                    Document={"Bytes": file_bytes},
                    FeatureTypes=["TABLES", "FORMS"],
                )
            except Exception as exc:
                last_exc = exc
                code = ""
                try:
                    from botocore.exceptions import ClientError

                    if isinstance(exc, ClientError):
                        code = str(exc.response.get("Error", {}).get("Code") or "")
                except ImportError:
                    if hasattr(exc, "response"):
                        code = str((exc.response or {}).get("Error", {}).get("Code") or "")
                if code == "ThrottlingException" and attempt < max_retries - 1:
                    time.sleep(2**attempt)
                    continue
                raise
        if last_exc is not None:
            raise last_exc
        raise TextractError("Textract analyze_document failed.")

    def _split_pdf_pages(self, file_bytes: bytes) -> list[bytes]:
        try:
            from pypdf import PdfReader, PdfWriter
        except ImportError as exc:
            raise TextractError("PDF splitting unavailable.") from exc
        reader = PdfReader(BytesIO(file_bytes))
        pages: list[bytes] = []
        for idx in range(len(reader.pages)):
            writer = PdfWriter()
            writer.add_page(reader.pages[idx])
            buf = BytesIO()
            writer.write(buf)
            pages.append(buf.getvalue())
        return pages

    def _detect_page_text(self, page_bytes: bytes) -> str:
        try:
            response = self._boto_client().detect_document_text(Document={"Bytes": page_bytes})
        except Exception as exc:
            raise TextractError(f"Textract page extract failed: {exc}") from exc
        blocks = response.get("Blocks") or []
        return self._extract_plain_text(blocks)

    def extract_text(self, file_bytes: bytes, filename: str) -> dict[str, Any]:
        """Return text, tables, and forms. Multi-page PDFs are processed page-by-page."""
        filename = (filename or "upload").strip() or "upload"
        page_count = self._get_page_count(file_bytes, filename)

        if page_count > 1 and Path(filename).suffix.lower() == ".pdf":
            return self._extract_multipage_pdf(file_bytes, filename, page_count)

        response: dict[str, Any]
        mode = self.mode()
        self._charge(1, mode)
        if mode == "analyze":
            try:
                response = self._analyze_document_with_retry(file_bytes)
            except Exception as exc:
                logger.warning("Textract analyze_document failed: %s", exc)
                try:
                    response = self._boto_client().detect_document_text(
                        Document={"Bytes": file_bytes},
                    )
                except Exception as exc2:
                    raise TextractError(f"Textract failed: {exc2}") from exc2
        else:
            try:
                response = self._boto_client().detect_document_text(Document={"Bytes": file_bytes})
            except Exception as exc:
                raise TextractError(f"Textract failed: {exc}") from exc

        blocks = response.get("Blocks") or []
        text = self._extract_plain_text(blocks)
        tables: list[dict[str, Any]] = []
        forms: list[dict[str, Any]] = []
        try:
            tables = self._extract_tables(blocks)
        except Exception as exc:
            logger.warning("Table parse failed, falling back to text-only: %s", exc)
        try:
            forms = self._extract_forms(blocks)
        except Exception as exc:
            logger.warning("Form parse failed: %s", exc)

        return {
            "text": text,
            "tables": tables,
            "forms": forms,
            "page_count": page_count,
            "filename": filename,
            "pages": [{"page": 1, "text": text}] if text else [],
        }

    def _extract_multipage_pdf(
        self, file_bytes: bytes, filename: str, page_count: int
    ) -> dict[str, Any]:
        page_blobs = self._split_pdf_pages(file_bytes)
        self._charge(len(page_blobs[: self.TEXTRACT_MAX_PAGES]), "detect")
        pages_meta: list[dict[str, Any]] = []
        combined: list[str] = []
        for idx, blob in enumerate(page_blobs[: self.TEXTRACT_MAX_PAGES], start=1):
            page_text = self._detect_page_text(blob).strip()
            pages_meta.append({"page": idx, "text": page_text})
            if page_text:
                combined.append(f"--- Page {idx} ---\n{page_text}")
        text = "\n\n".join(combined)
        return {
            "text": text,
            "tables": [],
            "forms": [],
            "page_count": min(page_count, len(page_blobs)),
            "filename": filename,
            "pages": pages_meta,
        }

    @staticmethod
    def _block_map(blocks: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {b["Id"]: b for b in blocks if b.get("Id")}

    @staticmethod
    def _extract_plain_text(blocks: list[dict[str, Any]]) -> str:
        lines = [
            block["Text"]
            for block in blocks
            if block.get("BlockType") == "LINE" and block.get("Text")
        ]
        return "\n".join(lines)

    def _extract_tables(self, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        bmap = self._block_map(blocks)
        tables: list[dict[str, Any]] = []
        for block in blocks:
            if block.get("BlockType") != "TABLE":
                continue
            grid = self._table_to_grid(block, bmap)
            if grid:
                tables.append(
                    {
                        "id": block.get("Id"),
                        "rows": grid,
                        "row_count": len(grid),
                        "column_count": max((len(row) for row in grid), default=0),
                    }
                )
        if not tables:
            fallback = self._fallback_table_from_lines(blocks)
            if fallback:
                tables.append(fallback)
        return tables

    def _table_to_grid(
        self,
        table_block: dict[str, Any],
        bmap: dict[str, dict[str, Any]],
    ) -> list[list[str]]:
        cells: list[tuple[int, int, str]] = []
        for rel in table_block.get("Relationships") or []:
            if rel.get("Type") != "CHILD":
                continue
            for cid in rel.get("Ids") or []:
                cell = bmap.get(cid)
                if not cell or cell.get("BlockType") != "CELL":
                    continue
                text = self._cell_text(cell, bmap)
                row = int(cell.get("RowIndex") or 1) - 1
                col = int(cell.get("ColumnIndex") or 1) - 1
                cells.append((row, col, text))

        if not cells:
            return []

        max_row = max(row for row, _, _ in cells) + 1
        max_col = max(col for _, col, _ in cells) + 1
        grid = [["" for _ in range(max_col)] for _ in range(max_row)]
        for row, col, text in cells:
            if grid[row][col]:
                grid[row][col] += " " + text
            else:
                grid[row][col] = text
        return grid

    def _cell_text(self, cell: dict[str, Any], bmap: dict[str, dict[str, Any]]) -> str:
        parts: list[str] = []
        for rel in cell.get("Relationships") or []:
            if rel.get("Type") != "CHILD":
                continue
            for cid in rel.get("Ids") or []:
                child = bmap.get(cid)
                if not child:
                    continue
                if child.get("BlockType") == "WORD" and child.get("Text"):
                    parts.append(child["Text"])
                elif child.get("BlockType") == "SELECTION_ELEMENT":
                    state = child.get("SelectionStatus", "")
                    parts.append("[X]" if state == "SELECTED" else "[ ]")
        return " ".join(parts).strip()

    def _fallback_table_from_lines(self, blocks: list[dict[str, Any]]) -> dict[str, Any] | None:
        """When gridlines break, group LINE blocks by similar vertical position."""
        lines = [b for b in blocks if b.get("BlockType") == "LINE" and b.get("Text")]
        if len(lines) < 2:
            return None

        buckets: dict[float, list[str]] = defaultdict(list)
        for line in lines:
            geom = (line.get("Geometry") or {}).get("BoundingBox") or {}
            top = round(float(geom.get("Top") or 0.0), 3)
            buckets[top].append(line["Text"])

        sorted_rows = [buckets[key] for key in sorted(buckets.keys())]
        if not sorted_rows:
            return None
        return {
            "id": "fallback",
            "rows": sorted_rows,
            "row_count": len(sorted_rows),
            "column_count": max(len(row) for row in sorted_rows),
            "fallback": True,
        }

    def _extract_forms(self, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        bmap = self._block_map(blocks)
        keys = [
            block
            for block in blocks
            if block.get("BlockType") == "KEY_VALUE_SET"
            and "KEY" in (block.get("EntityTypes") or [])
        ]
        forms: list[dict[str, Any]] = []
        for key_block in keys:
            key_text = self._kv_text(key_block, bmap)
            value_text = ""
            for rel in key_block.get("Relationships") or []:
                if rel.get("Type") != "VALUE":
                    continue
                for vid in rel.get("Ids") or []:
                    val = bmap.get(vid)
                    if val:
                        value_text = self._kv_text(val, bmap)
            if key_text:
                forms.append({"key": key_text, "value": value_text})
        return forms

    def _kv_text(self, block: dict[str, Any], bmap: dict[str, dict[str, Any]]) -> str:
        parts: list[str] = []
        for rel in block.get("Relationships") or []:
            if rel.get("Type") != "CHILD":
                continue
            for cid in rel.get("Ids") or []:
                child = bmap.get(cid)
                if child and child.get("BlockType") == "WORD" and child.get("Text"):
                    parts.append(child["Text"])
        return " ".join(parts).strip()
