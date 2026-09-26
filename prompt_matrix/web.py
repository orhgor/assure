"""Local web UI for Assure (PEM engine)."""

from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
import threading
import time
import uuid
import webbrowser
from pathlib import Path

from flask import (
    Flask,
    Response,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
)


def _read_text(path: str, default: str = "unknown") -> str:
    try:
        p = Path(path)
        if p.exists():
            value = p.read_text(encoding="utf-8").strip()
            return value or default
    except Exception:
        pass
    return default


try:
    from .service_auth import is_service_api_request, service_api_authorized
except ImportError:
    from service_auth import is_service_api_request, service_api_authorized

try:
    from .engine import (
        MatrixError,
        execute,
        load_matrix,
        render_prompt_detailed,
    )
    from .keys import load_keys, provider_status, save_provider_key, send_ready
    from .runtime import library_status, warm_libraries
    from .library import (
        class_version_diff,
        create_class,
        delete_class,
        delete_prompt,
        get_saved_prompt,
        learn_structure,
        library_payload,
        load_library,
        rollback_class,
        save_prompt,
        snapshot_class,
        suggest_class,
    )
    from .personas import list_personas
    from .pipelines import compile_deep_prompt, run_workflow
    from .route import route_snapshot
except ImportError:
    from engine import (
        MatrixError,
        execute,
        load_matrix,
        render_prompt_detailed,
    )
    from keys import load_keys, provider_status, save_provider_key, send_ready
    from runtime import library_status, warm_libraries
    from library import (
        class_version_diff,
        create_class,
        delete_class,
        delete_prompt,
        get_saved_prompt,
        learn_structure,
        library_payload,
        load_library,
        rollback_class,
        save_prompt,
        snapshot_class,
        suggest_class,
    )
    from personas import list_personas
    from pipelines import compile_deep_prompt, run_workflow
    from route import route_snapshot

try:
    from .paths import resource_dir
except ImportError:
    from paths import resource_dir

PACKAGE_DIR = resource_dir()
STATIC_DIR = PACKAGE_DIR / "static"
TEMPLATES_DIR = PACKAGE_DIR / "templates"
PROTOTYPE_DIR = PACKAGE_DIR.parent / "prototype"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_BASIC_USER = "admin"
DEFAULT_BASIC_PASS = "changeme"


def _server_environment() -> bool:
    """True on a deployed server (production / staging), where multi-replica
    invariants are enforced at startup instead of discovered in production."""
    return (os.environ.get("ENVIRONMENT") or "").strip().lower() in ("production", "staging")


def _check_shared_state_configuration() -> None:
    """Fail fast when a deployed server would keep state in one process.

    PostgreSQL is checked by the first query (history._new_connection). Redis
    carries rate-limit counters, the Red-Hat debounce and Celery results; without
    it every replica keeps its own copy and the limits stop meaning anything.
    ``ASSURE_ALLOW_LOCAL_STATE=1`` is the explicit single-process opt-out.
    """
    if not _server_environment():
        return
    if os.environ.get("ASSURE_ALLOW_LOCAL_STATE", "").strip().lower() in ("1", "true", "yes"):
        return
    try:
        from .services.redis_client import redis_configured
    except ImportError:
        from services.redis_client import redis_configured
    if not redis_configured():
        raise RuntimeError(
            "REDIS_URL must be set when ENVIRONMENT is production/staging (rate limits, "
            "debounce locks, task results are shared through it). Set ASSURE_ALLOW_LOCAL_STATE=1 "
            "only for a deliberate single-process deployment."
        )


def _init_sentry() -> None:
    dsn = (os.environ.get("SENTRY_DSN") or "").strip()
    if not dsn:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
    except ImportError:
        return
    sentry_sdk.init(
        dsn=dsn,
        environment=os.environ.get("ENVIRONMENT", "production"),
        integrations=[FlaskIntegration()],
        traces_sample_rate=0.1,
    )


def _plausible_enabled() -> bool:
    if os.environ.get("PLAUSIBLE_ENABLED", "").strip() == "1":
        return True
    return os.environ.get("ENVIRONMENT") == "production"


def _sentry_enabled() -> bool:
    if os.environ.get("SENTRY_ENABLED", "").strip() == "1":
        return True
    return os.environ.get("ENVIRONMENT") == "production"


def _help_url() -> str:
    raw = (os.environ.get("ASSURE_HELP_URL") or "").strip()
    if raw:
        return raw
    return "mailto:feedback@getassureai.com"


_DEFAULT_SENTRY_BROWSER_DSN = (
    "https://464429cd135c3ce0fbbcb5b78e46e639@"
    "o4512026954694656.ingest.us.sentry.io/4512026963869696"
)


def _sentry_browser_dsn() -> str:
    for key in ("SENTRY_BROWSER_DSN", "SENTRY_DSN"):
        dsn = (os.environ.get(key) or "").strip()
        if dsn:
            return dsn
    if os.environ.get("ENVIRONMENT") == "production":
        return _DEFAULT_SENTRY_BROWSER_DSN
    return ""


try:
    from .waitlist import (
        DuplicateWaitlistError,
        WaitlistUnavailableError,
        insert_waitlist,
        is_valid_email as _is_valid_email,
    )
except ImportError:
    from waitlist import (
        DuplicateWaitlistError,
        WaitlistUnavailableError,
        insert_waitlist,
        is_valid_email as _is_valid_email,
    )

CANONICAL_PUBLIC_HOST = os.environ.get("CANONICAL_HOST", "getassureai.com").strip().lower()
_LEGACY_PUBLIC_HOSTS = frozenset({"www.getassureai.com"})

BRAND = {
    "name": "Assure",
    "category": "The Deterministic Truth Engine",
    "tagline": "The enterprise standard for verified AI drafting.",
    "page_title": "Assure AI — The Deterministic Truth Engine for High-Stakes Professionals",
    "meta_description": (
        "Assure AI is the deterministic truth engine built for insurance, legal, and compliance "
        "professionals to mathematically ground every citation, exclusion, and financial figure before it ships."
    ),
    "architecture_title": "How It Works · Assure — The Deterministic Truth Engine",
    "architecture_meta_description": (
        "Why guessing fails—and how Assure turns your intent into verified documents you can ship with confidence."
    ),
}
_WAITLIST_ORIGINS = frozenset(
    {
        "https://getassureai.com",
        "https://www.getassureai.com",
        "https://app.getassureai.com",
    }
)


def _waitlist_allowed_origin(origin: str) -> str:
    """Allow production landing hosts and http(s)://127.0.0.1:* / localhost:*."""
    raw = (origin or "").strip()
    if not raw:
        return ""
    if raw in _WAITLIST_ORIGINS:
        return raw
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(raw)
    except ValueError:
        return ""
    host = (parts.hostname or "").lower()
    if parts.scheme in {"http", "https"} and host in {"127.0.0.1", "localhost"}:
        return raw
    return ""


def _apply_waitlist_cors(resp):
    origin = _waitlist_allowed_origin(request.headers.get("Origin") or "")
    if origin:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Vary"] = "Origin"
    return resp


def http_basic_user() -> str:
    return os.environ.get("PEM_HTTP_USER") or DEFAULT_BASIC_USER


def http_basic_pass() -> str:
    return os.environ.get("PEM_HTTP_PASS") or DEFAULT_BASIC_PASS


def loopback_host(host: str) -> bool:
    return host in {"127.0.0.1", "localhost", "::1"}


def http_password_is_default() -> bool:
    return http_basic_pass() == DEFAULT_BASIC_PASS


def http_auth_required(host: str) -> bool:
    """Sign-in is off on this machine unless you set a password. LAN always requires one."""
    if not loopback_host(host):
        return True
    return not http_password_is_default()


def lan_bind_with_default_password(host: str) -> bool:
    return host in {"0.0.0.0", "::"} and http_password_is_default()


def startup_lines(
    *,
    local: str,
    host: str,
    send_ready_now: bool,
    auth_on: bool,
    user: str,
) -> list[str]:
    lines = [
        "Assure is running.",
        "",
        f"Your browser should open. If it does not, go to {local}",
    ]
    if not send_ready_now:
        lines.append("First run: paste a provider key, then write a question.")
    if auth_on:
        lines.append(f"Sign-in is on. User is {user}.")
    elif not loopback_host(host):
        lines.append("Sign-in is on for this bind address.")
    lines.append("Copy stays on this computer. A Send goes only to the provider you chose.")
    return lines


def first_open_url(local: str) -> str:
    """First browser tab. Connect if no Send-ready provider is pasted yet."""
    base = local.rstrip("/")
    return base if send_ready() else f"{base}/connect"


