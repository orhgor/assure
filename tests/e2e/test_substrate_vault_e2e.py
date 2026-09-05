"""Playwright coverage for the Substrate Vault panel: upload, list, delete,
include toggle, and the ⌘U shortcut.

Follows the same live-server + pytest-playwright pattern as
``test_workbench_ux_e2e.py`` / ``test_user_simulation.py``. Textract is
mocked in-process (the live server runs in a background thread inside this
same interpreter), so no AWS calls are made.

Run locally:
  uv sync --extra dev
  uv run pytest tests/e2e/test_substrate_vault_e2e.py -v --tb=short
"""

from __future__ import annotations

import io
import os
from unittest.mock import patch

import pytest
from pypdf import PdfWriter

pytestmark = pytest.mark.playwright

VAULT = "#substrate-vault"
VAULT_LIST = "#substrate-vault-list"
VAULT_EMPTY = "#substrate-vault-empty"
VAULT_COUNT = "#substrate-vault-count"
VAULT_UPLOAD_BTN = "#substrate-vault-upload-btn"
VAULT_FILE_INPUT = "#substrate-vault-file-input"
FILE_ROW = "#substrate-vault-list .substrate-file-row"
FILE_ROW_SETTLED = "#substrate-vault-list .substrate-file-row:not(.is-processing)"
WORKBENCH = "#jdf-workbench"
COMPILE_BTN = "#generate-compile-btn"
LOCALE_SELECT = "#locale-select"
ONBOARDING_KEY = "assure_onboarding_complete"


def _app_url(base_url: str) -> str:
    env_base = os.environ.get("ASSURE_BASE_URL")
    root = (env_base or base_url).rstrip("/")
    return f"{root}/app"


def _goto_workbench(page, base_url: str):
    page.add_init_script(f"try {{ localStorage.setItem({ONBOARDING_KEY!r}, '1'); }} catch (e) {{}}")
    page.goto(_app_url(base_url), wait_until="domcontentloaded")
    page.wait_for_selector(WORKBENCH, state="visible")
    page.wait_for_function(
        "() => window.__assureJdf && typeof window.__assureJdf.render === 'function'"
    )
    page.wait_for_function("() => !document.body.classList.contains('onboarding-active')")
    page.wait_for_selector(COMPILE_BTN, state="visible")
    page.wait_for_function("() => !!window.AssureSubstrateVault")
    return page


def _single_page_pdf_path(tmp_path) -> str:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    path = tmp_path / "brief.pdf"
    path.write_bytes(buf.getvalue())
    return str(path)


def _mock_textract():
    """Patches the in-process TextractClient used by the live Flask server."""
    return patch("prompt_matrix.routers.substrate.TextractClient")


def _clear_vault(page):
    """The live server's DB is session-scoped across this file's tests — wipe
    the 'default' project's vault so each test starts from a clean list."""
    page.evaluate(
        """async () => {
            const res = await fetch('/api/projects/default/substrate');
            const data = await res.json();
            for (const f of (data.files || [])) {
                await fetch('/api/projects/default/substrate/' + f.id, { method: 'DELETE' });
            }
            if (window.AssureSubstrateVault) {
                await window.AssureSubstrateVault.fetchList();
            }
        }"""
    )


class TestVaultPanel:
    def test_empty_state_shown_by_default(self, page, base_url):
        _goto_workbench(page, base_url)
        _clear_vault(page)
        vault = page.locator(VAULT)
        assert vault.is_visible()
        assert page.locator(VAULT_EMPTY).is_visible()
        assert page.locator(VAULT_COUNT).is_hidden()

    def test_collapse_state_persists_across_reload(self, page, base_url):
        _goto_workbench(page, base_url)
        page.evaluate(
            f"try {{ localStorage.removeItem('assure_vault_collapsed'); }} catch (e) {{}}"
        )
        page.locator(f"{VAULT} summary").click()
        collapsed = page.locator(VAULT).evaluate("el => el.open")
        assert collapsed is False
        # <details> fires its native "toggle" event on a queued task, not
        # synchronously with the click — wait for the persisted write so a
        # reload right after toggling doesn't race the storage listener.
        page.wait_for_function("() => localStorage.getItem('assure_vault_collapsed') === '1'")

        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(WORKBENCH, state="visible")
        after_reload = page.locator(VAULT).evaluate("el => el.open")
        assert after_reload is False


