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

from prompt_matrix.cloud_auth import template_state  # noqa: E402
from prompt_matrix.editions import snapshot as edition_snapshot  # noqa: E402
from prompt_matrix.i18n import LOCALES, LOCALE_LABELS, catalog as string_catalog  # noqa: E402
from prompt_matrix.ui_cache import LANDING_CSS, LANDING_JS  # noqa: E402
from prompt_matrix.web import create_app  # noqa: E402

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
        "template": "landing_privacy.html",
        "output": "privacy/index.html",
        "kind": "landing",
        "suffix": "privacy/",
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


def _render_page(app, page: dict, locale: str) -> str:
    extra = dict(page.get("extra") or {})
    with app.app_context():
        with app.test_request_context(path="/", query_string={"lang": locale}):
            session["lang"] = locale
            ctx: dict = {
                "locale": locale,
                "languages": LOCALE_LABELS,
                "strings": string_catalog(locale),
            }
            if page["kind"] == "landing":
                ctx["landing_css_version"] = LANDING_CSS
                ctx["landing_js_version"] = LANDING_JS
            else:
                ctx.update(
                    active=page["active"],
                    auth=template_state(include_pk=False),
                    edition=edition_snapshot(),
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
    app = create_app(require_auth=False)
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
