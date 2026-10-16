"""Standalone Flask app that renders the marketing site.

ISOLATION CONTRACT — this module must never import the workbench app:
  - no `prompt_matrix.web` import (that is the app: DB, sessions, workbench routes)
  - no database connection of any kind (no DATABASE_URL needed to build)
Shared with the app only through FILES (single source of truth):
  - prompt_matrix/templates  (markup — one source, served live and rendered here)
  - prompt_matrix/static     (assets — css/js/favicon/logo)
  - prompt_matrix/i18n.py    (string catalog — loaded standalone, no package import)
  - prompt_matrix/ui_cache.py (cache-bust versions)

Workbench-runtime pieces are replicated as tiny env-only shims below:
  - gettext   (msgid -> key -> catalog(locale), matching web.py behavior)
  - brand     (base name + overrides from the string catalog, matching web.py)
  - auth      (template_state equivalent: Clerk env-only logic, no session user)
  - edition   (static snapshot: env edition plan, zero usage — no DB)
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any

from flask import Flask, request, session

ROOT = Path(__file__).resolve().parents[1]
ACCOUNT_ID = "381b292d419f2efdc1c85a3636268e91"


def _load_standalone(module_name: str, rel_path: str):
    """Import a prompt_matrix data module WITHOUT importing the package
    (prompt_matrix/__init__ pulls the engine: pydantic/jinja2 chain)."""
    spec = importlib.util.spec_from_file_location(module_name, ROOT / rel_path)
    module = importlib.util.module_from_spec(spec)
    # claim the real package path so intra-catalog relative imports
    # (e.g. `from .privacy_enterprise import ...` in catalog()) resolve
    module.__package__ = "prompt_matrix"
    module.__path__ = [str(ROOT / "prompt_matrix")]
    sys.modules.setdefault("prompt_matrix", module)
    spec.loader.exec_module(module)
    return module


_strings = _load_standalone("assure_marketing_strings", "prompt_matrix/i18n.py")
_uiv = _load_standalone("assure_marketing_uiv", "prompt_matrix/ui_cache.py")

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
        template_folder=str(ROOT / "prompt_matrix" / "templates"),
        static_folder=str(ROOT / "prompt_matrix" / "static"),
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
        }

    return app