class TestUploadFlow:
    def test_upload_adds_verified_row_and_updates_count(self, page, base_url, tmp_path):
        _goto_workbench(page, base_url)
        _clear_vault(page)
        pdf_path = _single_page_pdf_path(tmp_path)

        with _mock_textract() as mock_cls:
            instance = mock_cls.return_value
            instance._get_page_count.return_value = 1
            instance.extract_text.return_value = {
                "text": "Net income grew 12% year over year.",
                "tables": [],
                "forms": [],
                "page_count": 1,
                "filename": "brief.pdf",
            }
            page.set_input_files(VAULT_FILE_INPUT, pdf_path)
            page.wait_for_selector(FILE_ROW_SETTLED, state="visible")

        row = page.locator(FILE_ROW).first
        assert "brief.pdf" in row.inner_text()
        assert "Verified" in row.inner_text() or "\u2705" in row.inner_text()
        assert page.locator(VAULT_EMPTY).is_hidden()
        assert page.locator(VAULT_COUNT).inner_text().strip() == "1"

    def test_upload_failure_shows_toast_and_removes_optimistic_row(self, page, base_url, tmp_path):
        _goto_workbench(page, base_url)
        _clear_vault(page)
        pdf_path = _single_page_pdf_path(tmp_path)

        with _mock_textract() as mock_cls:
            instance = mock_cls.return_value
            instance._get_page_count.return_value = 1
            instance.extract_text.return_value = {
                "text": "x",
                "tables": [],
                "forms": [],
                "page_count": 1,
                "filename": "brief.pdf",
            }
            page.set_input_files(VAULT_FILE_INPUT, pdf_path)
            page.wait_for_function(f"() => document.querySelectorAll({FILE_ROW!r}).length === 0")
        assert page.locator(VAULT_EMPTY).is_visible()


class TestDeleteAndInclude:
    def _upload_one(self, page, tmp_path):
        pdf_path = _single_page_pdf_path(tmp_path)
        with _mock_textract() as mock_cls:
            instance = mock_cls.return_value
            instance._get_page_count.return_value = 1
            instance.extract_text.return_value = {
                "text": "Net income grew 12% year over year.",
                "tables": [],
                "forms": [],
                "page_count": 1,
                "filename": "brief.pdf",
            }
            page.set_input_files(VAULT_FILE_INPUT, pdf_path)
            page.wait_for_selector(FILE_ROW_SETTLED, state="visible")

    def test_unchecking_includes_persists_across_reload(self, page, base_url, tmp_path):
        _goto_workbench(page, base_url)
        _clear_vault(page)
        self._upload_one(page, tmp_path)

        checkbox = page.locator(f"{FILE_ROW} .substrate-file-checkbox").first
        assert checkbox.is_checked()
        checkbox.uncheck()
        page.wait_for_function(
            "() => window.AssureSubstrateVault.files[0] && "
            "window.AssureSubstrateVault.files[0].included === false"
        )

        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(WORKBENCH, state="visible")
        page.wait_for_selector(FILE_ROW, state="visible")
        reloaded_checkbox = page.locator(f"{FILE_ROW} .substrate-file-checkbox").first
        assert not reloaded_checkbox.is_checked()

    def test_delete_removes_row_after_confirm(self, page, base_url, tmp_path):
        _goto_workbench(page, base_url)
        _clear_vault(page)
        self._upload_one(page, tmp_path)

        page.once("dialog", lambda dialog: dialog.accept())
        page.locator(f"{FILE_ROW} .substrate-file-delete").first.click()
        page.wait_for_function(f"() => document.querySelectorAll({FILE_ROW!r}).length === 0")
        assert page.locator(VAULT_EMPTY).is_visible()

    def test_delete_cancelled_keeps_row(self, page, base_url, tmp_path):
        _goto_workbench(page, base_url)
        _clear_vault(page)
        self._upload_one(page, tmp_path)

        page.once("dialog", lambda dialog: dialog.dismiss())
        page.locator(f"{FILE_ROW} .substrate-file-delete").first.click()
        page.wait_for_timeout(200)
        assert page.locator(FILE_ROW).count() == 1


class TestShortcutAndI18n:
    def test_cmd_u_triggers_upload(self, page, base_url):
        _goto_workbench(page, base_url)
        page.locator("body").click(position={"x": 5, "y": 5})
        page.evaluate(
            "() => { window.__testUploadTriggered = false; "
            "window.AssureSubstrateVault.triggerUpload = () => { window.__testUploadTriggered = true; }; }"
        )
        mod = "Meta" if page.evaluate("() => navigator.platform.includes('Mac')") else "Control"
        page.keyboard.press(f"{mod}+U")
        triggered = page.evaluate("() => window.__testUploadTriggered")
        assert triggered is True

    def test_locale_switch_translates_empty_state(self, page, base_url):
        _goto_workbench(page, base_url)
        _clear_vault(page)
        page.select_option(LOCALE_SELECT, "tr")
        page.wait_for_function("() => document.documentElement.lang === 'tr'")
        empty_text = page.locator(VAULT_EMPTY).inner_text()
        assert "Sürükleyip" in empty_text
        assert "Drag and drop" not in empty_text
