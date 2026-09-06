"""PDF export returns valid bytes."""

from __future__ import annotations

import pytest

from tests.playwright.helpers import (
    empty_annotations,
    sample_document,
)

pytestmark = pytest.mark.playwright


def test_export_pdf_returns_pdf_bytes(workbench_page, base_url):
    page = workbench_page
    ann = empty_annotations()
    doc = sample_document(content="Paragraph for PDF export smoke test.")
    doc["document_id"] = "doc-pw-export"
    doc["meta"]["title"] = "Export PDF test"

    ok = page.evaluate(
        """async (document) => {
          const pid = window.__ASSURE_PROJECT_ID__ || 'default';
          const res = await fetch(`/api/projects/${encodeURIComponent(pid)}/jdf`, {
            method: 'PUT',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ document, mutation_type: 'PLAYWRIGHT_EXPORT_PDF' }),
          });
          return res.ok;
        }""",
        doc,
    )
    assert ok is True

    response = page.request.get(f"{base_url.rstrip('/')}/api/projects/default/export?format=pdf")
    assert response.ok
    body = response.body()
    assert len(body) > 500
    assert body[:4] == b"%PDF"
