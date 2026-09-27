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
    assert APP_CSS == "assure-110"
    assert APP_JS == "assure-110"


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


# ---------------------------------------------------------------------------
# Accounts (local user management, 2026-09-27): identity in the header, the
# forced password change, permission-driven hiding, Team and Audit pages.
# ---------------------------------------------------------------------------

ACCOUNT_KEYS = (
    "shell.auth.not_allowed", "shell.auth.sign_in_required", "shell.auth.role.owner", "shell.auth.role.compliance_reviewer",
    "shell.auth.role.reviewer", "shell.auth.role.intake", "shell.auth.role.auditor", "shell.auth.change_password", "shell.auth.sign_out",
    "shell.auth.password_title", "shell.auth.password_forced", "shell.auth.password_current", "shell.auth.password_new",
    "shell.auth.password_again", "shell.auth.password_save", "shell.auth.password_mismatch", "shell.rail.team", "shell.rail.audit",
    "shell.audit.open", "shell.team.title", "shell.team.invite", "shell.team.users", "shell.team.pending", "shell.team.sessions",
    "shell.team.reset_password", "shell.team.temporary_password", "shell.team.revoke", "shell.team.copy", "shell.team.emailed",
    "shell.team.not_emailed", "shell.audit.title", "shell.audit.project", "shell.audit.actor", "shell.audit.event_type",
    "shell.audit.since", "shell.audit.time", "shell.audit.payload", "parsing.not_allowed",
)


def test_shell_markup_carries_identity_account_menu_and_rail_pages() -> None:
    html = (ROOT / "prototype" / "index.html").read_text(encoding="utf-8")
    assert 'id="shell-identity"' in html and 'id="identity-name"' in html and 'id="identity-role"' in html
    assert 'id="menu-signout"' in html and 'id="menu-password"' in html and 'id="menu-identity"' in html
    assert 'id="rail-team"' in html and 'href="/team.html"' in html and 'id="rail-audit"' in html and 'href="/audit.html"' in html
    assert 'id="history-audit-link"' in html
    # No colour block in the header: the identity is text.
    css = (ROOT / "prototype" / "shell.css").read_text(encoding="utf-8")
    identity_css = css.split(".identity {", 1)[1].split("}", 1)[0]
    assert "background" not in identity_css
    # Permission-driven hiding by body attributes shell.js writes from /api/auth/me.
    for attr in ("data-can-upload", "data-can-delete", "data-can-original", "data-can-export", "data-can-dossier", "data-can-override"):
        assert 'body[%s="0"]' % attr in css, attr


def test_shell_reads_me_and_gates_actions_by_permission() -> None:
    js = (ROOT / "prototype" / "shell.js").read_text(encoding="utf-8")
    assert 'fetch("/api/auth/me"' in js and "must_change_password" in js and '_shellModalRenderers.password' in js
    for perm in ("fields.accept", "fields.accept_compliance", "fields.correct", "fields.dispute", "disputes.resolve",
                 "classification.override", "documents.upload", "documents.delete", "documents.download_original",
                 "exports.read", "exports.dossier", "compile.run", "team.manage", "audit.read", "reports.replay"):
        assert '"%s"' % perm in js, perm
    assert 'jsonPost("/api/auth/logout"' in js and 'jsonPost("/api/auth/password"' in js
    # A 401 sends the reader to /signin and keeps where they were.
    assert 'leaveFor("/signin" + (here' in js
    # The forced change cannot be escaped.
    assert 'SHELL.ui.modal === "password" && __me && __me.must_change_password' in js


def test_team_and_audit_pages_exist_with_their_controls() -> None:
    team = (ROOT / "prototype" / "team.html").read_text(encoding="utf-8")
    for needle in ('data-page="team"', 'id="invite-form"', 'id="invite-role"', 'id="users-body"', 'id="invitations-body"', 'id="sessions-body"', 'id="page-signout"'):
        assert needle in team, needle
    audit = (ROOT / "prototype" / "audit.html").read_text(encoding="utf-8")
    for needle in ('data-page="audit"', 'id="audit-filters"', 'id="f-project"', 'id="f-actor"', 'id="f-type"', 'id="f-since"', 'id="audit-body"'):
        assert needle in audit, needle
    js = (ROOT / "prototype" / "account-pages.js").read_text(encoding="utf-8")
    for route in ("/api/team/users", "/api/team/invitations", "/api/team/sessions", "/api/audit?", "/api/auth/logout", "/reset-password"):
        assert route in js, route
    assert "temporary_password" in js and "accept_url" in js and "emailed" in js and 'window.location.replace("/signin?next="' in js
    # Nothing shown once is stored: no localStorage / sessionStorage in the account pages.
    assert "localStorage" not in js and "sessionStorage" not in js


def test_account_strings_in_every_locale() -> None:
    for locale in LOCALES:
        cat = CATALOGS[locale]
        for key in ACCOUNT_KEYS:
            assert key in cat, f"missing {locale} {key}"
            assert str(cat[key]).strip(), f"empty {locale} {key}"
    assert "{role}" in CATALOGS["en"]["shell.auth.not_allowed"] and "{perm}" in CATALOGS["en"]["shell.auth.not_allowed"]
