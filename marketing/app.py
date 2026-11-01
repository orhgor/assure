"""Standalone Flask app that builds the marketing site.

ISOLATION CONTRACT — this module must never read the workbench:
  - no workbench import or file read of any kind
  - no database connection (no DATABASE_URL needed to build)
Everything the site needs lives under marketing/:
  - marketing/templates (markup)
  - marketing/static    (assets)
  - marketing/i18n.py   (string catalog, own copy)
  - marketing/cache_versions.py (cache-bust values)

Workbench-runtime pieces are replicated as tiny env-only shims below:
  - gettext   (msgid -> key -> catalog(locale))
  - brand     (base name + overrides from the string catalog)
  - auth      (env-only logic, no session user; marketing pages have no auth)
  - edition   (static snapshot: env edition plan, zero usage — no DB)
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

from flask import Flask, request, session

ROOT = Path(__file__).resolve().parents[1]


def _load_local(module_name: str, rel_path: str):
    """Import a data module from the marketing tree without package machinery."""
    spec = importlib.util.spec_from_file_location(module_name, Path(__file__).parent / rel_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(module_name, module)
    spec.loader.exec_module(module)
    return module


_strings = _load_local("assure_marketing_strings", "i18n.py")
_uiv = _load_local("assure_marketing_uiv", "cache_versions.py")

LOCALES = _strings.LOCALES
LOCALE_LABELS = _strings.LOCALE_LABELS
string_catalog = _strings.catalog
LANDING_CSS = _uiv.LANDING_CSS
LANDING_JS = _uiv.LANDING_JS

BRAND_BASE = {"name": "Assure"}
BRAND_STRING_KEYS = (
    "brand.category",
    "brand.tagline",
    "brand.eyebrow",
    "brand.hero_title",
    "brand.page_title",
    "brand.meta_description",
    "brand.architecture_title",
    "brand.architecture_meta_description",
)

EDITION_FREE = {
    "id": "free",
    "label": "Free",
    "daily_sends": 5,
    "max_ensemble": 1,
    "full_text_history": False,
    "history_days": 7,
    "export_formats": [],
    "allow_diff": False,
    "personas": ["redhat"],
    "can_export_work": False,
    "can_search_full": False,
    "can_refine_any": False,
    "product": "Assure",
    "engine": "PEM",
    "cloud": False,
    "gate_ensemble": False,
    "stripe": False,
    "supabase": False,
}


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _plausible_enabled() -> bool:
    if _env("PLAUSIBLE_ENABLED") == "1":
        return True
    return _env("ENVIRONMENT") == "production"


def _sentry_enabled() -> bool:
    if _env("SENTRY_ENABLED") == "1":
        return True
    return _env("ENVIRONMENT") == "production"


def _sentry_browser_dsn() -> str:
    for key in ("SENTRY_BROWSER_DSN", "SENTRY_DSN"):
        dsn = _env(key)
        if dsn:
            return dsn
    if _env("ENVIRONMENT") == "production":
        return _DEFAULT_SENTRY_BROWSER_DSN
    return ""


def _clerk_configured() -> bool:
    return bool(_env("CLERK_PUBLISHABLE_KEY") and _env("CLERK_SECRET_KEY"))


def _is_self_hosted() -> bool:
    raw = (_env("ASSURE_EDITION") or _env("PEM_EDITION")).replace("_", "-")
    return raw in {"self-hosted", "selfhosted", "self-host"}


def auth_state(*, include_pk: bool = False) -> dict[str, Any]:
    """template_state equivalent — env-only, matches cloud_auth logic."""
    required = _clerk_configured() and not _is_self_hosted() and _env(
        "ASSURE_REQUIRE_LOGIN", "true"
    ).lower() not in {"0", "false", "no", "off"}
    return {
        "configured": _clerk_configured(),
        "required": required,
        "self_hosted": _is_self_hosted(),
        "signed_in": False,
        "user_id": "",
        "email": "",
        "publishable_key": "",
    }


def edition_snapshot_static() -> dict[str, Any]:
    return dict(EDITION_FREE)


def create_static_app(*, default_locale: str = "en") -> Flask:
    app = Flask(
        "assure-marketing",
        template_folder=str(Path(__file__).parent / "templates"),
        static_folder=str(Path(__file__).parent / "static"),
    )
    app.secret_key = "static-render-only"  # session cookie only; no auth here

    def _locale() -> str:
        if session.get("lang"):
            return _strings.normalize_locale(session.get("lang"))
        cookie = request.cookies.get("assure_lang")
        if cookie:
            return _strings.normalize_locale(cookie)
        return _strings.normalize_locale(default_locale)

    en_msgid_key: dict[str, str] = {}
    for key, val in _strings.EN.items():
        if isinstance(val, str) and val not in en_msgid_key:
            en_msgid_key[val] = key

    def _gettext(message: str) -> str:
        key = en_msgid_key.get(message)
        if key:
            return _strings.catalog(_locale()).get(key, message)
        return message  # matches web.py fallback when Babel is absent

    app.jinja_env.globals["gettext"] = _gettext

    @app.context_processor
    def _marketing_context() -> dict:
        strings = _strings.catalog(_locale())
        brand = dict(BRAND_BASE)
        for key in BRAND_STRING_KEYS:
            short = key.split(".", 1)[1]
            if strings.get(key):
                brand[short] = strings[key]
        deployment = json.loads(
            (Path(__file__).parent / "content" / "deployment_modes.json").read_text(encoding="utf-8")
        )
        return {
            "brand": brand,
            "strings": strings,
            "locale": _locale(),
            "languages": LOCALE_LABELS,
            "landing_css_version": _uiv.LANDING_CSS,
            "landing_js_version": _uiv.LANDING_JS,
            "plausible_enabled": _plausible_enabled(),
            "sentry_enabled": _sentry_enabled(),
            "sentry_browser_dsn": _sentry_browser_dsn(),
            "deployment": deployment,
        }

    return app