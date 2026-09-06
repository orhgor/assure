"""Fixtures for tests/playwright — local Flask server for browser tests."""

from __future__ import annotations

import pytest

from tests.e2e.conftest import base_url, live_assure_server  # noqa: F401
from tests.playwright.helpers import goto_workbench, prime_page  # noqa: E402


@pytest.fixture
def workbench_page(page, base_url):
    """Desktop workbench on /app with onboarding dismissed."""
    prime_page(page)
    page = goto_workbench(page, base_url)
    page.evaluate(
        """() => {
          try {
            sessionStorage.setItem('assure_session_compiles', '0');
            if (window.AssureGenerate && typeof window.AssureGenerate.resetUi === 'function') {
              window.AssureGenerate.resetUi();
            }
          } catch (e) {}
        }"""
    )
    page.evaluate(
        """async () => {
          const empty = {
            document_id: 'doc-test-reset',
            meta: {},
            truth_ledger: {},
            body: [],
          };
          await fetch('/api/projects/default/jdf', {
            method: 'PUT',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ document: empty, mutation_type: 'PLAYWRIGHT_RESET' }),
          });
          if (window.__assureJdf && typeof window.__assureJdf.refreshCanvas === 'function') {
            await window.__assureJdf.refreshCanvas();
          }
        }"""
    )
    return page
