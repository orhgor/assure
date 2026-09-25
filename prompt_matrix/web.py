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

        _modality_words = {
            "digital_pdf": "Digital PDF",
            "scanned_pdf": "Scan",
            "phone_photo": "Phone photo",
            "screenshot": "Screenshot",
            "handwritten": "Handwritten",
            "table_image": "Table image",
            "mixed": "Mixed bundle",
            "text": "Text",
        }
        _material_words = {
            "pdf": "PDF",
            "image": "Image",
            "photo": "Photo",
            "screenshot": "Screenshot",
            "handwritten_image": "Handwritten note",
            "table": "Table image",
            "mixed_bundle": "Mixed bundle",
            "text_file": "Text file",
        }
        _parser_words = {"jdf-cli": "JDF", "textract": "Textract", "pymupdf": "PyMuPDF", "text": "Text"}
        _flag_words = {
            "low_res": "Low resolution",
            "low_resolution": "Low resolution",
            "blurry": "Blurry",
            "low_contrast": "Low contrast",
            "skewed": "Skewed",
            "glare": "Glare",
            "noisy": "Noisy",
            "faint_signature": "Faint signature",
            "handwritten": "Handwritten",
            "no_text_layer": "No text layer",
        }
        _low_quality_flags = {"low_res", "low_resolution", "blurry", "low_contrast", "skewed", "glare", "noisy"}
        _state_words = {
            "accepted": "Accepted",
            "partial": "Partial",
            "unverified": "Unverified",
            "disputed": "Disputed",
            "rejected": "Rejected",
        }
        _image_ext = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".heic", ".gif", ".bmp")

        def _words(value, table=None):
            if value in (None, ""):
                return None
            key = str(value).strip().lower()
            if table and key in table:
                return table[key]
            return key.replace("_", " ").replace("-", " ").strip().capitalize()

        def _num(value):
            try:
                if value is None or isinstance(value, bool):
                    return None
                return float(value)
            except (TypeError, ValueError):
                return None

        def _score_label(value):
            n = _num(value)
            return f"{n:.2f}" if n is not None else "—"

        def _date_label(value):
            if value in (None, ""):
                return "—"
            dt = value
            if isinstance(value, str):
                try:
                    from datetime import datetime as _dt

                    dt = _dt.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError:
                    return value[:10]
            try:
                return f"{dt:%b} {dt.day}, {dt.year}"
            except (AttributeError, ValueError):
                return str(value)[:10]

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
            if value in (None, "", "unknown", "uncertain", "other"):
                return "Type uncertain"
            return _words(value) or "Type uncertain"

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
                "conflicts": 0,
                "chips": [],
                "status": "unassessed",
                "status_label": "Not assessed",
                "primary_label": "Open",
                "primary_href": f"/?project_id={project_id}",
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

            card["fields_review"] = int(fields_review or 0)
            card["fields_rejected"] = int(fields_rejected or 0)
            card["fields_total"] = int(fields_total or 0)
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
            if card["doc_type_label"] == "Type uncertain":
                chips.append("Document type is uncertain.")
            if replay.get("eligible"):
                chips.append("Replay available after policy update.")
            card["chips"] = chips

            # Status ranking (brief §3H): conflict > needs review > ready.
            if card["fields_rejected"] > 0 or card["conflicts"] > 0:
                card["status"] = "conflict"
                card["status_label"] = (
                    "Conflict detected" if card["conflicts"] else _plural(card["fields_rejected"], "field") + " rejected"
                )
                card["primary_label"] = "Review"
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
                if f.get("review_required") or f.get("field_state") in ("rejected", "disputed", "unverified", "partial")
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
        need_attention = sum(1 for c in cards if c["status"] in ("review", "conflict"))
        ready = sum(1 for c in cards if c["status"] == "ready")
        summary = {
            "total": len(cards),
            "pages": sum(int(c["page_count"] or 0) for c in cards),
            "avg_quality": (sum(qualities) / len(qualities)) if qualities else None,
            "avg_quality_label": f"{sum(qualities) / len(qualities):.2f}" if qualities else "—",
            "need_attention": need_attention,
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
            if value is None:
                return "—"
            if isinstance(value, bool):
                return "Yes" if value else "No"
            if isinstance(value, int):
                return f"{value:,}"
            if isinstance(value, float):
                return f"{value:,.2f}".rstrip("0").rstrip(".") if value != int(value) else f"{int(value):,}"
            return str(value)

        import re as _re

        def _reason_words(reason):
            """The decision policy's reason string in plain words for the Why column.

            The raw string stays in the card's Details table and the API; here the
            system tokens (``extraction_confidence 0.52 < 0.75``,
            ``number_quality handwritten: …``) read as a sentence fragment a
            reviewer can act on (brief §2 "Wording clarity").
            """
            if not reason:
                return "No reason recorded"
            text = str(reason).strip()
            m = _re.match(r"^(extraction|verification)_confidence\s+([0-9.]+)\s*<\s*([0-9.]+)$", text)
            if m:
                return f"{m.group(1).capitalize()} confidence {m.group(2)} is below {m.group(3)}"
            m = _re.match(r"^(number|signature)_quality\s+([a-z_]+)\s*:?\s*(.*)$", text, _re.I)
            if m:
                tail = f": {m.group(3).strip()}" if m.group(3).strip() else ""
                return f"{m.group(1).capitalize()} reads {m.group(2).replace('_', ' ')}{tail}"
            m = _re.match(r"^z3 violation\s*:\s*(.*)$", text, _re.I)
            if m:
                return f"Verification failed: {m.group(1).strip()}" if m.group(1).strip() else "Verification failed"
            m = _re.match(r"^plausibility rule '([^']+)' failed\s*:?\s*(.*)$", text, _re.I)
            if m:
                return f"Implausible ({m.group(1).replace('_', ' ')}){': ' + m.group(2).strip() if m.group(2).strip() else ''}"
            m = _re.match(r"^disputed\s*:\s*(.*)$", text, _re.I)
            if m:
                return f"Disputed: {m.group(1).strip()}"
            if text.lower() == "field not found":
                return "Not found in the document"
            if text.lower().startswith("compliance-bound"):
                return "Compliance-bound: a person must confirm it"
            return text[0].upper() + text[1:]

        _routing_words = {
            "manual_review": "Needs a reviewer",
            "adjudicator_queue": "With an adjudicator",
            "compliance_review": "Needs compliance sign-off",
            "retry_parsure": "Retry with another reader",
            "replay_later": "Waiting for replay",
            "none": "No action",
        }
        _reason_labels = {
            "disputed": "Disputed",
            "not_found": "Not found",
            "signature": "Signature",
            "number_quality": "Number quality",
            "plausibility": "Plausibility",
            "verification": "Verification",
            "compliance": "Compliance-bound",
            "low_confidence": "Low confidence",
            "other": "Other",
        }

        queue_view = None
        if queue is not None:
            rows_out = []
            for item in queue.get("items") or []:
                state = str(item.get("field_state") or "unverified")
                dispute = item.get("dispute") or None
                rows_out.append(
                    {
                        "report_id": item.get("report_id") or "",
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
                "total": int(queue.get("total") or len(rows_out)),
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

        return _page(
            "parsing.html",
            "parsing",
            project_id=project_id,
            documents=cards,
            summary=summary,
            queue=queue_view,
            analytics=analytics_view,
        )

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
