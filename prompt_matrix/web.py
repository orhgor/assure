"""Local web UI for Assure (PEM engine)."""

from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
import uuid
import webbrowser
from pathlib import Path

from flask import Flask, Response, jsonify, make_response, redirect, render_template, request, send_from_directory, session

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
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_BASIC_USER = "admin"
DEFAULT_BASIC_PASS = "changeme"


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
        environment="production",
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
_LEGACY_PUBLIC_HOSTS = frozenset({"app.getassureai.com", "www.getassureai.com"})

BRAND = {
    "name": "Assure",
    "category": "The Intellectual Compiler",
    "tagline": "Compile intent. Verify logic. Ship truth.",
    "page_title": "Assure — The Intellectual Compiler",
    "meta_description": (
        "Assure is the first Intellectual Compiler — turning raw intent, messy documents, "
        "and unstructured data into mathematically verified, auditable deliverables."
    ),
    "architecture_title": "Architecture Deep-Dive · Assure — The Intellectual Compiler",
    "architecture_meta_description": (
        "Why flat text fails and how Assure uses JDF AST modular execution trees, "
        "Z3 SMT verification, and a six-stage pipeline for mathematical certainty."
    ),
}
_WAITLIST_ORIGINS = frozenset(
    {
        "https://getassureai.com",
        "https://www.getassureai.com",
        "https://assure.orhangorenn.workers.dev",
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
        from flask_cors import CORS

        CORS(
            app,
            origins=[
                "https://getassureai.com",
                "https://www.getassureai.com",
                "https://app.getassureai.com",
                "http://127.0.0.1:8765",
                "http://localhost:8765",
            ],
            allow_headers=[
                "Content-Type",
                "Authorization",
                "X-Gemini-Key",
                "X-Claude-Key",
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
    app.secret_key = os.environ.get("PEM_SECRET_KEY") or os.environ.get("FLASK_SECRET_KEY") or "assure-local-dev"
    app.config["BABEL_DEFAULT_LOCALE"] = "en"
    app.config["BABEL_TRANSLATION_DIRECTORIES"] = str(PACKAGE_DIR / "translations")
    try:
        from flask_babel import Babel
    except ImportError:
        Babel = None
    try:
        from .i18n import EN, LOCALES, LOCALE_LABELS, catalog as string_catalog, friendly_error, normalize_locale
    except ImportError:
        from i18n import EN, LOCALES, LOCALE_LABELS, catalog as string_catalog, friendly_error, normalize_locale
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
        match = request.accept_languages.best_match(list(LOCALES))
        if match:
            return normalize_locale(match)
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
            current_user_id,
            is_self_hosted,
            login_required,
            protect_request,
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
            current_user_id,
            is_self_hosted,
            login_required,
            protect_request,
            remember_user,
            safe_next,
            template_state,
            verify_session_token,
        )

    @app.before_request
    def _set_language_guard_locale():
        try:
            from .services.language_guard import resolve_request_locale, set_request_locale
        except ImportError:
            from services.language_guard import resolve_request_locale, set_request_locale
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
        return protect_request()

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

    @app.get("/architecture")
    def architecture_page():
        return _landing_page("architecture.html")

    def _workspace_page():
        try:
            from .db.jdf_repository import DEFAULT_PROJECT_ID, fetch_latest_jdf_or_empty
        except ImportError:
            from db.jdf_repository import DEFAULT_PROJECT_ID, fetch_latest_jdf_or_empty
        initial_jdf = fetch_latest_jdf_or_empty(DEFAULT_PROJECT_ID)
        return _page(
            "index.html",
            "compose",
            initial_pane="compose",
            include_pk=True,
            initial_jdf=initial_jdf,
            project_id=DEFAULT_PROJECT_ID,
        )

    @app.get("/app")
    @login_required
    def workspace():
        return _workspace_page()

    @app.get("/compose")
    def compose_redirect():
        qs = request.query_string.decode() if request.query_string else ""
        return redirect("/app" + (("?" + qs) if qs else ""))

    @app.get("/history")
    @login_required
    def history_page():
        return _page("index.html", "history", initial_pane="history")

    @app.get("/learn")
    def learn_page():
        return _page("index.html", "learn", initial_pane="learn")

    @app.get("/library")
    @login_required
    def library_page():
        return _page("index.html", "library", initial_pane="library")

    @app.get("/connect")
    @login_required
    def connect():
        return _page("connect.html", "connect")

    @app.get("/signin")
    def signin():
        return _page(
            "auth.html",
            "signin",
            include_pk=True,
            auth_mode="signin",
            next_url=safe_next(request.args.get("next")),
        )

    @app.get("/signup")
    def signup():
        return _page(
            "auth.html",
            "signup",
            include_pk=True,
            auth_mode="signup",
            next_url=safe_next(request.args.get("next")),
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

    @app.get("/pricing")
    def pricing():
        try:
            from .cloud_billing import stripe_configured
        except ImportError:
            from cloud_billing import stripe_configured
        return _page("pricing.html", "pricing", stripe_ready=stripe_configured())

    @app.get("/privacy")
    def privacy():
        return _page("privacy.html", "privacy")

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
            return jsonify({"error": string_catalog(_locale()).get("billing.missing") or str(exc)}), 400
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
            return jsonify({"error": string_catalog(_locale()).get("billing.missing") or str(exc)}), 400
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
            return jsonify({"error": friendly_error(
                "Could not search those file paths. Remove * and ** from the question, "
                "or put paths only in Extra context.",
                _locale(),
            )}), 400
        except OSError:
            return jsonify({"error": friendly_error(
                "Could not search those file paths. Remove * and ** from the question, "
                "or put paths only in Extra context.",
                _locale(),
            )}), 400

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
            from .quality import audit_spans, confidence_text, models_from_steps, models_used_from_steps
        except ImportError:
            from quality import audit_spans, confidence_text, models_from_steps, models_used_from_steps
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


    @app.post("/api/tester-feedback")
    def handle_tester_feedback():
        data = request.get_json(silent=True) or {}
        text = str(data.get("text") or "").strip()
        if not text:
            return jsonify({"error": "Feedback text required."}), 400
        if len(text) > 4000:
            return jsonify({"error": "Feedback text too long (max 4000 chars)."}), 400
        page = str(data.get("page") or request.referrer or "")[:500]
        request_id = str(uuid.uuid4())
        user_hint = str(data.get("user") or "").strip()[:120] or None
        try:
            from flask import g

            user_id = getattr(g, "user_id", None)
            if user_id:
                user_hint = str(user_id)
        except RuntimeError:
            pass
        try:
            from .lib.logger import get_audit_logger
        except ImportError:
            from lib.logger import get_audit_logger
        get_audit_logger().log_audit(
            request_id,
            None,
            "TESTER_FEEDBACK",
            success=True,
            details={"text": text, "page": page, "user": user_hint},
        )
        return jsonify({"status": "ok"}), 200

    @app.post("/api/feedback")
    @login_required
    def handle_feedback():
        data = request.get_json(silent=True) or {}
        run_hash = str(data.get("run_hash") or "").strip()
        rating_raw = data.get("rating")
        variation_id = data.get("variation_id")
        if not run_hash:
            return jsonify({"error": "Missing run."}), 400
        try:
            rating = int(rating_raw)
        except (TypeError, ValueError):
            return jsonify({"error": "Rating must be 0 or 1."}), 400
        if rating not in (0, 1):
            return jsonify({"error": "Rating must be 0 or 1."}), 400
        vid = None
        if variation_id not in (None, ""):
            try:
                vid = int(variation_id)
            except (TypeError, ValueError):
                vid = None
        try:
            from .template_library import apply_feedback
        except ImportError:
            from template_library import apply_feedback
        out = apply_feedback(run_hash, rating, vid)
        if not out.get("ok"):
            return jsonify({"error": out.get("error") or "Could not save feedback."}), 400
        return jsonify({**out, "status": "ok"}), 200

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

    @app.get("/api/prompts")
    def prompts_list():
        try:
            return jsonify({"prompts": [item.model_dump() for item in load_library().prompts]})
        except MatrixError as exc:
            return jsonify({"error": friendly_error(str(exc), _locale())}), 400

    @app.get("/api/prompts/<prompt_id>")
    def prompts_one(prompt_id: str):
        try:
            return jsonify(get_saved_prompt(prompt_id).model_dump())
        except MatrixError as exc:
            return jsonify({"error": friendly_error(str(exc), _locale())}), 404

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
        from .routers.sandbox import register_sandbox_routes
    except ImportError:
        from routers.sandbox import register_sandbox_routes
    register_sandbox_routes(app)

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