def create_app(*, require_auth: bool = True) -> Flask:
    _check_shared_state_configuration()
    _init_sentry()
    try:
        from .pem_runner import ensure_preflight
    except ImportError:
        from pem_runner import ensure_preflight
    ensure_preflight()
    load_keys()
    try:
        from .db.connection import init_db
    except ImportError:
        from db.connection import init_db
    init_db()
    # AWS identity: .env wins, then credentials saved from the Sources panel
    # (integration_settings), then the machine role. Applied before any boto3
    # client exists so the object store and Textract share one identity.
    try:
        from .services.aws_integration import apply_to_environment as _apply_aws
    except ImportError:
        from services.aws_integration import apply_to_environment as _apply_aws
    try:
        _apply_aws()
    except Exception:
        logging.getLogger("assure").exception("aws integration: could not apply saved settings")
    try:
        from .routers.sandbox import ensure_sandbox_project
    except ImportError:
        from routers.sandbox import ensure_sandbox_project
    # The sandbox is a fixed identifier with no creation path, and its budget row
    # references `projects`; without this row its first request fails at the
    # foreign key before any model call (routers/sandbox.ensure_sandbox_project).
    ensure_sandbox_project()
    try:
        from .signals import connect_redhat_signals
    except ImportError:
        from signals import connect_redhat_signals
    connect_redhat_signals()
    try:
        from .cloud_billing import load_cloud_env
    except ImportError:
        from cloud_billing import load_cloud_env
    load_cloud_env()
    threading.Thread(target=warm_libraries, daemon=True, name="pem-libs").start()
    app = Flask(
        __name__,
        static_folder=str(STATIC_DIR),
        static_url_path="/static",
        template_folder=str(TEMPLATES_DIR),
    )

    try:
        from .rate_limits import init_app_limiter
    except ImportError:
        from rate_limits import init_app_limiter
    init_app_limiter(app)

    try:
        from .db.pipeline_cache import maybe_prune_pipeline_cache
    except ImportError:
        from db.pipeline_cache import maybe_prune_pipeline_cache
    maybe_prune_pipeline_cache()

    try:
        from flask_cors import CORS

        CORS(
            app,
            origins=os.environ.get(
                "CORS_ORIGINS",
                ",".join([
                    "https://getassureai.com",
                    "https://www.getassureai.com",
                    "https://app.getassureai.com",
                    "https://staging.getassureai.com",
                    "http://127.0.0.1:8765",
                    "http://localhost:8765",
                ]),
            ).split(","),
            allow_headers=[
                "Content-Type",
                "Authorization",
                "X-Gemini-Key",
                "X-Claude-Key",
                "X-CSRFToken",
                "X-CSRF-TOKEN",
            ],
            supports_credentials=True,
        )
    except ImportError:
        pass
    try:
        from .history import close_db
    except ImportError:
        from history import close_db

    app.teardown_appcontext(close_db)

    try:
        from .lib.logger import set_request_id
    except ImportError:
        from lib.logger import set_request_id

    app.before_request(set_request_id)

    @app.after_request
    def _static_cache_headers(resp: Response):
        if request.path.startswith("/static/"):
            if "v=" in (request.query_string or b"").decode("utf-8", errors="ignore"):
                resp.headers.setdefault("Cache-Control", "public, max-age=86400, immutable")
            elif os.environ.get("ENVIRONMENT") == "production":
                resp.headers.setdefault("Cache-Control", "public, max-age=300")
        return resp

    # Behind a load balancer (ALB, Cloudflare) the client address and scheme
    # arrive in X-Forwarded-*; without this every rate-limit bucket and every
    # audit row would carry the balancer's IP. One proxy hop is trusted.
    try:
        from werkzeug.middleware.proxy_fix import ProxyFix

        hops = int(os.environ.get("PROXY_FIX_HOPS", "1"))
        if hops > 0:
            app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops, x_host=hops, x_port=hops)
    except ImportError:
        pass

    secret = os.environ.get("PEM_SECRET_KEY") or os.environ.get("FLASK_SECRET_KEY")
    if not secret:
        if _server_environment():
            # Every replica must sign sessions with the same secret; a default
            # would also be a public one. Refuse to start rather than run with it.
            raise RuntimeError(
                "PEM_SECRET_KEY (or FLASK_SECRET_KEY) must be set when ENVIRONMENT is "
                f"{os.environ.get('ENVIRONMENT')!r}."
            )
        secret = "assure-local-dev"
    app.secret_key = secret
    try:
        from .middleware import csrf_enabled, csrf_exempt_path, register_security_guards
    except ImportError:
        from middleware import csrf_enabled, csrf_exempt_path, register_security_guards
    register_security_guards(app)
    app.config["WTF_CSRF_ENABLED"] = csrf_enabled()
    app.config["WTF_CSRF_HEADERS"] = ["X-CSRFToken", "X-CSRF-TOKEN"]
    app.config["WTF_CSRF_TIME_LIMIT"] = None
    csrf = None
    if app.config["WTF_CSRF_ENABLED"]:
        try:
            from flask_wtf.csrf import CSRFError, CSRFProtect, generate_csrf

            csrf = CSRFProtect()
            csrf.init_app(app)

            @app.errorhandler(CSRFError)
            def _csrf_error(_err):
                return jsonify({"ok": False, "error": "CSRF token missing or invalid."}), 403

            @app.context_processor
            def _csrf_token():
                return {"csrf_token": generate_csrf}
        except ImportError:
            app.config["WTF_CSRF_ENABLED"] = False
            csrf = None
    app.config["BABEL_DEFAULT_LOCALE"] = "en"
    app.config["BABEL_TRANSLATION_DIRECTORIES"] = str(PACKAGE_DIR / "translations")
    try:
        from flask_babel import Babel
    except ImportError:
        Babel = None
    try:
        from .i18n import (
            EN,
            LOCALES,
            LOCALE_LABELS,
            catalog as string_catalog,
            friendly_error,
            normalize_locale,
        )
    except ImportError:
        from i18n import (
            EN,
            LOCALES,
            LOCALE_LABELS,
            catalog as string_catalog,
            friendly_error,
            normalize_locale,
        )
    try:
        from .web_ui import protect_app
    except ImportError:
        from web_ui import protect_app
    if require_auth:
        protect_app(app)

    def _locale() -> str:
        # Flask-Babel 4 uses locale_selector=, not @babel.localeselector.
        # ?lang= wins so the header switcher can reload the Jinja catalog.
        # Then session, cookie, then the browser Accept-Language header.
        arg = request.args.get("lang")
        if arg:
            lang = normalize_locale(arg)
            session["lang"] = lang
            return lang
        if session.get("lang"):
            return normalize_locale(session.get("lang"))
        cookie = request.cookies.get("assure_lang")
        if cookie:
            return normalize_locale(cookie)
        # Default English — do not infer locale from Accept-Language (avoids mixed TR/EN UI).
        return "en"

    if Babel is not None:
        Babel(app, locale_selector=_locale)
    en_msgid_key = {}
    for key, val in EN.items():
        if isinstance(val, str) and val not in en_msgid_key:
            en_msgid_key[val] = key

    def _gettext(message):
        key = en_msgid_key.get(message)
        if key:
            return string_catalog(_locale()).get(key, message)
        if Babel is not None:
            from flask_babel import gettext as babel_gettext

            return babel_gettext(message)
        return message

    app.jinja_env.globals["gettext"] = _gettext

    @app.context_processor
    def _inject_brand():
        strings = string_catalog(_locale())
        brand = dict(BRAND)
        for key in (
            "brand.category",
            "brand.tagline",
            "brand.eyebrow",
            "brand.hero_title",
            "brand.page_title",
            "brand.meta_description",
            "brand.architecture_title",
            "brand.architecture_meta_description",
        ):
            short = key.split(".", 1)[1]
            if strings.get(key):
                brand[short] = strings[key]
        return {"brand": brand}

    try:
        from .ui_cache import APP_CSS, APP_JS
    except ImportError:
        try:
            from ui_cache import APP_CSS, APP_JS
        except ImportError:
            APP_CSS, APP_JS = "1", "1"

    @app.context_processor
    def _ui_versions():
        return {
            "css_version": APP_CSS,
            "js_version": APP_JS,
            "plausible_enabled": _plausible_enabled(),
            "sentry_enabled": _sentry_enabled(),
            "sentry_browser_dsn": _sentry_browser_dsn(),
            "help_url": _help_url(),
        }

    try:
        from .cloud_auth import (
            AuthError,
            auth_required,
            clear_user,
            clerk_configured,
            clerk_only_enabled,
            current_user_id,
            is_self_hosted,
            login_required,
            protect_request,
            require_clerk_login,
            remember_user,
            safe_next,
            template_state,
            verify_session_token,
        )
    except ImportError:
        from cloud_auth import (
            AuthError,
            auth_required,
            clear_user,
            clerk_configured,
            clerk_only_enabled,
            current_user_id,
            is_self_hosted,
            login_required,
            protect_request,
            require_clerk_login,
            remember_user,
            safe_next,
            template_state,
            verify_session_token,
        )
    try:
        from .service_auth import is_service_api_request, service_api_authorized
    except ImportError:
        from service_auth import is_service_api_request, service_api_authorized
    @app.before_request
    def _set_language_guard_locale():
        """Pin the language guard's locale to this request.

        Cleared first: the locale lives in a ContextVar that ``resolve_request_locale``
        consults before the request, and a ContextVar set during one request is
        still set when the same thread serves the next (test client; gunicorn sync
        workers). Without the reset a thread that once answered an ``en`` request
        kept ``en`` for a later body carrying ``locale: de`` — measured 2026-09-23
        by two ``/api/compile-system`` calls returning byte-identical prompts.
        """
        try:
            from .services.language_guard import resolve_request_locale, set_request_locale
        except ImportError:
            from services.language_guard import resolve_request_locale, set_request_locale
        set_request_locale(None)
        set_request_locale(resolve_request_locale())

    @app.before_request
    def _canonical_host_redirect():
        from urllib.parse import urlsplit, urlunsplit

        host = (request.host or "").split(":")[0].lower()
        if host in _LEGACY_PUBLIC_HOSTS and CANONICAL_PUBLIC_HOST:
            parts = urlsplit(request.url)
            return redirect(
                urlunsplit(("https", CANONICAL_PUBLIC_HOST, parts.path, parts.query, "")),
                code=301,
            )
        return None
    @app.before_request
    def _cloud_login():
        # Bypass service API (ingest-and-verify) - uses service token auth
        if is_service_api_request(request.path):
            if service_api_authorized(request):
                return None
            return jsonify({"error": "Missing or invalid service token."}), 401

        if not is_self_hosted() and require_clerk_login():
            return protect_request()
        return None
    def _apply_browser_api_keys() -> None:
        """Apply in-memory BYOK keys from request headers. Never logged or persisted."""
        gemini = (request.headers.get("X-Gemini-Key") or "").strip()
        claude = (request.headers.get("X-Claude-Key") or "").strip()
        if not gemini and not claude:
            return
        blob: dict[str, str] = {}
        if gemini:
            blob["GEMINI_API_KEY"] = gemini
            blob["GOOGLE_API_KEY"] = gemini
        if claude:
            blob["ANTHROPIC_API_KEY"] = claude
            blob["CLAUDE_API_KEY"] = claude
        from flask import g

        g.browser_api_keys = blob

    def _page(template: str, active: str, **extra):
        lang = _locale()
        session["lang"] = lang
        include_pk = bool(extra.pop("include_pk", False))
        try:
            from .editions import snapshot as edition_snapshot
        except ImportError:
            from editions import snapshot as edition_snapshot
        html = render_template(
            template,
            locale=lang,
            languages=LOCALE_LABELS,
            strings=string_catalog(lang),
            active=active,
            auth=template_state(include_pk=include_pk),
            edition=edition_snapshot(),
            **extra,
        )
        resp = make_response(html)
        resp.set_cookie("assure_lang", lang, max_age=60 * 60 * 24 * 365, samesite="Lax")
        if os.environ.get("ENVIRONMENT") == "production":
            resp.headers["Cache-Control"] = "no-store, max-age=0"
            resp.headers["Pragma"] = "no-cache"
        return resp

    def _landing_page(template: str):
        lang = _locale()
        session["lang"] = lang
        try:
            from .ui_cache import LANDING_CSS, LANDING_JS
        except ImportError:
            from ui_cache import LANDING_CSS, LANDING_JS
        resp = make_response(
            render_template(
                template,
                locale=lang,
                languages=LOCALE_LABELS,
                strings=string_catalog(lang),
                landing_css_version=LANDING_CSS,
                landing_js_version=LANDING_JS,
            )
        )
        resp.set_cookie("assure_lang", lang, max_age=60 * 60 * 24 * 365, samesite="Lax")
        if os.environ.get("ENVIRONMENT") == "production":
            resp.headers["Cache-Control"] = "public, max-age=300"
        return resp

    @app.get("/")
    def marketing_landing():
        return _landing_page("landing.html")

    @app.get("/favicon.ico")
    @app.get("/favicon.svg")
    def favicon():
        return send_from_directory(str(STATIC_DIR), "favicon.svg", mimetype="image/svg+xml")

    @app.get("/connect")
    @login_required
    def connect():
        return _page("connect.html", "connect")

    @app.get("/parsing")
    def parsing_page():
        """Parsure intake page: what came in, its quality, what needs attention.

        Evidence-first, per the UI brief of 2026-09-25 (assure_ui_revisions.md §4):
        quality, modality, review state and replay readiness are the visible
        signals; the parser is a small secondary badge. Reports come from
        ``db/parsure_repository.list_reports`` and are joined to the Sources vault
        rows by ``document_id`` first and ``filename`` second; when the module or
        its table is absent (older databases, concurrent rollout) the page still
        renders from the vault rows alone and every quality reads "—" with the
        reason, never a default number (docs/anti-claims.md).
        """
        try:
            from .db.substrate_repository import list_substrate_for_project
        except ImportError:
            from db.substrate_repository import list_substrate_for_project

        project_id = request.args.get("project_id") or "default"
        rows = list_substrate_for_project(project_id)

        reports: list[dict] = []
        reports_available = False
        try:
            try:
                from .db.parsure_repository import list_reports as _list_reports
            except ImportError:
                from db.parsure_repository import list_reports as _list_reports
            reports = list(_list_reports(project_id, limit=200) or [])
            reports_available = True
        except Exception:  # noqa: BLE001 — module/table absent must not break intake
            reports = []

        # Words, formatting and grouping are shared with the export routes and
        # the record page (routers/parsure_routes "Words" section) so a value
        # reads the same in a cell, a CSV and a record.
        try:
            from .routers import parsure_routes as _pv
        except ImportError:
            from routers import parsure_routes as _pv  # type: ignore

        _modality_words = _pv.MODALITY_WORDS
        _material_words = _pv.MATERIAL_WORDS
        _parser_words = _pv.PARSER_WORDS
        _flag_words = _pv.FLAG_WORDS
        _low_quality_flags = _pv.LOW_QUALITY_FLAGS
        _state_words = _pv.STATE_WORDS
        _words, _num, _score_label, _date_label = _pv.words, _pv.num, _pv.score_label, _pv.date_label
        _image_ext = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".heic", ".gif", ".bmp")

        def _plural(n, word):
            return f"{n} {word}" if n == 1 else f"{n} {word}s"

        def _fallback_modality(row):
            name = (row.get("filename") or "").lower()
            kind = (row.get("source_kind") or "").lower()
            if name.endswith((".txt", ".md")):
                return "Text"
            if name.endswith(_image_ext):
                return "Image"
            if "scan" in kind or "ocr" in kind or row.get("parser_name") == "textract":
                return "Scan"
            if name.endswith(".pdf"):
                return "Digital PDF"
            return None

        def _doc_type_label(classification):
            if not isinstance(classification, dict):
                return "Type uncertain"
            value = classification.get("override") or classification.get("document_type")
            return _pv.doc_type_label(value if isinstance(value, str) else classification)

        def _card_from(row, report):
            filename = (row or {}).get("filename") or (report or {}).get("filename") or "Untitled"
            card = {
                "id": (row or {}).get("id") or (report or {}).get("document_id") or "",
                "report_id": (report or {}).get("report_id") or "",
                "filename": filename,
                "page_count": (report or {}).get("page_count") or (row or {}).get("page_count") or 0,
                "date_label": _date_label((report or {}).get("created_at") or (row or {}).get("created_at")),
                "parser_label": _words((report or {}).get("parser_name") or (row or {}).get("parser_name"), _parser_words),
                "quality": None,
                "quality_label": "—",
                "quality_note": None,
                "doc_type_label": "Type uncertain",
                "source_label": None,
                "modality_label": None,
                "fields_review": 0,
                "fields_rejected": 0,
                "fields_total": 0,
                "fields_found": 0,
                "nothing_extracted": False,
                "conflicts": 0,
                "chips": [],
                "status": "unassessed",
                "status_label": "Not assessed",
                "primary_label": "Open",
                "primary_href": f"/?project_id={project_id}",
                "record_href": None,
                "details": None,
            }
            if not report:
                card["modality_label"] = _fallback_modality(row or {})
                card["quality_note"] = "Quality not assessed (uploaded before intake scoring)"
                return card

            summary = report.get("review_summary") or {}
            fields = list(report.get("fields") or [])
            conflicts = list(report.get("conflicts") or [])
            quality_report = report.get("quality_report") or {}
            replay = report.get("replay") or {}
            pages = list(report.get("pages") or [])
            doc_flags = [str(f) for f in (report.get("quality_flags") or [])]

            fields_review = summary.get("fields_review")
            if fields_review is None:
                fields_review = sum(1 for f in fields if f.get("review_required"))
            fields_rejected = summary.get("fields_rejected")
            if fields_rejected is None:
                fields_rejected = sum(1 for f in fields if f.get("field_state") == "rejected")
            fields_total = summary.get("fields_total")
            if fields_total is None:
                fields_total = len(fields)

            # One rule for "needs review" (field_extractor.field_needs_review,
            # via attention_counts) so this card, the summary line, the queue
            # and the data count agree; the stored summary is only the fallback
            # for a report saved without its fields.
            attention_one = _pv.attention_counts([report]) if fields else None
            card["fields_review"] = int(attention_one["fields"] if attention_one else (fields_review or 0))
            card["fields_rejected"] = int(fields_rejected or 0)
            card["fields_total"] = int(fields_total or 0)
            card["fields_found"] = int(attention_one["fields_found"] if attention_one else (summary.get("fields_found") or 0))
            card["nothing_extracted"] = bool(fields) and card["fields_found"] == 0
            card["record_href"] = f"/parsing/{card['report_id']}?project_id={project_id}" if card["report_id"] else None
            card["conflicts"] = len(conflicts)
            card["quality"] = _num(report.get("document_quality_score"))
            card["quality_label"] = _score_label(report.get("document_quality_score"))
            if card["quality"] is None:
                card["quality_note"] = "Quality not scored for this document"
            card["doc_type_label"] = _doc_type_label(report.get("classification"))
            card["source_label"] = _words(report.get("material_type"), _material_words)
            card["modality_label"] = _words(report.get("modality"), _modality_words)
            if card["source_label"] and card["source_label"] == card["modality_label"]:
                card["source_label"] = None

            # Chips: calm amber warnings, one short sentence each (brief §3F).
            chips: list[str] = []
            page_flags = {str(f) for p in pages for f in (p.get("flags") or [])}
            low_pages = [p for p in pages if _num(p.get("quality_score")) is not None and _num(p.get("quality_score")) < 0.5]
            if (card["quality"] is not None and card["quality"] < 0.5) or low_pages or (
                _low_quality_flags & (page_flags | set(doc_flags))
            ):
                chips.append("Page quality is low.")
            signature = (quality_report.get("signature") or {}) if isinstance(quality_report, dict) else {}
            sig_quality = str(signature.get("quality") or "").lower()
            if not sig_quality:
                sig_qualities = {str(f.get("signature_quality") or "").lower() for f in fields}
                sig_quality = "faint" if "faint" in sig_qualities else ("incomplete" if "incomplete" in sig_qualities else "")
            if sig_quality == "faint":
                chips.append("Signature is faint.")
            elif sig_quality == "incomplete":
                chips.append("Signature is incomplete.")
            numbers = (quality_report.get("numbers") or {}) if isinstance(quality_report, dict) else {}
            flagged_numbers = numbers.get("flagged")
            flagged_count = len(flagged_numbers) if isinstance(flagged_numbers, (list, tuple)) else int(flagged_numbers or 0)
            if flagged_count:
                chips.append("Some numbers are unclear.")
            if card["doc_type_label"] == "Type uncertain" and not card["nothing_extracted"]:
                chips.append("Document type is uncertain.")
            if "no_text" in doc_flags:
                # Deliverable of 2026-09-26: say the pages could not be read,
                # with the measured character count — never an OCR confidence
                # this code did not measure.
                text_chars = quality_report.get("text_chars") if isinstance(quality_report, dict) else None
                chips.append(
                    f"The pages could not be read ({int(text_chars)} character{'' if int(text_chars) == 1 else 's'})."
                    if isinstance(text_chars, (int, float)) else "The pages could not be read."
                )
            if replay.get("eligible") and not card["nothing_extracted"]:
                chips.append("Replay available after policy update.")
            card["chips"] = chips

            # Status ranking (brief §3H): conflict > nothing extracted > needs
            # review > ready. "Nothing extracted" is one amber fact about the
            # document (the type is the likely cause), not N red field failures.
            untyped_empty = not fields and card["doc_type_label"] == "Type uncertain"
            if card["fields_rejected"] > 0 or card["conflicts"] > 0:
                card["status"] = "conflict"
                card["status_label"] = (
                    "Conflict detected" if card["conflicts"] else _plural(card["fields_rejected"], "field") + " rejected"
                )
                card["primary_label"] = "Review"
            elif card["nothing_extracted"] or untyped_empty:
                card["status"] = "notype"
                card["status_label"] = (
                    "Nothing extracted — check the document type" if card["nothing_extracted"] else "Nothing extracted — choose the document type"
                )
                card["primary_label"] = "Check type" if card["nothing_extracted"] else "Choose type"
                card["primary_href"] = card["record_href"] or f"/?project_id={project_id}&report_id={card['report_id']}"
            elif card["fields_review"] > 0:
                card["status"] = "review"
                card["status_label"] = (
                    f"{card['fields_review']} field needs review"
                    if card["fields_review"] == 1
                    else f"{card['fields_review']} fields need review"
                )
                card["primary_label"] = "Review"
            else:
                card["status"] = "ready"
                card["status_label"] = "Ready for Assure"
                card["primary_label"] = "Send to Assure"
            if card["status"] != "notype":
                card["primary_href"] = f"/?project_id={project_id}&report_id={card['report_id']}"

            attention = [
                {
                    "label": f.get("label") or _words(f.get("name")) or "Field",
                    "state": _words(f.get("field_state"), _state_words) or "—",
                    "confidence": _score_label(f.get("extraction_confidence")),
                    "reason": f.get("reason") or "No reason recorded",
                    "basis": f.get("confidence_basis") or "—",
                }
                for f in fields
                if _pv.field_needs_review(f) or f.get("field_state") in ("rejected", "disputed", "unverified", "partial")
            ]
            page_rows = [
                {
                    "page": p.get("page"),
                    "score": _score_label(p.get("quality_score")),
                    "flags": ", ".join(_words(f, _flag_words) for f in (p.get("flags") or [])) or "No issues",
                    "basis": p.get("basis") or "",
                }
                for p in pages
            ]
            sig_basis = signature.get("basis") if isinstance(signature, dict) else None
            replay_reasons = [str(r) for r in (replay.get("reasons") or [])]
            card["details"] = {
                "pages": page_rows,
                "fields": attention,
                "signature": (_words(sig_quality) if sig_quality else None),
                "signature_basis": sig_basis,
                "numbers_flagged": flagged_count,
                "quality_summary": quality_report.get("summary") if isinstance(quality_report, dict) else None,
                "replay_eligible": bool(replay.get("eligible")),
                "replay_reasons": replay_reasons,
                "replay_history": list(replay.get("history") or []),
                "conflicts": conflicts,
                "technical": {
                    "Parser": (report.get("parser_name") or "—"),
                    "Parser version": (report.get("parser_version") or "—"),
                    "Source kind": (report.get("source_kind") or (row or {}).get("source_kind") or "—"),
                    "Modality": (report.get("modality") or "—"),
                    "Material": (report.get("material_type") or "—"),
                    "Document flags": ", ".join(doc_flags) or "—",
                    "Report": card["report_id"] or "—",
                },
            }
            return card

        by_document: dict[str, dict] = {}
        by_filename: dict[str, dict] = {}
        for rep in reports:  # newest first: the first report seen for a key wins
            doc_id = rep.get("document_id")
            if doc_id and doc_id not in by_document:
                by_document[str(doc_id)] = rep
            name = rep.get("filename")
            if name and name not in by_filename:
                by_filename[str(name)] = rep

        cards: list[dict] = []
        matched: set = set()
        for row in rows:
            rep = by_document.get(str(row.get("id") or "")) or by_filename.get(str(row.get("filename") or ""))
            if rep is not None:
                matched.add(id(rep))
            cards.append(_card_from(row, rep))
        for rep in reports:
            if id(rep) not in matched:
                cards.append(_card_from(None, rep))

        qualities = [c["quality"] for c in cards if c["quality"] is not None]
        # One unit per count (2026-09-26): documents and fields needing
        # attention come from parsure_repository.attention_counts — the same
        # helper the queue, its API counts and the data count line use.
        try:
            attention = _pv.attention_counts(reports)
        except Exception:  # noqa: BLE001 — a stubbed repository must not break intake
            attention = {"documents": sum(1 for c in cards if c["status"] in ("review", "conflict")), "fields": sum(c["fields_review"] for c in cards),
                         "nothing_extracted": sum(1 for c in cards if c["nothing_extracted"]), "fields_found": sum(c["fields_found"] for c in cards)}
        ready = sum(1 for c in cards if c["status"] == "ready")
        summary = {
            "total": len(cards),
            "pages": sum(int(c["page_count"] or 0) for c in cards),
            "avg_quality": (sum(qualities) / len(qualities)) if qualities else None,
            "avg_quality_label": _score_label(sum(qualities) / len(qualities)) if qualities else "—",
            "need_attention": int(attention["documents"]),
            "fields_review": int(attention["fields"]),
            "nothing_extracted": int(attention["nothing_extracted"]),
            "ready": ready,
            "reports_available": reports_available,
        }

        # Review queue (spec §9 item 18) and analytics (item 27) come from the
        # same repository; when either is absent (older module, missing table)
        # the section is omitted or reads "—", never a default number.
        queue = None
        analytics = None
        if reports_available:
            try:
                try:
                    from .db import parsure_repository as _parsure_repo
                except ImportError:
                    from db import parsure_repository as _parsure_repo
                queue = _parsure_repo.list_queue(project_id, limit=200)
            except Exception:  # noqa: BLE001 — queue must not break intake
                queue = None
            try:
                analytics = _parsure_repo.analytics(project_id)
            except Exception:  # noqa: BLE001
                analytics = None

        def _value_label(value):
            return _pv.value_label(None, value)

        _reason_words = _pv.reason_words
        _routing_words = _pv.ROUTING_WORDS
        _reason_labels = _pv.REASON_LABELS or {"other": "Other"}

        queue_view = None
        if queue is not None:
            rows_out = []
            folded: set = set()
            for item in queue.get("items") or []:
                state = str(item.get("field_state") or "unverified")
                dispute = item.get("dispute") or None
                rid = str(item.get("report_id") or "")
                if item.get("nothing_extracted") and state not in ("disputed", "rejected"):
                    # The N "not found" rows of a document that yielded nothing
                    # are one fact — the type is the likely cause — so they fold
                    # into one row that leads to the type selector. Counts are
                    # not changed by the fold: the header still says N fields.
                    if rid in folded:
                        continue
                    folded.add(rid)
                    type_label = _words(item.get("document_type")) if item.get("document_type") not in (None, "", "uncertain", "unknown", "other") else "an uncertain type"
                    rows_out.append(
                        {
                            "kind": "nothing_extracted",
                            "report_id": rid,
                            "field_name": "",
                            "filename": item.get("filename") or "Untitled",
                            "doc_type_label": _words(item.get("document_type")) if item.get("document_type") not in (None, "", "uncertain", "unknown", "other") else "Type uncertain",
                            "label": f"nothing extracted as {type_label}",
                            "fields_total": int(item.get("fields_total") or 0),
                            "reason": "The document type may be wrong — change it and the fields are re-read.",
                            "state": "unverified",
                            "state_label": "Nothing extracted",
                            "record_href": f"/parsing/{rid}?project_id={project_id}",
                            "review_href": f"/?project_id={project_id}&report_id={rid}",
                        }
                    )
                    continue
                rows_out.append(
                    {
                        "kind": "field",
                        "report_id": rid,
                        "field_name": item.get("field_name") or "",
                        "filename": item.get("filename") or "Untitled",
                        "doc_type_label": _words(item.get("document_type")) if item.get("document_type") not in (None, "", "uncertain", "unknown", "other") else "Type uncertain",
                        "label": item.get("label") or _words(item.get("field_name")) or "Field",
                        "value_label": _value_label(item.get("value")),
                        "has_value": item.get("value") is not None,
                        "confidence_label": _score_label(item.get("extraction_confidence")),
                        "reason": _reason_words(item.get("reason")),
                        "reason_category": _reason_labels.get(item.get("reason_category") or "other", "Other"),
                        "state": state,
                        "state_label": _words(state, _state_words) or "—",
                        "action_label": _routing_words.get(str(item.get("routing_action") or "none"), _words(item.get("routing_action")) or "—"),
                        "dispute": dispute,
                        "overdue": bool(dispute and dispute.get("overdue")),
                        "due_words": (dispute or {}).get("due_words"),
                        "review_href": f"/?project_id={project_id}&report_id={item.get('report_id')}",
                    }
                )
            queue_view = {
                "rows": rows_out,
                "total": int(queue.get("total") or 0),
                "documents": len({str(i.get("report_id")) for i in (queue.get("items") or [])}),
                "counts": queue.get("counts") or {},
                "visible": 8,
            }

        def _pct_label(rate):
            n = _num(rate)
            return f"{round(n * 100)}%" if n is not None else "—"

        analytics_view = None
        if analytics is not None:
            documents_n = int(analytics.get("documents") or 0)
            trend = list(analytics.get("trend") or [])
            points = [(i, _num(d.get("avg_quality"))) for i, d in enumerate(trend)]
            scored = [(i, q) for i, q in points if q is not None]
            width, height, pad = 560.0, 64.0, 6.0
            step = (width - 2 * pad) / max(1, len(trend) - 1)

            def _xy(i, q):
                return round(pad + i * step, 1), round(pad + (1.0 - max(0.0, min(1.0, q))) * (height - 2 * pad), 1)

            spark = None
            if scored:
                coords = [_xy(i, q) for i, q in scored]
                spark = {
                    "width": int(width),
                    "height": int(height),
                    "path": " ".join(("M" if k == 0 else "L") + f"{x} {y}" for k, (x, y) in enumerate(coords)),
                    "points": [
                        {"x": x, "y": y, "day": trend[i]["day"], "label": f"{q:.2f}", "documents": trend[i]["documents"]}
                        for (x, y), (i, q) in zip(coords, scored)
                    ],
                    "last": {"x": coords[-1][0], "y": coords[-1][1], "label": f"{scored[-1][1]:.2f}"},
                    "days_with_intake": len(scored),
                    "single": len(scored) == 1,
                }
            hist = list(analytics.get("quality_histogram") or [])
            hist_max = max([int(h.get("count") or 0) for h in hist] or [0])
            hist_view = [
                {
                    "bucket": h.get("bucket"),
                    "count": int(h.get("count") or 0),
                    "pct": (int(h.get("count") or 0) / hist_max * 100.0) if hist_max else 0.0,
                    # Tone only where it means something: the top band is what
                    # Assure accepts without a second look; the bottom two are
                    # what the cards flag as "Page quality is low."
                    "tone": "verified" if _num(h.get("low")) is not None and _num(h.get("low")) >= 0.8 else ("partial" if _num(h.get("high")) is not None and _num(h.get("high")) <= 0.4 else "neutral"),
                }
                for h in hist
            ]
            issues_src = (analytics.get("issue_distribution") or {})
            issues = [
                {"label": i.get("label") or i.get("key"), "count": int(i.get("count") or 0), "kind": "field"}
                for i in (issues_src.get("field_reasons") or [])
            ] + [
                {"label": _words(i.get("key"), _flag_words) or i.get("label"), "count": int(i.get("count") or 0), "kind": "page"}
                for i in (issues_src.get("quality_flags") or [])
            ]
            issues.sort(key=lambda i: (-i["count"], i["label"]))
            issue_max = max([i["count"] for i in issues] or [0])
            for i in issues:
                i["pct"] = (i["count"] / issue_max * 100.0) if issue_max else 0.0
            modality = [
                {"label": _words(k, _modality_words) or k, "count": int(v or 0)}
                for k, v in (analytics.get("by_modality") or {}).items()
            ]
            analytics_view = {
                "has_data": documents_n > 0,
                "documents": documents_n,
                "avg_quality_label": _score_label(analytics.get("avg_document_quality")),
                "review_rate_label": _pct_label(analytics.get("review_rate")),
                "fields_review": int(analytics.get("fields_review") or 0),
                "fields_total": int(analytics.get("fields_total") or 0),
                "disputes_open": int(analytics.get("disputes_open") or 0),
                "disputes_overdue": int(analytics.get("disputes_overdue") or 0),
                "disputes_due_24h": int(analytics.get("disputes_due_24h") or 0),
                "corrections": int(analytics.get("corrections") or 0),
                "correction_rate_label": _pct_label(analytics.get("correction_rate")),
                "replay_rate_label": _pct_label(analytics.get("replay_eligible_rate")),
                "replay_eligible": int(analytics.get("replay_eligible") or 0),
                "spark": spark,
                "trend_rows": [d for d in trend if int(d.get("documents") or 0) > 0],
                "trend_start_label": _date_label(trend[0]["day"]) if trend else "—",
                "trend_end_label": _date_label(trend[-1]["day"]) if trend else "—",
                "trend_days": int(analytics.get("trend_days") or 30),
                "histogram": hist_view,
                "unscored": int(analytics.get("quality_unscored") or 0),
                "issues": issues[:8],
                "modality": modality,
            }

        # Extracted data: one table per document type, rows = documents, columns
        # = the type's fields in taxonomy order. This answers the client's
        # question of 2026-09-25 ("how do we access the parsed information …
        # in bulk?") on the page itself; the export routes give the same rows
        # as a file. Built from the reports already loaded above — no second
        # query, and a repository without reports simply yields no tables.
        data_view = None
        if reports_available:
            groups_out = []
            docs_n = values_n = review_n = 0
            for type_key, group in _pv.group_reports_by_type(reports):
                columns = _pv.type_columns(type_key, group)
                rows_out = []
                for rep in group:
                    by_name = {str(f.get("name")): f for f in (rep.get("fields") or []) if isinstance(f, dict)}
                    cells = []
                    buckets = set()
                    for col in columns:
                        f = by_name.get(col["name"])
                        if f is None:
                            cells.append({"text": "—", "title": "Not a field of this document", "mark": "absent", "bucket": ""})
                            continue
                        full = _pv.value_label(f)
                        text = full if len(full) <= 40 else full[:39].rstrip() + "…"
                        mark, bucket = _pv.field_mark(f), _pv.field_bucket(f)
                        buckets.add(bucket)
                        if f.get("value") is not None:
                            values_n += 1
                        if _pv.field_needs_review(f):
                            review_n += 1
                        title = full if mark == "accepted" else f"{full} — {_pv.reason_words(f.get('reason'))}"
                        cells.append({"text": text, "title": title, "mark": mark, "bucket": bucket})
                    docs_n += 1
                    rid = rep.get("report_id") or ""
                    rows_out.append({
                        "report_id": rid,
                        "filename": rep.get("filename") or "Untitled",
                        "date_label": _date_label(rep.get("created_at")),
                        "review_href": f"/?project_id={project_id}&report_id={rid}",
                        "record_href": f"/parsing/{rid}?project_id={project_id}",
                        "cells": cells,
                        "buckets": " ".join(sorted(buckets)),
                        "search": " ".join([str(rep.get("filename") or "")] + [c["title"] for c in cells if c["mark"] != "absent"]).lower(),
                    })
                groups_out.append({
                    "key": type_key,
                    "label": _pv.doc_type_label(type_key),
                    "columns": columns,
                    "rows": rows_out,
                    "no_fields_note": ("No fields until the type is known" if type_key == "uncertain" else "No fields were extracted"),
                })
            data_view = {
                "groups": groups_out,
                "documents": docs_n,
                "value_count": values_n,
                "need_review": review_n,
                "has_data": any(g["columns"] for g in groups_out),
                "export_wide_href": f"/api/projects/{project_id}/parsure/export?format=csv&wide=1",
                "export_long_href": f"/api/projects/{project_id}/parsure/export?format=csv",
                "export_json_href": f"/api/projects/{project_id}/parsure/export?format=json",
            }

        return _page(
            "parsing.html",
            "parsing",
            project_id=project_id,
            documents=cards,
            summary=summary,
            queue=queue_view,
            analytics=analytics_view,
            data=data_view,
        )


    @app.get("/parsing/<report_id>")
    def parsing_record_page(report_id: str):
        """One document's record: every field with its value, state, confidence
        and reason; the text of each page; the corrections, disputes and audit
        events against it; the technical details folded away.

        Reached from the "Record" link in the Extracted data tables (client ask
        of 2026-09-25: "how do we access the parsed information?"). The project
        comes from the report itself (``parsure_repository.find_report``) and
        the ownership check runs on it before anything renders; a report that
        is not there is a calm 404 page, not an error. Page text comes from
        ``report_page_texts`` — the only place the private ``_page_texts`` is
        shown — never through the public API.
        """
        try:
            from .db import parsure_repository as _repo
            from .middleware import check_project_ownership as _check_owner
            from .routers import parsure_routes as _pv
            from .services import v1_orchestrator as _orch
        except ImportError:
            from db import parsure_repository as _repo  # type: ignore
            from middleware import check_project_ownership as _check_owner  # type: ignore
            from routers import parsure_routes as _pv  # type: ignore
            from services import v1_orchestrator as _orch  # type: ignore

        project_hint = (request.args.get("project_id") or "").strip() or None
        report = None
        try:
            report = _repo.get_report(project_hint, report_id) if project_hint else _repo.find_report(report_id)
        except Exception:  # noqa: BLE001 — a missing table reads as a missing record
            report = None
        if not report:
            resp = _page("parsure_detail.html", "parsing", missing=True, report_id=report_id, project_id=project_hint or "default")
            resp.status_code = 404
            return resp
        project_id = str(report.get("project_id") or project_hint or "default")
        denied = _check_owner(project_id)
        if denied is not None:
            return denied

        fields = [f for f in (report.get("fields") or []) if isinstance(f, dict)]
        classification = report.get("classification") if isinstance(report.get("classification"), dict) else {}
        quality_report = report.get("quality_report") if isinstance(report.get("quality_report"), dict) else {}
        replay = report.get("replay") if isinstance(report.get("replay"), dict) else {}
        laya = report.get("laya") if isinstance(report.get("laya"), dict) else {}
        verification = report.get("verification") if isinstance(report.get("verification"), dict) else {}

        def _field_row(f):
            span = f.get("source_span") if isinstance(f.get("source_span"), dict) else {}
            mark = _pv.field_mark(f)
            return {
                "name": f.get("name") or "",
                "label": f.get("label") or _pv.words(f.get("name")) or "Field",
                "value": _pv.value_label(f),
                "raw": f.get("raw") if f.get("raw") not in (None, "") and str(f.get("raw")) != _pv.value_label(f) else None,
                "mark": mark,
                "state": f.get("field_state") or "unverified",
                "state_label": _pv.words(f.get("field_state"), _pv.STATE_WORDS) or "—",
                "confidence": _pv.score_label(f.get("extraction_confidence")),
                "basis": f.get("confidence_basis") or "No basis recorded",
                "page": span.get("page") if span.get("page") is not None else None,
                "node_id": span.get("node_id") or f.get("tree_node_id") or f.get("field_source_node_id") or None,
                "reason": _pv.reason_words(f.get("reason")) if (f.get("reason") or mark != "accepted") else "",
                "action": _pv.ROUTING_WORDS.get(str(f.get("routing_action") or "none"), _pv.words(f.get("routing_action")) or "—"),
                "needs_person": _pv.field_needs_review(f),
                "corrected": bool(f.get("corrected")),
            }

        field_rows = [_field_row(f) for f in fields]
        field_rows.sort(key=lambda r: 0 if r["needs_person"] else 1)  # stable: report order within each half
        review_n = sum(1 for r in field_rows if r["needs_person"])  # the queue's rule — same number as /parsing
        found_n = _repo.fields_found(report)
        nothing_extracted = bool(fields) and found_n == 0

        try:
            texts = _repo.report_page_texts(project_id, report["report_id"]) or []
        except Exception:  # noqa: BLE001
            texts = []
        page_rows = []
        pages = [p for p in (report.get("pages") or []) if isinstance(p, dict)]
        page_numbers = [p.get("page") for p in pages] or list(range(1, len(texts) + 1))
        for idx, number in enumerate(page_numbers):
            p = pages[idx] if idx < len(pages) else {}
            text = texts[idx] if idx < len(texts) else None
            flags = [_pv.words(fl, _pv.FLAG_WORDS) for fl in (p.get("flags") or [])]
            page_rows.append({
                "page": number if number is not None else idx + 1,
                "score": _pv.score_label(p.get("quality_score")),
                "flags": ", ".join(fl for fl in flags if fl) or "No issues",
                "basis": p.get("basis") or "",
                "text": text.strip() if isinstance(text, str) and text.strip() else None,
                "chars": len(text) if isinstance(text, str) else 0,
            })

        try:
            corrections = _repo.list_corrections(project_id, report["report_id"])
            disputes = _repo.list_disputes(project_id, report_id=report["report_id"])
            events = _repo.list_events(project_id, report_id=report["report_id"], limit=500)
        except Exception:  # noqa: BLE001
            corrections, disputes, events = [], [], []
        labels = {r["name"]: r["label"] for r in field_rows}
        history = []
        for c in corrections:
            history.append({
                "at": c.get("created_at"), "kind": "correction",
                "title": f"{labels.get(c.get('field_name'), _pv.words(c.get('field_name')) or 'Field')} corrected",
                "detail": f"{_pv.value_label(None, c.get('original_value'))} → {_pv.value_label(None, c.get('corrected_value'))}"
                          + (f" — {c.get('reason')}" if c.get("reason") else ""),
                "actor": c.get("actor"),
            })
        for d in disputes:
            label = labels.get(d.get("field_name"), _pv.words(d.get("field_name")) or "Field")
            if d.get("status") == "open":
                sla = d.get("due_words") or ""
                history.append({
                    "at": d.get("opened_at"), "kind": "dispute", "overdue": bool(d.get("overdue")),
                    "title": f"{label} disputed", "detail": (d.get("reason") or "") + (f" — {sla}" if sla else ""),
                    "actor": d.get("actor"),
                })
            else:
                history.append({
                    "at": d.get("opened_at"), "kind": "dispute", "overdue": False,
                    "title": f"{label} disputed", "detail": d.get("reason") or "", "actor": d.get("actor"),
                })
                history.append({
                    "at": d.get("resolved_at"), "kind": "resolution",
                    "title": f"{label} dispute resolved", "detail": d.get("resolution") or "", "actor": d.get("actor"),
                })
        for e in events:
            if e.get("event_type") in ("field_corrected", "dispute_opened", "dispute_resolved"):
                continue  # the correction / dispute rows above carry the fuller record
            payload = e.get("payload") if isinstance(e.get("payload"), dict) else {}
            detail = ""
            et = e.get("event_type")
            if et == "field_accepted":
                detail = f"Value {_pv.value_label(None, payload.get('value'))}"
            elif et == "classification_overridden":
                detail = f"{_pv.doc_type_label(payload.get('previous'))} → {_pv.doc_type_label(payload.get('document_type'))}"
                if payload.get("reason"):
                    detail += f" — {payload['reason']}"
            elif et == "exported":
                detail = f"{str(payload.get('format') or '').upper()}" + (" (project)" if payload.get("scope") == "project" else "")
            elif et == "quality_assessed" and payload.get("document_quality_score") is not None:
                detail = f"Quality {_pv.score_label(payload.get('document_quality_score'))}"
            elif et == "classified" and payload.get("document_type"):
                detail = _pv.doc_type_label(payload.get("document_type"))
            elif et == "fields_extracted" and payload.get("fields_total") is not None:
                detail = f"{payload.get('fields_found', '—')} of {payload.get('fields_total')} found"
            field_label = labels.get(e.get("field_name")) if e.get("field_name") else None
            history.append({
                "at": e.get("created_at"), "kind": "event",
                "title": (f"{field_label} " if field_label and et == "field_accepted" else "") + (
                    "accepted" if field_label and et == "field_accepted" else _pv.EVENT_WORDS.get(str(et), _pv.words(et) or "Event")
                ),
                "detail": detail, "actor": e.get("actor"),
            })
        history.sort(key=lambda h: str(h.get("at") or ""), reverse=True)
        for h in history:
            h["at_label"] = _pv.datetime_label(h.get("at"))

        quality = _pv.num(report.get("document_quality_score"))
        quality_sentence = (quality_report.get("summary") or "").strip()
        if not quality_sentence:
            quality_sentence = "Quality not scored for this document." if quality is None else "No page issues were found."
        # The document-level fact when nothing was read: from the report when
        # the orchestrator recorded it, else derived the same way (reports
        # saved before 2026-09-26). The character count is the measured length
        # of the kept page text; when no text was kept it is unknown, not 0.
        text_chars = quality_report.get("text_chars")
        if not isinstance(text_chars, (int, float)):
            text_chars = sum(len((t or "").strip()) for t in texts) if texts else None
        extraction_sentence = quality_report.get("extraction_sentence")
        if extraction_sentence is None and (nothing_extracted or (not fields and _pv.doc_type_label(classification) == "Type uncertain")):
            extraction_sentence = _orch.extraction_sentence(classification.get("document_type"), len(fields), found_n, text_chars)
        no_text = "no_text" in {str(fl) for fl in (report.get("quality_flags") or [])}
        current_type = classification.get("document_type")
        show_notice = nothing_extracted or (not fields and _pv.doc_type_label(classification) == "Type uncertain")
        if show_notice and extraction_sentence:
            # The notice says it; the hero keeps the page-quality sentence so the
            # fact is stated once on the page.
            plain = quality_report.get("quality_sentence")
            if not isinstance(plain, str):
                plain = quality_sentence[len(extraction_sentence):].strip() if quality_sentence.startswith(extraction_sentence) else quality_sentence
            quality_sentence = plain or ("Quality not scored for this document." if quality is None else "No page issues were found.")
        # Which type's fields *are* on the page — the evidence pass, run on the
        # kept text (regex only, a few ms), so the selector proposes it and the
        # notice says how many fields that type finds.
        type_hint = None
        if show_notice and texts:
            try:
                hint = _orch.reclassify_by_evidence(current_type, None, texts)
            except Exception:  # noqa: BLE001 — a hint, never a failure
                hint = None
            if hint:
                type_hint = {"type": hint["document_type"], "label": _pv.doc_type_label(hint["document_type"]),
                             "found": hint["evidence"]["found"], "total": hint["evidence"]["total"]}
        source_label = _pv.words(report.get("material_type"), _pv.MATERIAL_WORDS)
        modality_label = _pv.words(report.get("modality"), _pv.MODALITY_WORDS)
        if source_label and source_label == modality_label:
            source_label = None
        replay_history = replay.get("history") if isinstance(replay.get("history"), list) else []
        technical = [
            ("Parser", report.get("parser_name") or "—"),
            ("Parser version", report.get("parser_version") or "—"),
            ("Verification", ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in verification.items() if v is not None) or "—"),
            ("Policy version", report.get("policy_version") or "—"),
            ("Modality", report.get("modality") or "—"),
            ("Material", report.get("material_type") or "—"),
            ("Source kind", report.get("source_kind") or "—"),
            ("Type basis", f"{classification.get('basis') or '—'} (confidence {_pv.score_label(classification.get('confidence'))})"),
            ("Laya", (
                f"suggested path {laya.get('suggested_route') or '—'}; escalate {'yes' if laya.get('escalate') else 'no'}; "
                f"human review {'yes' if laya.get('human_review') else 'no'}"
                + (f"; reasons: {', '.join(str(r) for r in laya.get('reasons') or [])}" if laya.get("reasons") else "")
                + (f"; model {laya.get('model')}" if laya.get("model") else "")
            ) if laya else "—"),
            ("Replay", (
                ("eligible" if replay.get("eligible") else "not eligible")
                + (f" — {'; '.join(str(r) for r in replay.get('reasons') or [])}" if replay.get("reasons") else "")
                + (f"; {len(replay_history)} earlier replay{'s' if len(replay_history) != 1 else ''}" if replay_history else "")
            ) if replay else "—"),
            ("Report", report.get("report_id") or "—"),
            ("Document", report.get("document_id") or "—"),
            ("Revision", report.get("revision_id") or "—"),
            ("Job", report.get("job_id") or "—"),
        ]
        record = {
            "report_id": report.get("report_id") or report_id,
            "filename": report.get("filename") or "Untitled",
            "type_label": _pv.doc_type_label(classification),
            "type_overridden": bool(classification.get("override")),
            "source_label": source_label,
            "modality_label": modality_label,
            "date_label": _pv.date_label(report.get("created_at")),
            "page_count": int(report.get("page_count") or len(page_rows) or 0),
            "quality_label": _pv.score_label(report.get("document_quality_score")),
            "quality_sentence": quality_sentence,
            "fields_total": len(field_rows),
            "fields_review": review_n,
            "fields_found": found_n,
            "nothing_extracted": nothing_extracted,
            "untyped": not fields and _pv.doc_type_label(classification) == "Type uncertain",
            "extraction_sentence": extraction_sentence,
            "no_text": no_text,
            "text_chars": int(text_chars) if isinstance(text_chars, (int, float)) else None,
            "type_options": _pv.type_options(type_hint["type"] if type_hint else current_type),
            "type_hint": type_hint,
            "type_override_url": f"/api/projects/{project_id}/parsure/{report.get('report_id')}/classification",
            "pages_open": nothing_extracted or (not fields and _pv.doc_type_label(classification) == "Type uncertain"),
            "fields": field_rows,
            "pages": page_rows,
            "has_page_text": any(p["text"] for p in page_rows),
            "history": history,
            "technical": technical,
            "conflicts": [
                (c.get("summary") or c.get("reason") or c.get("field") or "Source conflict detected") if isinstance(c, dict) else str(c)
                for c in (report.get("conflicts") or [])
            ],
            "review_href": f"/?project_id={project_id}&report_id={report.get('report_id')}",
            "export_json_href": f"/api/projects/{project_id}/parsure/{report.get('report_id')}/export?format=json",
            "export_csv_href": f"/api/projects/{project_id}/parsure/{report.get('report_id')}/export?format=csv",
            "back_href": f"/parsing?project_id={project_id}",
        }
        return _page("parsure_detail.html", "parsing", missing=False, project_id=project_id, record=record)

    @app.get("/signin")
    def signin():
        return _page(
            "auth.html",
            "signin",
            include_pk=True,
            auth_mode="signin",
            next_url=safe_next(request.args.get("next")),
            page_class="auth-page",
        )

    @app.get("/signup")
    def signup():
        return _page(
            "auth.html",
            "signup",
            include_pk=True,
            auth_mode="signup",
            next_url=safe_next(request.args.get("next")),
            page_class="auth-page",
        )

    @app.get("/signout")
    def signout():
        clear_user()
        return redirect("/signin")

    @app.get("/api/auth/config")
    def auth_config():
        return jsonify(
            {
                "required": auth_required(),
                "configured": clerk_configured(),
                "self_hosted": is_self_hosted(),
                "signed_in": bool(current_user_id()),
                # The edge reads this to decide whether shell documents need a
                # session, so the flag lives in the app's env only.
                "clerk_only": clerk_only_enabled(),
            }
        )

    @app.get("/api/auth/me")
    def auth_me():
        return jsonify(
            {
                "user_id": current_user_id() or "",
                "email": session.get("clerk_email") or "",
            }
        )

    @app.post("/api/auth/session")
    def auth_session():
        if is_self_hosted() or not clerk_configured():
            return jsonify({"error": string_catalog(_locale()).get("auth.missing")}), 400
        data = request.get_json(silent=True) or {}
        token = str(data.get("token") or "")
        try:
            user = verify_session_token(token)
        except AuthError:
            return jsonify({"error": string_catalog(_locale()).get("auth.fail")}), 401
        remember_user(user_id=user["id"], email=user.get("email") or "")
        try:
            from .cloud_billing import ensure_user
        except ImportError:
            from cloud_billing import ensure_user
        try:
            ensure_user(user["id"], user.get("email") or "")
        except Exception:
            return jsonify({"error": string_catalog(_locale()).get("billing.missing")}), 503
        return jsonify({"ok": True, "user_id": user["id"], "email": user.get("email") or ""})

    @app.post("/api/auth/logout")
    def auth_logout():
        clear_user()
        return jsonify({"ok": True})

    VALID_ROLES = ("admin", "compliance", "developer", "executive")

    @app.get("/api/user/role")
    def user_role():
        role = str(session.get("assure_role") or "compliance")
        if role not in VALID_ROLES:
            role = "compliance"
        return jsonify({"role": role, "roles": list(VALID_ROLES)})

    @app.post("/api/user/role")
    def set_user_role():
        data = request.get_json(silent=True) or {}
        role = str(data.get("role") or "").strip().lower()
        if role not in VALID_ROLES:
            return jsonify({"ok": False, "error": "Invalid role."}), 400
        session["assure_role"] = role
        return jsonify({"ok": True, "role": role})

    @app.get("/pricing")
    def pricing():
        try:
            from .cloud_billing import stripe_configured
        except ImportError:
            from cloud_billing import stripe_configured
        return _page("pricing.html", "pricing", stripe_ready=stripe_configured())

    @app.get("/privacy")
    def privacy():
        return _landing_page("landing_privacy.html")

    @app.get("/terms")
    def terms():
        return _page("terms.html", "terms")

    @app.get("/about")
    def about():
        return _page("about.html", "about")

    @app.get("/account")
    @login_required
    def account():
        try:
            from .cloud_billing import subscription_payload, stripe_configured
        except ImportError:
            from cloud_billing import subscription_payload, stripe_configured
        return _page(
            "account.html",
            "account",
            billing=subscription_payload(),
            stripe_ready=stripe_configured(),
            upgraded=request.args.get("upgraded") == "1",
        )

    @app.get("/api/subscription")
    def api_subscription():
        try:
            from .cloud_billing import subscription_payload
        except ImportError:
            from cloud_billing import subscription_payload
        return jsonify(subscription_payload())

    @app.post("/api/billing/checkout")
    def billing_checkout():
        try:
            from .cloud_billing import BillingError, create_checkout_url
        except ImportError:
            from cloud_billing import BillingError, create_checkout_url
        user_id = current_user_id()
        if not user_id:
            return jsonify({"error": string_catalog(_locale()).get("auth.fail")}), 401
        try:
            url = create_checkout_url(
                user_id=user_id,
                email=session.get("clerk_email") or "",
                origin=request.host_url.rstrip("/"),
            )
        except BillingError as exc:
            return jsonify(
                {"error": string_catalog(_locale()).get("billing.missing") or str(exc)}
            ), 400
        return jsonify({"url": url})

    @app.post("/api/billing/portal")
    def billing_portal():
        try:
            from .cloud_billing import BillingError, create_portal_url
        except ImportError:
            from cloud_billing import BillingError, create_portal_url
        user_id = current_user_id()
        if not user_id:
            return jsonify({"error": string_catalog(_locale()).get("auth.fail")}), 401
        try:
            url = create_portal_url(user_id=user_id, origin=request.host_url.rstrip("/"))
        except BillingError as exc:
            return jsonify(
                {"error": string_catalog(_locale()).get("billing.missing") or str(exc)}
            ), 400
        return jsonify({"url": url})

    @app.post("/api/webhooks/stripe")
    def stripe_webhooks():
        try:
            from .cloud_billing import BillingError, verify_stripe_payload
            from .history import upsert_user_subscription
        except ImportError:
            from cloud_billing import BillingError, verify_stripe_payload
            from history import upsert_user_subscription
        payload = request.get_data(as_text=False)
        header = request.headers.get("Stripe-Signature") or ""
        try:
            event = verify_stripe_payload(payload, header)
        except BillingError as exc:
            return jsonify({"error": str(exc)}), 400
        if str(event.get("type") or "") == "checkout.session.completed":
            obj = event.get("data", {}).get("object") or {}
            if isinstance(obj, dict):
                meta = obj.get("metadata") or {}
                user_id = str(
                    obj.get("client_reference_id")
                    or meta.get("clerk_user_id")
                    or meta.get("user_id")
                    or ""
                )
                if user_id:
                    upsert_user_subscription(user_id, "pro")
        return jsonify({"ok": True}), 200

    @app.post("/api/webhook/stripe")
    def stripe_webhook():
        try:
            from .cloud_billing import BillingError, apply_stripe_event, verify_stripe_payload
        except ImportError:
            from cloud_billing import BillingError, apply_stripe_event, verify_stripe_payload
        payload = request.get_data()
        header = request.headers.get("Stripe-Signature") or ""
        try:
            event = verify_stripe_payload(payload, header)
            apply_stripe_event(event)
        except BillingError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"ok": True})

    @app.post("/api/account/delete")
    def account_delete():
        try:
            from .cloud_billing import delete_user
        except ImportError:
            from cloud_billing import delete_user
        user_id = current_user_id()
        if not user_id:
            return jsonify({"error": string_catalog(_locale()).get("auth.fail")}), 401
        try:
            from .cloud_auth import delete_clerk_user
        except ImportError:
            from cloud_auth import delete_clerk_user
        try:
            delete_clerk_user(user_id)
        except AuthError:
            return jsonify({"error": string_catalog(_locale()).get("auth.fail")}), 503
        delete_user(user_id)
        clear_user()
        return jsonify({"ok": True})

    @app.get("/api/account/export")
    def account_export():
        try:
            from .cloud_billing import export_user
        except ImportError:
            from cloud_billing import export_user
        user_id = current_user_id()
        if not user_id:
            return jsonify({"error": string_catalog(_locale()).get("auth.fail")}), 401
        return jsonify(export_user(user_id))

    @app.get("/account/usage")
    @login_required
    def usage_page():
        user_id = current_user_id()
        try:
            from .credit_guard import usage_payload
        except ImportError:
            from credit_guard import usage_payload
        payload = usage_payload(user_id)
        return _page("usage.html", "usage", usage=payload)

    @app.get("/api/usage")
    @login_required
    def api_usage():
        user_id = current_user_id()
        try:
            from .credit_guard import usage_payload
        except ImportError:
            from credit_guard import usage_payload
        return jsonify(usage_payload(user_id))

    @app.get("/api/settings")
    @login_required
    def get_settings_view():
        user_id = current_user_id()
        if not user_id:
            return jsonify({"api_keys": {}, "preferences": {}})
        try:
            from .credit_guard import ensure_wallet
            from .user_settings import get_settings
        except ImportError:
            from credit_guard import ensure_wallet
            from user_settings import get_settings
        try:
            ensure_wallet(user_id)
            return jsonify(get_settings(user_id))
        except Exception:
            return jsonify({"api_keys": {}, "preferences": {}})

    @app.post("/api/settings")
    @login_required
    def post_settings_view():
        user_id = current_user_id()
        if not user_id:
            return jsonify({"error": string_catalog(_locale()).get("auth.fail")}), 401
        data = request.get_json(silent=True) or {}
        try:
            from .credit_guard import ensure_wallet
            from .user_settings import save_settings
        except ImportError:
            from credit_guard import ensure_wallet
            from user_settings import save_settings
        try:
            ensure_wallet(user_id)
            save_settings(
                user_id,
                api_keys=data.get("api_keys") if "api_keys" in data else None,
                preferences=data.get("preferences") if "preferences" in data else None,
            )
        except Exception:
            return jsonify({"error": string_catalog(_locale()).get("billing.missing")}), 503
        return jsonify({"ok": True})

    @app.get("/api/i18n")
    def i18n_view():
        lang = _locale()
        if request.args.get("lang"):
            lang = normalize_locale(request.args.get("lang"))
            session["lang"] = lang
        payload = {
            "locale": lang,
            "locales": [{"id": item, "label": LOCALE_LABELS.get(item, item)} for item in LOCALES],
            "strings": string_catalog(lang),
        }
        resp = jsonify(payload)
        resp.set_cookie("assure_lang", lang, max_age=60 * 60 * 24 * 365, samesite="Lax")
        return resp

    @app.get("/api/health")
    def health():
        payload = {"status": "ok"}
        try:
            from .runtime import library_status
        except ImportError:
            from runtime import library_status
        try:
            payload.update(library_status())
        except Exception:
            pass
        # Add build identity for immutable deploy verification
        payload["build_sha"] = os.getenv("ASSURE_BUILD_SHA") or os.getenv("BUILD_SHA") or _read_text("/app/ASSURE_BUILD_SHA") or _read_text("/app/BUILD_SHA")
        payload["build_branch"] = os.getenv("ASSURE_BUILD_BRANCH") or os.getenv("BUILD_BRANCH") or _read_text("/app/ASSURE_BUILD_BRANCH") or _read_text("/app/BUILD_BRANCH")
        payload["build_time"] = os.getenv("ASSURE_BUILD_TIME") or os.getenv("BUILD_TIME") or _read_text("/app/ASSURE_BUILD_TIME") or _read_text("/app/BUILD_TIME")
        payload["image_ref"] = os.getenv("APP_IMAGE", "unknown")
        # Audit rows this process failed to write. The insert is best-effort, so
        # without this the loss is only in the service log; here it is a number a
        # probe or a human can read.
        try:
            from .lib.logger import audit_drop_count
        except ImportError:
            from lib.logger import audit_drop_count
        payload["audit_drops"] = audit_drop_count()
        # Pipeline-cache rows a write was refused — a project_id no `projects` row
        # owns. The insert is best-effort, so without this the loss is only in the
        # service log; here it is a number a probe or a human can read. Same idiom
        # as the audit-row counter on f674370.
        try:
            from .lib.logger import cache_drop_count
        except ImportError:
            from lib.logger import cache_drop_count
        payload["cache_drops"] = cache_drop_count()
        # Connections this process has taken from SQLite and not given back. Both
        # counters above are cumulative counts of writes that did not land; this one
        # is the resource those failures used to leave behind — a connection held
        # open with its statement uncommitted is what makes the *next* writer fail,
        # which is the family the two of them are instances of. A gauge, so it falls
        # as well as rises: it reads 0 with nothing in flight, and a number that
        # only grows is a leak that no longer needs a stack sample to find.
        try:
            from .db.open_connections import db_open_connection_count
        except ImportError:
            from db.open_connections import db_open_connection_count
        payload["db_open_connections"] = db_open_connection_count()
        return jsonify(payload), 200

    @app.post("/api/upload/validate")
    @login_required
    def upload_validate_view():
        """Validate Substrate Vault attachment size and PDF page count."""
        try:
            from .upload_limits import UploadRejectedError, validate_upload_bytes
        except ImportError:
            from upload_limits import UploadRejectedError, validate_upload_bytes
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            return jsonify({"error": "No file uploaded."}), 400
        try:
            meta = validate_upload_bytes(upload.filename, upload.read())
        except UploadRejectedError as exc:
            return jsonify({"error": str(exc)}), exc.http_status
        return jsonify({"ok": True, **meta})

    @app.route("/api/waitlist", methods=["POST", "OPTIONS"])
    def waitlist():
        if request.method == "OPTIONS":
            return _apply_waitlist_cors(make_response("", 204))
        data = request.get_json(silent=True) or {}
        name = str(data.get("name") or "").strip()
        email = str(data.get("email") or "").strip()
        if not name:
            return _apply_waitlist_cors(jsonify({"error": "Full name is required."})), 400
        if not email or not _is_valid_email(email):
            return _apply_waitlist_cors(jsonify({"error": "Enter a valid email address."})), 400
        try:
            insert_waitlist(name, email)
        except DuplicateWaitlistError:
            return _apply_waitlist_cors(jsonify({"status": "ok"}))
        except WaitlistUnavailableError as exc:
            msg = str(exc) or "The waitlist is not open yet. Try again later."
            return _apply_waitlist_cors(jsonify({"error": msg})), 503
        return _apply_waitlist_cors(jsonify({"status": "ok"}))

    @app.get("/api/status")
    def status_view():
        return jsonify(provider_status())

    @app.post("/api/keys")
    def save_key_view():
        data = request.get_json(silent=True) or {}
        try:
            payload = save_provider_key(
                str(data.get("target") or ""),
                str(data.get("key") or ""),
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(payload)

    @app.get("/api/catalog")
    def catalog():
        config = load_matrix()
        targets = []
        for key, target in config.targets.items():
            targets.append(
                {
                    "id": key,
                    "label": target.label or key,
                    "wrapper": target.wrapper,
                    "model": target.model,
                }
            )
        try:
            from .editions import snapshot
            from .i18n import intent_plain
        except ImportError:
            from editions import snapshot
            from i18n import intent_plain
        lang = _locale()
        edition = snapshot(config.runtime.edition)
        plains = intent_plain(lang)
        intents = []
        for key, intent in config.intents.items():
            intents.append(
                {
                    "id": key,
                    "label": key,
                    "role": intent.role,
                    "output_format": intent.output_format,
                    "plain": plains.get(key, ""),
                }
            )
        return jsonify(
            {
                "product": "Assure",
                "engine": "PEM",
                "targets": targets,
                "intents": intents,
                "personas": list_personas(include_locked=True),
                "route": route_snapshot(),
                "edition": edition,
                "locale": lang,
            }
        )

    @app.post("/api/intent")
    def intent_view():
        data = request.get_json(silent=True) or {}
        task = str(data.get("task") or "")
        try:
            from .intent_detector import detect_intent_payload
        except ImportError:
            from intent_detector import detect_intent_payload
        return jsonify(detect_intent_payload(task))

    @app.post("/api/preview")
    def preview_view():
        data = request.get_json(silent=True) or {}
        try:
            from .upload_limits import UploadRejectedError, validate_file_context_payload
        except ImportError:
            from upload_limits import UploadRejectedError, validate_file_context_payload
        file_context = str(data.get("file_context") or "").strip()
        if file_context:
            try:
                validate_file_context_payload(file_context, data.get("upload_meta"))
            except UploadRejectedError as exc:
                return jsonify({"error": str(exc)}), exc.http_status
        target = str(data.get("target_ai") or data.get("target") or "").strip()
        task = str(data.get("task") or "").strip()
        context = str(data.get("context") or "")
        class_id = (data.get("class_id") or "").strip() or None
        audience = str(data.get("audience") or "general").strip().lower() or "general"
        intent_raw = str(data.get("intent") or "").strip()
        try:
            from .intent_detector import FALLBACK, detect_intent
        except ImportError:
            from intent_detector import FALLBACK, detect_intent
        intent = intent_raw if intent_raw and intent_raw != "auto" else detect_intent(task)
        if not intent:
            intent = FALLBACK
        if not task or not target:
            return jsonify({"prompt": "", "intent": intent, "target_ai": target})
        try:
            rendered = compile_deep_prompt(
                task,
                intent,
                context,
                target_ai=target,
                params={"audience": audience},
                class_id=class_id,
            )
        except MatrixError as exc:
            return jsonify({"error": friendly_error(str(exc), _locale()), "intent": intent}), 400
        return jsonify(
            {
                "prompt": rendered.prompt,
                "intent": rendered.intent,
                "target_ai": rendered.target_ai,
                "files_read": rendered.files_read,
            }
        )

    @app.route("/api/compile-system", methods=["GET", "POST"])
    def compile_system_view():
        """The system message the compile path sends, for the ask it is compiling.

        Consumed by the prototype shell panel so it can show the model what the
        model receives. Inputs are the compile's own: ``intent``, ``icp_profile``
        (alias ``icpProfile``, the same aliases ``POST /draft`` accepts) and the
        request locale (``locale``/``lang`` in the body or query, else session,
        cookie, Accept-Language — ``language_guard.resolve_request_locale``).
        ``project_id`` is accepted for symmetry with the compile call and is
        echoed; the ICP profile is per request, not per project (``DraftPayload.
        icp_profile``), so nothing is looked up from it. The prompt is built by
        ``routers/draft.compile_system_as_sent`` — the pipeline's own composition
        including the language guard — so the pane equals the system turn for the
        same inputs. A body carrying no ask returns the static message, unchanged."""
        from .routers.draft import _COMPILE_SYSTEM, compile_system_as_sent
        from .services.answer_shape import choose_shape
        from .services.language_guard import resolve_request_locale

        data = request.get_json(silent=True) or {}
        if not data and request.args:
            data = request.args.to_dict()
        intent = str(data.get("intent") or "").strip()
        if not intent:
            return jsonify({"prompt": _COMPILE_SYSTEM})
        icp_profile = data.get("icp_profile") or data.get("icpProfile") or None
        icp_profile = str(icp_profile).strip() or None if icp_profile else None
        locale = resolve_request_locale()
        return jsonify(
            {
                "prompt": compile_system_as_sent(intent, icp_profile, locale),
                "answer_shape": choose_shape(intent),
                "icp_profile": icp_profile,
                "locale": locale,
                "project_id": data.get("project_id") or data.get("projectId"),
            }
        )

    @app.post("/api/render")
    @login_required
    def render_view():
        data = request.get_json(silent=True) or {}
        try:
            from .upload_limits import UploadRejectedError, validate_file_context_payload
        except ImportError:
            from upload_limits import UploadRejectedError, validate_file_context_payload
        file_context = str(data.get("file_context") or "").strip()
        if file_context:
            try:
                validate_file_context_payload(file_context, data.get("upload_meta"))
            except UploadRejectedError as exc:
                return jsonify({"error": friendly_error(str(exc), _locale())}), exc.http_status
        target = str(data.get("target_ai") or data.get("target") or "").strip()
        intent = str(data.get("intent") or "").strip()
        task = str(data.get("task") or "").strip()
        context = str(data.get("context") or "")
        direct = bool(data.get("direct"))
        # Clipboard is client-side (navigator.clipboard). Never copy on the server.
        copy = False
        class_id = (data.get("class_id") or "").strip() or None
        workflow = str(data.get("workflow") or "single").strip().lower()
        extra_targets = data.get("extra_targets") or []
        if not isinstance(extra_targets, list):
            extra_targets = []
        extra_targets = [str(item) for item in extra_targets]
        critic = str(data.get("critic") or "").strip() or None
        persona = str(data.get("persona") or "").strip() or None
        ground = bool(data.get("ground"))
        local = bool(data.get("local"))
        cheap = bool(data.get("cheap"))
        audience = str(data.get("audience") or "general").strip().lower() or "general"
        files_attached = bool(data.get("files_attached")) or bool(file_context)

        if not target or not intent or not task:
            return jsonify({"error": "Pick a target, an intent, and write a task."}), 400

        _apply_browser_api_keys()

        try:
            from .cloud_billing import is_cloud_mode
            from .editions import current_edition
        except ImportError:
            from cloud_billing import is_cloud_mode
            from editions import current_edition
        if is_cloud_mode() and current_edition().id == "free" and workflow == "ensemble":
            return jsonify({"error": string_catalog(_locale()).get("billing.pro_only")}), 403

        try:
            result = run_workflow(
                target,
                intent,
                task,
                context,
                workflow=workflow,
                extra_targets=extra_targets,
                critic=critic,
                persona=persona,
                ground=ground,
                direct=direct,
                copy=bool(copy),
                class_id=class_id,
                local=local,
                lint=direct,
                cheap=cheap,
                audience=audience,
            )
            suggested = suggest_class(task, intent)
        except MatrixError as exc:
            return jsonify({"error": friendly_error(str(exc), _locale())}), 400
        except RecursionError:
            return jsonify(
                {
                    "error": friendly_error(
                        "Could not search those file paths. Remove * and ** from the question, "
                        "or put paths only in Extra context.",
                        _locale(),
                    )
                }
            ), 400
        except OSError:
            return jsonify(
                {
                    "error": friendly_error(
                        "Could not search those file paths. Remove * and ** from the question, "
                        "or put paths only in Extra context.",
                        _locale(),
                    )
                }
            ), 400

        try:
            from .linter import lint_prompt
        except ImportError:
            from linter import lint_prompt
        report = lint_prompt(result.target_ai, result.prompt)

        try:
            from .editions import snapshot
        except ImportError:
            from editions import snapshot
        try:
            from .quality import (
                audit_spans,
                confidence_text,
                models_from_steps,
                models_used_from_steps,
            )
        except ImportError:
            from quality import (
                audit_spans,
                confidence_text,
                models_from_steps,
                models_used_from_steps,
            )
        models = models_from_steps(result.steps, result.target_ai)
        models_used = models_used_from_steps(result.steps)
        qdict = dict(result.quality) if isinstance(result.quality, dict) else {}
        if files_attached and file_context:
            spans = audit_spans(result.reply or "", file_context)
        else:
            spans = {"grounded_spans": [], "inferred_spans": []}
        qdict["grounded_spans"] = spans["grounded_spans"]
        qdict["inferred_spans"] = spans["inferred_spans"]
        conf = confidence_text(
            quality=qdict,
            models=models,
            workflow=result.workflow or "",
        )

        return jsonify(
            {
                "prompt": result.prompt,
                "target_ai": result.target_ai,
                "intent": result.intent,
                "wrapper": result.wrapper,
                "files_read": result.files_read,
                "class_id": result.class_id,
                "suggested_class": suggested.model_dump() if suggested else None,
                "reply": result.reply,
                "note": result.note,
                "copied": result.copied,
                "workflow": result.workflow,
                "steps": [step.model_dump() for step in result.steps],
                "persona": result.persona,
                "local": result.local,
                "direct": result.direct,
                "ground": ground,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "total_tokens": result.total_tokens,
                "tokens": result.tokens,
                "estimated_cost": result.estimated_cost,
                "routed_model": result.routed_model,
                "variation_id": result.variation_id,
                "quality": qdict or None,
                "grounded_spans": spans["grounded_spans"],
                "inferred_spans": spans["inferred_spans"],
                "files_attached": files_attached,
                "models": models,
                "models_used": models_used,
                "confidence_text": conf,
                "run_hash": result.run_hash,
                "edition": snapshot(load_matrix().runtime.edition),
                "lint": {
                    "ok": report.ok,
                    "errors": report.errors,
                    "warnings": report.warnings,
                },
            }
        )

    def _history_plan():
        try:
            from .editions import current_edition, snapshot
        except ImportError:
            from editions import current_edition, snapshot
        config = load_matrix()
        plan = current_edition(config.runtime.edition)
        return plan, snapshot(config.runtime.edition)

    @app.get("/api/history")
    @app.get("/api/history/search")
    @login_required
    def history_list():
        try:
            from .history import list_works
        except ImportError:
            from history import list_works
        plan, edition = _history_plan()
        q = str(request.args.get("q") or "")
        try:
            limit = int(request.args.get("limit") or 50)
            offset = int(request.args.get("offset") or 0)
        except ValueError:
            limit, offset = 50, 0
        payload = list_works(
            days=plan.history_days,
            full=plan.full_text_history,
            q=q,
            limit=limit,
            offset=offset,
        )
        payload["edition"] = edition
        payload["q"] = q
        return jsonify(payload)

    @app.get("/api/history/diff")
    def history_diff():
        """GET /api/history/diff?left=<hash>&right=<hash> — unified diff as text/plain."""
        try:
            from .history import diff_runs
        except ImportError:
            from history import diff_runs
        left_hash = (request.args.get("left") or "").strip()
        right_hash = (request.args.get("right") or "").strip()
        if not left_hash or not right_hash:
            return jsonify({"error": "Missing required query params: left, right"}), 400
        try:
            diff_text = diff_runs(left_hash, right_hash)
            return Response(diff_text, mimetype="text/plain")
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 404

    @app.get("/api/history/<int:item_id>")
    def history_one(item_id: int):
        try:
            from .history import get_work
        except ImportError:
            from history import get_work
        plan, edition = _history_plan()
        item = get_work(item_id, full=plan.full_text_history, days=plan.history_days)
        if not item:
            return jsonify({"error": "That item is gone."}), 404
        item["edition"] = edition
        item["can_export"] = plan.full_text_history
        item["can_refine_any"] = plan.full_text_history
        item["can_copy"] = True
        return jsonify(item)

    @app.delete("/api/history/<int:item_id>")
    def history_delete(item_id: int):
        try:
            from .history import delete_work
        except ImportError:
            from history import delete_work
        if not delete_work(item_id):
            return jsonify({"error": "That item is gone."}), 404
        return jsonify({"ok": True, "id": item_id})

    @app.delete("/api/history")
    def history_clear():
        try:
            from .history import clear_works
        except ImportError:
            from history import clear_works
        n = clear_works()
        return jsonify({"ok": True, "deleted": n})

    @app.get("/api/history/<int:item_id>/export")
    def history_export(item_id: int):
        try:
            from .history import export_work, get_work
        except ImportError:
            from history import export_work, get_work
        plan, _edition = _history_plan()
        fmt = str(request.args.get("format") or "markdown").strip().lower()
        item = get_work(item_id, full=plan.full_text_history, days=plan.history_days)
        if not item:
            return jsonify({"error": "That item is gone."}), 404
        if fmt in {"pdf", "markdown", "html", "prompty", "md"} and not plan.full_text_history:
            fmt = "plain"
        try:
            data, filename, mime = export_work(item, fmt)
        except ValueError:
            return jsonify({"error": "Unknown export format."}), 400
        resp = Response(data, mimetype=mime)
        resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        return resp

    @app.get("/api/library")
    def library_view():
        try:
            return jsonify(library_payload())
        except MatrixError as exc:
            return jsonify({"error": friendly_error(str(exc), _locale())}), 400

    @app.post("/api/library/classes")
    def create_class_view():
        data = request.get_json(silent=True) or {}
        try:
            item = create_class(
                str(data.get("name") or ""),
                description=str(data.get("description") or ""),
                role=str(data.get("role") or ""),
                output_format=str(data.get("output_format") or ""),
                structure=data.get("structure"),
                wrapper=data.get("wrapper"),
                target_hint=data.get("target_hint"),
                source_excerpt=str(data.get("source_excerpt") or ""),
            )
        except MatrixError as exc:
            return jsonify({"error": friendly_error(str(exc), _locale())}), 400
        return jsonify(item.model_dump()), 201

    @app.post("/api/library/prompts")
    def save_prompt_view():
        data = request.get_json(silent=True) or {}
        class_id = str(data.get("class_id") or "").strip()
        prompt = str(data.get("prompt") or "")
        if not class_id or not prompt.strip():
            return jsonify({"error": "Need a class and a generated prompt."}), 400
        try:
            item = save_prompt(
                class_id=class_id,
                target_ai=str(data.get("target_ai") or ""),
                intent=str(data.get("intent") or ""),
                task=str(data.get("task") or ""),
                prompt=prompt,
            )
        except MatrixError as exc:
            return jsonify({"error": friendly_error(str(exc), _locale())}), 400
        return jsonify(item.model_dump()), 201

    @app.delete("/api/library/classes/<class_id>")
    def delete_class_view(class_id: str):
        try:
            delete_class(class_id)
        except MatrixError as exc:
            return jsonify({"error": friendly_error(str(exc), _locale())}), 400
        return jsonify({"deleted": class_id})

    @app.delete("/api/library/prompts/<prompt_id>")
    def delete_prompt_view(prompt_id: str):
        try:
            delete_prompt(prompt_id)
        except MatrixError as exc:
            return jsonify({"error": friendly_error(str(exc), _locale())}), 400
        return jsonify({"deleted": prompt_id})

    @app.post("/api/library/classes/<class_id>/versions")
    def class_snapshot_view(class_id: str):
        data = request.get_json(silent=True) or {}
        try:
            item = snapshot_class(class_id, note=str(data.get("note") or ""))
        except MatrixError as exc:
            return jsonify({"error": friendly_error(str(exc), _locale())}), 400
        return jsonify(item.model_dump()), 201

    @app.post("/api/library/classes/<class_id>/rollback")
    def class_rollback_view(class_id: str):
        data = request.get_json(silent=True) or {}
        try:
            item = rollback_class(class_id, int(data.get("n")))
        except (MatrixError, TypeError, ValueError) as exc:
            return jsonify({"error": friendly_error(str(exc), _locale())}), 400
        return jsonify(item.model_dump())

    @app.get("/api/library/classes/<class_id>/diff")
    def class_diff_view(class_id: str):
        try:
            a = int(request.args.get("a") or 0)
            b = int(request.args.get("b") or 0)
            text = class_version_diff(class_id, a, b)
        except (MatrixError, TypeError, ValueError) as exc:
            return jsonify({"error": friendly_error(str(exc), _locale())}), 400
        return jsonify({"diff": text, "a": a, "b": b})

    @app.post("/api/learn")
    def learn_view():
        data = request.get_json(silent=True) or {}
        try:
            learned = learn_structure(str(data.get("prompt") or ""))
        except MatrixError as exc:
            return jsonify({"error": friendly_error(str(exc), _locale())}), 400
        return jsonify(learned.model_dump())

    @app.post("/api/copy")
    def copy_view():
        """Legacy ack for clients that already copied in the browser."""
        data = request.get_json(silent=True) or {}
        text = data.get("text")
        if not isinstance(text, str) or not text.strip():
            return jsonify({"error": "Nothing to copy."}), 400
        return jsonify({"copied": True, "client": True, "chars": len(text)})

    @app.post("/api/export")
    def export_view():
        data = request.get_json(silent=True) or {}
        try:
            from .exporters import export_class
        except ImportError:
            from exporters import export_class
        try:
            text = export_class(str(data.get("class_id") or ""), str(data.get("format") or ""))
        except MatrixError as exc:
            return jsonify({"error": friendly_error(str(exc), _locale())}), 400
        return jsonify({"text": text, "format": str(data.get("format") or "")})

    try:
        from .routers.inquire_stream import register_inquire_routes
    except ImportError:
        from routers.inquire_stream import register_inquire_routes
    register_inquire_routes(app)

    try:
        from .routers.jdf_routes import register_jdf_routes
    except ImportError:
        from routers.jdf_routes import register_jdf_routes
    register_jdf_routes(app)

    try:
        from .routers.jdf_memory_routes import register_jdf_memory_routes
    except ImportError:
        from routers.jdf_memory_routes import register_jdf_memory_routes
    register_jdf_memory_routes(app)

    try:
        from .routers.export_routes import register_export_routes
    except ImportError:
        from routers.export_routes import register_export_routes
    register_export_routes(app)

    try:
        from .routers.health import register_health_routes
    except ImportError:
        from routers.health import register_health_routes
    register_health_routes(app)

    try:
        from .routers.adoption_routes import register_adoption_routes
    except ImportError:
        from routers.adoption_routes import register_adoption_routes
    register_adoption_routes(app)

    try:
        from .routers.draft import register_draft_routes
    except ImportError:
        from routers.draft import register_draft_routes
    register_draft_routes(app)

    try:
        from .routers.project_routes import register_project_routes
    except ImportError:
        from routers.project_routes import register_project_routes
    register_project_routes(app)

    try:
        from .routers.comment_routes import register_comment_routes
    except ImportError:
        from routers.comment_routes import register_comment_routes
    register_comment_routes(app)

    try:
        from .routers.substrate import register_substrate_routes
    except ImportError:
        from routers.substrate import register_substrate_routes
    register_substrate_routes(app)
    try:
        from .routers.ingest_jobs_routes import register_ingest_jobs_routes
    except ImportError:
        from routers.ingest_jobs_routes import register_ingest_jobs_routes
    register_ingest_jobs_routes(app)
    try:
        from .routers.parsure_routes import register_parsure_routes
    except ImportError:
        from routers.parsure_routes import register_parsure_routes
    register_parsure_routes(app)
    try:
        from .routers.integrations_routes import register_integrations_routes
    except ImportError:
        from routers.integrations_routes import register_integrations_routes
    register_integrations_routes(app)

    try:
        from .routers.sandbox import register_sandbox_routes
    except ImportError:
        from routers.sandbox import register_sandbox_routes
    register_sandbox_routes(app)

    try:
        from .routers.refine_node import register_refine_node_routes
    except ImportError:
        from routers.refine_node import register_refine_node_routes
    register_refine_node_routes(app)

    try:
        from .routers.retrieval_routes import register_retrieval_routes
    except ImportError:
        from routers.retrieval_routes import register_retrieval_routes
    register_retrieval_routes(app)

    try:
        from .routers.conflict_routes import register_conflict_routes
    except ImportError:
        from routers.conflict_routes import register_conflict_routes
    register_conflict_routes(app)

    try:
        from .routers.omp_routes import register_omp_routes
    except ImportError:
        from routers.omp_routes import register_omp_routes
    register_omp_routes(app)

    try:
        from .routers.project_templates import register_project_template_routes
    except ImportError:
        from routers.project_templates import register_project_template_routes
    register_project_template_routes(app)

    try:
        from .routers.prompt_routes import register_prompt_routes
    except ImportError:
        from routers.prompt_routes import register_prompt_routes
    register_prompt_routes(app)

    try:
        from .routers.analytics import register_analytics_routes
    except ImportError:
        from routers.analytics import register_analytics_routes
    register_analytics_routes(app, page_renderer=_page)

    try:
        from .routers.feedback_routes import register_feedback_routes
    except ImportError:
        from routers.feedback_routes import register_feedback_routes
    register_feedback_routes(app, page_renderer=_page)

    try:
        from .routers.compliance_routes import register_compliance_routes
    except ImportError:
        from routers.compliance_routes import register_compliance_routes
    register_compliance_routes(app)

    try:
        from .routers.import_config_routes import (
            register_audit_log_routes,
            register_import_config_routes,
        )
    except ImportError:
        from routers.import_config_routes import (
            register_audit_log_routes,
            register_import_config_routes,
        )
    register_import_config_routes(app)

    try:
        from .routers.async_tasks_routes import register_async_task_routes
    except ImportError:
        from routers.async_tasks_routes import register_async_task_routes
    register_async_task_routes(app)
    register_audit_log_routes(app)

    try:
        from .routers.runs_routes import register_runs_routes
        from .routers.drafts_routes import register_drafts_routes
    except ImportError:
        from routers.runs_routes import register_runs_routes
        from routers.drafts_routes import register_drafts_routes
    register_runs_routes(app)
    register_drafts_routes(app)

    try:
        from .routers.locks_routes import register_locks_routes
    except ImportError:
        from routers.locks_routes import register_locks_routes
    register_locks_routes(app)

    try:
        from .routers.redhat_routes import register_redhat_routes
    except ImportError:
        from routers.redhat_routes import register_redhat_routes
    register_redhat_routes(app)

    try:
        from .routers.orchestrator_routes import register_orchestrator_routes
    except ImportError:
        from routers.orchestrator_routes import register_orchestrator_routes
    register_orchestrator_routes(app)

    try:
        from .routers.compare_routes import register_compare_routes
    except ImportError:
        from routers.compare_routes import register_compare_routes
    register_compare_routes(app)

    try:
        from .routers.polish_routes import register_polish_routes
    except ImportError:
        from routers.polish_routes import register_polish_routes
    register_polish_routes(app)

    try:
        from .routers.scan_routes import register_scan_routes
    except ImportError:
        from routers.scan_routes import register_scan_routes
    register_scan_routes(app)

    try:
        from .middleware_activity import register_activity_audit_middleware
    except ImportError:
        from middleware_activity import register_activity_audit_middleware
    register_activity_audit_middleware(app)

    @app.errorhandler(MatrixError)
    def matrix_error(exc: MatrixError):
        return jsonify({"error": friendly_error(str(exc), _locale())}), 400

    def _wants_json() -> bool:
        if request.path.startswith("/api/"):
            return True
        accept = (request.headers.get("Accept") or "").lower()
        if "application/json" in accept:
            first = accept.split(",")[0].strip()
            return "text/html" not in first
        return False

    def _localized_error(key: str, fallback: str) -> str:
        return string_catalog(_locale()).get(key, fallback)

    def _error_json(key: str, fallback: str, status: int):
        return jsonify({"error": _localized_error(key, fallback)}), status

    def _error_page(key: str, fallback: str, status: int, title_key: str, title_fallback: str):
        lang = _locale()
        session["lang"] = lang
        html = render_template(
            "error.html",
            locale=lang,
            languages=LOCALE_LABELS,
            active="",
            auth=template_state(),
            error_code=status,
            error_message=_localized_error(key, fallback),
            error_title=_localized_error(title_key, title_fallback),
        )
        resp = make_response(html, status)
        resp.set_cookie("assure_lang", lang, max_age=60 * 60 * 24 * 365, samesite="Lax")
        return resp

    @app.errorhandler(404)
    def not_found(_exc):
        if _wants_json():
            return _error_json(
                "error.not_found",
                "We couldn't find that page.",
                404,
            )
        return _error_page(
            "error.not_found",
            "We couldn't find that page.",
            404,
            "error.not_found_title",
            "Page not found",
        )

    @app.errorhandler(429)
    def rate_limited(_exc):
        if _wants_json():
            return _error_json(
                "error.rate_limit",
                "Too many requests. Please wait a moment and try again.",
                429,
            )
        return _error_page(
            "error.rate_limit",
            "Too many requests. Please wait a moment and try again.",
            429,
            "error.rate_limit",
            "Too many requests",
        )

    @app.errorhandler(500)
    def internal_error(_exc):
        if _wants_json():
            return _error_json(
                "error.internal",
                "Something went wrong on our side. We've been notified and are looking into it.",
                500,
            )
        return _error_page(
            "error.internal",
            "Something went wrong on our side. We've been notified and are looking into it.",
            500,
            "error.internal",
            "Something went wrong",
        )

    @app.errorhandler(Exception)
    def api_error(exc: Exception):
        from werkzeug.exceptions import HTTPException

        if isinstance(exc, HTTPException):
            if exc.code in (404, 429, 500):
                if exc.code == 404:
                    return not_found(exc)
                if exc.code == 429:
                    return rate_limited(exc)
                return internal_error(exc)
            if _wants_json():
                return jsonify({"error": exc.description or exc.name}), exc.code
            return exc
        if _wants_json():
            app.logger.exception(exc)
            return _error_json(
                "error.internal",
                "Something went wrong on our side. We've been notified and are looking into it.",
                500,
            )
        app.logger.exception(exc)
        return _error_page(
            "error.internal",
            "Something went wrong on our side. We've been notified and are looking into it.",
            500,
            "error.internal",
            "Something went wrong",
        )

    if csrf is not None:
        for rule in app.url_map.iter_rules():
            if csrf_exempt_path(rule.rule):
                view = app.view_functions.get(rule.endpoint)
                if view is not None:
                    csrf.exempt(view)

    @app.cli.command("prune-cache")
    def prune_cache_command() -> None:
        """Delete expired pipeline_cache rows."""
        try:
            from .db.pipeline_cache import prune_expired_pipeline_cache
        except ImportError:
            from db.pipeline_cache import prune_expired_pipeline_cache
        deleted = prune_expired_pipeline_cache()
        print(f"pruned {deleted} expired cache rows")

    return app


def _serve_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pem",
        description="Open Assure in a browser (PEM engine).",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="Bind address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port (default 8765)")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser tab")
    parser.add_argument(
        "--web",
        action="store_true",
        help="Start the web UI (default when no compile task is given)",
    )
    parser.add_argument(
        "--http-user",
        "--auth-user",
        dest="http_user",
        help="Sign-in user when a password is set (or PEM_HTTP_USER, default admin)",
    )
    parser.add_argument(
        "--http-pass",
        "--auth-pass",
        dest="http_pass",
        help="Sign-in password (or PEM_HTTP_PASS). Required on --host 0.0.0.0. Off on this machine by default.",
    )
    parser.add_argument(
        "--store-prompts",
        action="store_true",
        help="Store compiled prompts and finals after Send (PEM_STORE_PROMPTS)",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        metavar="N",
        help="LiteLLM max_tokens (PEM_MAX_TOKENS, default 4096)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        metavar="SECONDS",
        help="LiteLLM timeout in seconds (PEM_TIMEOUT_SECONDS, default 60)",
    )
    parser.add_argument(
        "--critic",
        help="Red-hat critic model, or 'rule' for a local no-API critic",
    )
    parser.add_argument(
        "--cheap",
        action="store_true",
        help="Default Send to the cheapest live model that fits length and intent (PEM_COST_ROUTE)",
    )
    parser.add_argument(
        "--edition",
        choices=["free", "pro", "team", "self-hosted", "cloud"],
        help="Assure edition (or ASSURE_EDITION). Default free.",
    )
    return parser


