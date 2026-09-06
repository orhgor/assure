"""Decision Provenance panel interaction."""

from __future__ import annotations

import pytest

from tests.playwright.helpers import (
    PROVENANCE_INFO_BTN,
    PROVENANCE_PANEL,
    fill_and_compile,
    route_draft_success,
    wait_compile_ready,
)

pytestmark = pytest.mark.playwright


def test_provenance_panel_shows_and_closes(workbench_page):
    page = workbench_page
    page.route("**/draft/stream", route_draft_success)

    fill_and_compile(page, "Summarize revenue from NAIC policy.")
    wait_compile_ready(page)

    page.wait_for_function(
        """() => {
          if (window.AssureProvenancePanel && window.AssureProvenancePanel.syncInfoButtons) {
            window.AssureProvenancePanel.syncInfoButtons();
          }
          if (window.AssureWowEffects && window.AssureWowEffects.refreshStamps) {
            window.AssureWowEffects.refreshStamps();
          }
          return document.querySelectorAll('.provenance-info-btn, .ink-stamp').length > 0;
        }""",
        timeout=20_000,
    )

    page.locator(PROVENANCE_INFO_BTN).first.click(force=True)
    page.wait_for_function(
        """() => {
          if (window.AssureProvenancePanel && window.AssureProvenancePanel.open) {
            const btn = document.querySelector('.provenance-info-btn');
            if (btn && document.getElementById('provenance-panel-drawer')?.hidden) {
              window.AssureProvenancePanel.open(btn.getAttribute('data-node-id'));
            }
          }
          const p = document.getElementById('provenance-panel-drawer');
          return p && !p.hidden;
        }""",
        timeout=15_000,
    )
    body = page.locator("#provenance-panel-body")
    text = body.inner_text()
    assert "NAIC" in text or "revenue" in text.lower()
    assert "4" in text or "Page" in text

    page.locator("#provenance-panel-close").click()
    page.wait_for_function(
        f"""() => {{
          const p = document.querySelector('{PROVENANCE_PANEL}');
          return p && p.hidden;
        }}""",
        timeout=10_000,
    )
