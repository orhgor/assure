"""JSON/YAML import UI tests."""

from __future__ import annotations

import json
import time

import pytest

pytestmark = pytest.mark.playwright


def test_import_config_button_visible(page, base_url):
    from tests.playwright.helpers import prime_page

    prime_page(page)
    page.goto(f"{base_url.rstrip('/')}/app", wait_until="domcontentloaded")
    page.locator("#command-deck-more summary").click()
    page.wait_for_selector("#jdf-import-config-btn", state="visible")
    assert page.locator("#jdf-import-config-input").count() == 1


def test_import_config_via_api_from_ui_context(page, base_url):
    from tests.playwright.helpers import prime_page

    prime_page(page)
    page.goto(f"{base_url.rstrip('/')}/app", wait_until="domcontentloaded")
    title = f"PW Config {int(time.time())}"
    create = page.request.post(
        f"{base_url.rstrip('/')}/api/projects",
        data=json.dumps({"title": title}),
        headers={"Content-Type": "application/json"},
    )
    assert create.status == 201
    pid = create.json()["id"]
    page.evaluate("(pid) => { window.__ASSURE_PROJECT_ID__ = pid; }", pid)
    import_res = page.request.post(
        f"{base_url.rstrip('/')}/api/projects/{pid}/import-config",
        data=json.dumps({"config": {"deductible_pct": 2}, "title": "Rating"}),
        headers={"Content-Type": "application/json"},
    )
    assert import_res.status == 200
    assert import_res.json().get("ok") is True
