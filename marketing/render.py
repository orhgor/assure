#!/usr/bin/env python3
"""Pre-render Flask marketing templates to static HTML (one locale per run)."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("ASSURE_REQUIRE_LOGIN", "false")
os.environ.setdefault("PEM_HTTP_PASS", "")
os.environ.setdefault("CLERK_PUBLISHABLE_KEY", "")
os.environ.setdefault("CLERK_SECRET_KEY", "")

from flask import render_template, session  # noqa: E402

from marketing.app import (  # noqa: E402
    LANDING_CSS,
    LANDING_JS,
    LOCALES,
    LOCALE_LABELS,
    auth_state,
    create_static_app,
    edition_snapshot_static,
    string_catalog,
)

# template -> output path under locale root
PAGES: list[dict] = [
    {"template": "landing.html", "output": "index.html", "kind": "landing", "suffix": ""},
    {
        "template": "architecture.html",
        "output": "architecture/index.html",
        "kind": "landing",
        "suffix": "architecture/",
    },
    {
        "template": "government.html",
        "output": "government/index.html",
        "kind": "landing",
        "suffix": "government/",
    },
    {
        "template": "use_cases_index.html",
        "output": "use-cases/index.html",
        "kind": "landing",
        "suffix": "use-cases/",
    },
    {
        "template": "use_case_government.html",
        "output": "use-cases/government/index.html",
        "kind": "landing",
        "suffix": "use-cases/government/",
    },
    {
        "template": "use_case_insurance.html",
        "output": "use-cases/insurance/index.html",
        "kind": "landing",
        "suffix": "use-cases/insurance/",
    },
    {
        "template": "use_case_fuel.html",
        "output": "use-cases/fuel-retail/index.html",
        "kind": "landing",
        "suffix": "use-cases/fuel-retail/",
    },
    {
        "template": "landing_privacy.html",
        "output": "privacy/index.html",
        "kind": "landing",
        "suffix": "privacy/",
    },
    {
        "template": "contact.html",
        "output": "contact/index.html",
        "kind": "landing",
        "suffix": "contact/",
    },
    {
        "template": "contact.html",
        "output": "contact-us/index.html",
        "kind": "landing",
        "suffix": "contact-us/",
    },
    {
        "template": "terms.html",
        "output": "terms/index.html",
        "kind": "base",
        "active": "terms",
        "suffix": "terms/",
    },
    {
        "template": "about.html",
        "output": "about/index.html",
        "kind": "base",
        "active": "about",
        "suffix": "about/",
    },
    {
        "template": "pricing.html",
        "output": "pricing/index.html",
        "kind": "base",
        "active": "pricing",
        "suffix": "pricing/",
        "extra": {"stripe_ready": False},
    },
]


_I18N_ATTR_RE = re.compile(r'data-i18n(?:-html|-placeholder|-aria|-label)?="([^"]+)"')
_GET_KEY_RE = re.compile(r"strings\.get\(\s*['\"]([a-zA-Z0-9_.]+)['\"]")
_INCLUDE_RE = re.compile(r"{%-?\s*include\s+'([^']+?)'\s*%-?\}")
TEMPLATES_DIR = ROOT / "prompt_matrix" / "templates"


def _locale_paths() -> dict[str, str]:
    return {loc: "/" if loc == "en" else f"/{loc}/" for loc in LOCALES}


def _postprocess(
    html: str,
    *,
    locale: str,
    page_suffix: str,
    static_base: str,
    app_host: str,
) -> str:
    static_base = static_base.rstrip("/")
    app_host = app_host.rstrip("/")

    html = html.replace('="/static/', f'="{static_base}/')
    html = html.replace("='/static/", f"='{static_base}/")

    for prefix in ("/app", "/api/"):
        html = re.sub(
            rf'href="{re.escape(prefix)}',
            f'href="{app_host}{prefix}',
            html,
        )
        html = re.sub(
            rf"href='{re.escape(prefix)}",
            f"href='{app_host}{prefix}",
            html,
        )

    inject = (
        "<script>"
        f'window.__ASSURE_APP_ORIGIN="{app_host}";'
        f"window.__ASSURE_LOCALE_PATHS={json.dumps(_locale_paths())};"
        f'window.__ASSURE_PAGE_SUFFIX="{page_suffix}";'
        f'window.__ASSURE_STATIC_LOCALE="{locale}";'
        "</script>\n"
    )
    if "</body>" in html:
        html = html.replace("</body>", inject + "</body>", 1)
    else:
        html += inject
    return html


def _used_string_keys(app, template_name: str, seen: set | None = None) -> set[str]:
    """Collect i18n keys a template (and its includes) actually reference."""
    seen = seen if seen is not None else set()
    if template_name in seen:
        return set()
    seen.add(template_name)
    keys: set[str] = set()
    try:
        source = (TEMPLATES_DIR / template_name).read_text(encoding="utf-8")
    except Exception:
        return keys
    for match in _I18N_ATTR_RE.finditer(source):
        keys.add(match.group(1))
    for match in _GET_KEY_RE.finditer(source):
        keys.add(match.group(1))
    for match in _INCLUDE_RE.finditer(source):
        keys |= _used_string_keys(app, match.group(1), seen)
    return keys


def _public_strings(app, page: dict, locale: str) -> dict:
    """Marketing pages ship only the strings they use — never the app catalog."""
    catalog = string_catalog(locale)
    used = _used_string_keys(app, page["template"])
    # landing-demo.js resolves keys dynamically ("landing.demo." + name).
    filtered = {k: v for k, v in catalog.items() if k in used or k.startswith("landing.demo.")}
    return filtered


def _render_page(app, page: dict, locale: str) -> str:
    extra = dict(page.get("extra") or {})
    with app.app_context():
        with app.test_request_context(path="/", query_string={"lang": locale}):
            session["lang"] = locale
            ctx: dict = {
                "locale": locale,
                "languages": LOCALE_LABELS,
                "strings": _public_strings(app, page, locale),
                "locale_paths": _locale_paths(),
                "page_suffix": page["suffix"],
            }
            if page["kind"] == "landing":
                ctx["landing_css_version"] = LANDING_CSS
                ctx["landing_js_version"] = LANDING_JS
            else:
                ctx.update(
                    active=page["active"],
                    auth=auth_state(include_pk=False),
                    edition=edition_snapshot_static(),
                    **extra,
                )
            return render_template(page["template"], **ctx)


def render_locale(
    locale: str,
    output_dir: Path,
    *,
    static_base: str,
    app_host: str,
) -> list[Path]:
    app = create_static_app()
    written: list[Path] = []
    output_dir.mkdir(parents=True, exist_ok=True)

    for page in PAGES:
        html = _render_page(app, page, locale)
        html = _postprocess(
            html,
            locale=locale,
            page_suffix=page["suffix"],
            static_base=static_base,
            app_host=app_host,
        )
        out_path = output_dir / page["output"]
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html, encoding="utf-8")
        written.append(out_path)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Render marketing HTML for one locale.")
    parser.add_argument("--locale", required=True, choices=LOCALES)
    parser.add_argument(
        "--output", required=True, type=Path, help="Locale root (e.g. dist or dist/es)"
    )
    parser.add_argument(
        "--static-base",
        default=os.environ.get("CDN_BASE_URL", "/static"),
        help="Prefix for /static assets (default: /static or CDN_BASE_URL)",
    )
    parser.add_argument(
        "--app-host",
        default=os.environ.get("APP_HOST", "https://app.getassureai.com"),
        help="Workbench origin for /app and /api/waitlist",
    )
    args = parser.parse_args()

    paths = render_locale(
        args.locale,
        args.output,
        static_base=args.static_base,
        app_host=args.app_host,
    )
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
