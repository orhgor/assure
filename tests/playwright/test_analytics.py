"""Analytics dashboard Playwright smoke test."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.playwright


def test_analytics_dashboard_loads(page, base_url):
    page.goto(f"{base_url}/analytics", wait_until="domcontentloaded")
    page.wait_for_selector("#analytics-page", timeout=10000)
    page.wait_for_selector("#kpi-z3-pass", timeout=10000)
    title = page.locator("h1").first.inner_text()
    assert "Analytics" in title or "Analitik" in title or "分析" in title
