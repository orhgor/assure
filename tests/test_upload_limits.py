"""Upload limit validation tests."""

from __future__ import annotations

import unittest

from prompt_matrix.upload_limits import (
    MAX_FILE_SIZE_BYTES,
    MAX_PAGE_COUNT,
    UploadRejectedError,
    extract_upload_filename,
    validate_file_context_payload,
    validate_upload_bytes,
)


class UploadLimitsTests(unittest.TestCase):
    def test_validate_upload_bytes_size(self):
        with self.assertRaises(UploadRejectedError):
            validate_upload_bytes("notes.txt", b"x" * (MAX_FILE_SIZE_BYTES + 1))

    def test_validate_file_context_size(self):
        with self.assertRaises(UploadRejectedError):
            validate_file_context_payload("x" * (MAX_FILE_SIZE_BYTES + 1))

    def test_validate_pdf_page_meta(self):
        with self.assertRaises(UploadRejectedError):
            validate_file_context_payload(
                "### File: report.pdf\n```\nbody\n```\n",
                {"filename": "report.pdf", "size_bytes": 1000, "page_count": MAX_PAGE_COUNT + 1},
            )

    def test_extract_upload_filename(self):
        name = extract_upload_filename("### File: brief.pdf\n```\ntext\n```")
        self.assertEqual(name, "brief.pdf")

    def test_small_text_upload_ok(self):
        meta = validate_upload_bytes("brief.txt", b"hello")
        self.assertEqual(meta["size_bytes"], 5)
        self.assertIsNone(meta["page_count"])


if __name__ == "__main__":
    unittest.main()
