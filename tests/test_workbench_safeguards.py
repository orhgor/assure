"""Workbench production safeguards: mobile lockout, tab guard, session limit."""

from __future__ import annotations

from pathlib import Path

from prompt_matrix.i18n import CATALOGS, LOCALES
from prompt_matrix.ui_cache import APP_CSS, APP_JS

ROOT = Path(__file__).resolve().parents[1]

SAFEGUARD_KEYS = (
    "workbench.status.health_cached",
    "safeguard.mobile.title",
    "safeguard.mobile.body",
    "safeguard.tab.title",
    "safeguard.tab.body",
    "safeguard.tab.continue",
    "safeguard.session.remaining",
    "safeguard.session.limit_reached",
    "safeguard.session.tooltip",
    "unsaved.switch_project",
    "unsaved.switch_view",
    "unsaved.switch_generating",
    "audit.title",
    "audit.deck",
    "audit.description",
    "audit.tooltip",
    "audit.export_now",
    "audit.toast_exported",
    "audit.toast_share",
    "jdf.menu.hint",
    "jdf.menu.edit",
    "jdf.menu.revise",
    "jdf.menu.reprompt",
    "jdf.menu.revision",
    "jdf.menu.revise_intent",
    "jdf.menu.reprompt_intent",
    "jdf.menu.revision_intent",
)


def test_catalogs_drop_corporate_lockout_copy() -> None:
    for locale in LOCALES:
        blob = " ".join(str(v) for v in CATALOGS[locale].values())
        assert "Kurumsal" not in blob, locale
        assert "Desktop required" not in blob, locale
    for locale in LOCALES:
        cat = CATALOGS[locale]
        for key in SAFEGUARD_KEYS:
            assert key in cat, f"missing {locale} {key}"
            assert str(cat[key]).strip(), f"empty {locale} {key}"


def test_style_keeps_workbench_usable_on_mobile() -> None:
    css = (ROOT / "prompt_matrix" / "static" / "style.css").read_text(encoding="utf-8")
    assert "#mobile-lockout" in css
    assert ".mobile-hint" in css
    assert "max-width: 1024px" in css
    assert "#assure-app {\n    display: none !important;" not in css


def test_ui_cache_bumped_for_safeguards() -> None:
    assert APP_CSS == "assure-108"
    assert APP_JS == "assure-108"


# ---------------------------------------------------------------------------
# Upload queue (customer feedback 2026-09-27): several files at once, real
# upload progress over XMLHttpRequest, one drawer with a row per file.
# ---------------------------------------------------------------------------

UPLOAD_KEYS = (
    "shell.upload.title", "shell.upload.overall", "shell.upload.failed_one", "shell.upload.failed_many",
    "shell.upload.cancelled_one", "shell.upload.cancelled_many", "shell.upload.chip_one", "shell.upload.chip_many",
    "shell.upload.waiting", "shell.upload.uploading_pct", "shell.upload.uploading_bytes", "shell.upload.indexing",
    "shell.upload.ocr", "shell.upload.done", "shell.upload.failed", "shell.upload.cancelled", "shell.upload.retry",
    "shell.upload.saved", "shell.upload.figures", "shell.upload.too_large", "shell.upload.type_source",
    "shell.upload.type_ingest", "shell.upload.network",
)


def test_shell_file_inputs_take_several_files() -> None:
    html = (ROOT / "prototype" / "index.html").read_text(encoding="utf-8")
    import re

    for input_id in ("source-file-input", "dock-ingest-file"):
        tag = re.search(r"<input[^>]*id=\"%s\"[^>]*>" % input_id, html)
        assert tag, input_id
        assert " multiple" in tag.group(0), input_id
    # The drawer and its chip are in the markup, outside the blocking modal layer.
    assert 'id="upload-modal"' in html and 'role="dialog"' in html.split('id="upload-modal"', 1)[1].split(">", 1)[0]
    assert 'id="upload-list"' in html and 'id="upload-overall"' in html and 'id="upload-close"' in html
    assert 'id="upload-chip"' in html
    assert html.index('id="upload-modal"') > html.index('id="modal-layer"')


def test_shell_uploads_over_xhr_with_progress_and_the_same_routes() -> None:
    js = (ROOT / "prototype" / "shell.js").read_text(encoding="utf-8")
    assert "new XMLHttpRequest()" in js
    assert "xhr.upload.onprogress" in js
    assert 'xhr.open("POST", url, true)' in js and "xhr.withCredentials = true" in js
    # The routes did not move: the Sources vault upload and the dock's ingest.
    assert '"/substrate/upload"' in js and '"/jdf/ingest"' in js
    # The 202 + task_id contract is still polled through the one task poller.
    assert "r.status === 202 && r.j.task_id" in js and "_awaitTask(r.j.task_id" in js
    # Three at a time; the client-side ceiling matches upload_limits.MAX_FILE_SIZE_MB.
    assert "var UPLOAD_PARALLEL = 3;" in js
    from prompt_matrix.upload_limits import MAX_FILE_SIZE_MB

    assert f"var MAX_UPLOAD_MB = {MAX_FILE_SIZE_MB};" in js
    # No fetch() is left on the two upload routes.
    import re

    for route in ("/substrate/upload", "/jdf/ingest"):
        for m in re.finditer(re.escape(route), js):
            window = js[max(0, m.start() - 400): m.start()]
            assert "fetch(" not in window.split("function")[-1], route
    # Cancel aborts the request; parsing has no percentage and gets none.
    assert "xhr.abort()" in js and 'data-indeterminate", "1"' in js
    # The upload queue is upload mode for its duration.
    assert '_uploadsActive()) return "upload"' in js


def test_upload_queue_strings_in_every_locale() -> None:
    for locale in LOCALES:
        cat = CATALOGS[locale]
        for key in UPLOAD_KEYS:
            assert key in cat, f"missing {locale} {key}"
            assert str(cat[key]).strip(), f"empty {locale} {key}"
    assert "{mb}" in CATALOGS["en"]["shell.upload.too_large"]
    assert "{pct}" in CATALOGS["en"]["shell.upload.uploading_pct"] and "{sent}" in CATALOGS["en"]["shell.upload.uploading_pct"]
