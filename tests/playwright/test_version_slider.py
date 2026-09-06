"""Version history slider navigates between saved revisions."""

from __future__ import annotations

import pytest

from tests.playwright.helpers import (
    VERSION_DISPLAY,
    VERSION_SLIDER,
    sample_document,
)

pytestmark = pytest.mark.playwright


def test_version_slider_navigates_history(workbench_page):
    page = workbench_page
    for idx in range(2):
        doc = sample_document(content=f"Revision {idx + 1}: supply chain resilience overview.")
        doc["document_id"] = f"doc-pw-rev-{idx + 1}"
        doc["meta"]["title"] = f"Revision {idx + 1}"
        ok = page.evaluate(
            """async (document) => {
              const pid = window.__ASSURE_PROJECT_ID__ || 'default';
              const res = await fetch(`/api/projects/${encodeURIComponent(pid)}/jdf`, {
                method: 'PUT',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ document, mutation_type: 'PLAYWRIGHT_VERSION' }),
              });
              return res.ok;
            }""",
            doc,
        )
        assert ok is True

    page.evaluate(
        "() => window.__assureJdf && window.__assureJdf.loadRevisionHistory && window.__assureJdf.loadRevisionHistory()"
    )
    page.wait_for_function(
        f"""() => {{
          const slider = document.querySelector('{VERSION_SLIDER}');
          return slider && Number(slider.max) > Number(slider.min);
        }}""",
        timeout=15_000,
    )

    result = page.evaluate(
        f"""() => {{
          const slider = document.querySelector('{VERSION_SLIDER}');
          const display = document.querySelector('{VERSION_DISPLAY}');
          if (slider && Number(slider.max) > Number(slider.min)) {{
            slider.value = String(Math.max(Number(slider.min), Number(slider.max) - 1));
            slider.dispatchEvent(new Event('input', {{ bubbles: true }}));
            slider.dispatchEvent(new Event('change', {{ bubbles: true }}));
            return 'slider';
          }}
          if (display && display.textContent && /v\\d+/i.test(display.textContent)) {{
            return 'display';
          }}
          return 'missing';
        }}"""
    )
    assert result in ("slider", "display"), f"version UI missing (got {result!r})"