def serve(argv: list[str] | None = None) -> int:
    parser = _serve_parser()
    args = parser.parse_args(argv)

    port = _first_free_port(args.host, args.port)
    try:
        from .engine import apply_runtime_options, load_matrix
    except ImportError:
        from engine import apply_runtime_options, load_matrix
    apply_runtime_options(
        load_matrix(),
        store_prompts=bool(getattr(args, "store_prompts", False)),
        max_tokens=getattr(args, "max_tokens", None),
        timeout=getattr(args, "timeout", None),
        critic=getattr(args, "critic", None),
        auth_user=args.http_user,
        auth_pass=args.http_pass,
        cheap=bool(getattr(args, "cheap", False)),
        edition=getattr(args, "edition", None),
    )
    from rich.console import Console
    from rich.panel import Panel

    console = Console()
    if lan_bind_with_default_password(args.host):
        console.print(
            "[bold red]Set --http-pass or PEM_HTTP_PASS before other machines can reach this.[/bold red]"
        )
        return 1
    auth_on = http_auth_required(args.host)
    app = create_app(require_auth=auth_on)
    url = f"http://{args.host}:{port}"
    local = f"http://127.0.0.1:{port}"
    lan = _lan_ip() if args.host in {"0.0.0.0", "::"} else None
    user = http_basic_user()
    open_url = first_open_url(local)
    lines = startup_lines(
        local=local,
        host=args.host,
        send_ready_now=send_ready(),
        auth_on=auth_on,
        user=user,
    )
    if args.host not in {"127.0.0.1", "localhost"}:
        lines.insert(3, url)
    if lan:
        lines.insert(3, f"http://{lan}:{port}")
    console.print(Panel("\n".join(lines), title="Assure", border_style="cyan"))

    if not args.no_browser:
        threading.Thread(target=_open_browser, args=(open_url,), daemon=True).start()

    try:
        app.run(host=args.host, port=port, debug=False, threaded=True, use_reloader=False)
    except KeyboardInterrupt:
        console.print("\nStopped.")
        return 0
    return 0


def _open_browser(url: str) -> None:
    time.sleep(0.5)
    try:
        webbrowser.open(url)
    except Exception:
        pass


def _first_free_port(host: str, start: int) -> int:
    bind_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    for port in range(start, start + 30):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((bind_host, port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"No free port in {start}-{start + 29}")


def _lan_ip() -> str | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


if __name__ == "__main__":
    raise SystemExit(serve(sys.argv[1:]))


# WSGI entry for gunicorn (`prompt_matrix.web:app`).
app = create_app(require_auth=False)
