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
        "template": "security.html",
        "output": "security/index.html",
        "kind": "base",
        "active": "security",
        "suffix": "security/",
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
    {
        "template": "404.html",
        "output": "404.html",
        "kind": "landing",
        "suffix": "",
        # One 404.html per locale dir; the Workers 404-page handler serves the nearest one.
    },
]


_I18N_ATTR_RE = re.compile(r'data-i18n(?:-html|-placeholder|-aria|-label)?="([^"]+)"')
_GET_KEY_RE = re.compile(r"strings\.get\(\s*['\"]([a-zA-Z0-9_.]+)['\"]")
_INCLUDE_RE = re.compile(r"{%-?\s*include\s+['\"]([^']+?)['\"]\s*%-?\}")
_EXTENDS_RE = re.compile(r"{%-?\s*extends\s+['\"]([^']+?)['\"]\s*%-?\}")
TEMPLATES_DIR = ROOT / "marketing" / "templates"

TEMPLATE_TO_META = {
    "landing.html": "landing",
    "government.html": "government",
    "use_cases_index.html": "use_cases_index",
    "use_case_government.html": "use_case_government",
    "use_case_insurance.html": "use_case_insurance",
    "use_case_fuel.html": "use_case_fuel",
    "landing_privacy.html": "landing_privacy",
    "contact.html": "contact",
    "terms.html": "terms",
    "about.html": "about",
    "security.html": "security",
    "pricing.html": "pricing",
}

LOCALE_TO_OG = {
    "en": "en_US",
    "es": "es_ES",
    "zh": "zh_CN",
    "fr": "fr_FR",
    "de": "de_DE",
    "ja": "ja_JP",
    "tr": "tr_TR",
    "ar": "ar_AE",
}

OG_IMAGE = "https://getassureai.com/static/og-image.png"


# Locales that exist for preview but are not published yet (turn on via PUBLISH_LOCALES).
UNPUBLISHED_BY_DEFAULT = frozenset()

# Locales that can be built with the marketing site (7 + ar)
MARKETING_LOCALES = ("en", "es", "zh", "fr", "de", "ja", "tr", "ar")


def publish_locales() -> list[str]:
    """Locales shown in the switcher / hreflang / language count.

    PUBLISH_LOCALES="en es zh fr de ja tr ar" (space or comma separated) overrides the default,
    which is every locale in LOCALES except those in UNPUBLISHED_BY_DEFAULT.
    """
    raw = os.environ.get("PUBLISH_LOCALES", "").replace(",", " ").split()
    if raw:
        unknown = [loc for loc in raw if loc not in LOCALES]
        if unknown:
            raise SystemExit(f"PUBLISH_LOCALES has unknown locale(s): {unknown}")
        return [loc for loc in LOCALES if loc in raw]
    return [loc for loc in LOCALES if loc not in UNPUBLISHED_BY_DEFAULT]


def _locale_paths() -> dict[str, str]:
    return {loc: "/" if loc == "en" else f"/{loc}/" for loc in publish_locales()}


_I18N_TEXT_RE = re.compile(
    r"<(\w+)((?:[^>]*?\s)?data-i18n=\"([^\"]+)\"[^>]*)>(.*?)</\1>", re.S
)
_I18N_HTML_RE = re.compile(
    r"<(\w+)((?:[^>]*?\s)?data-i18n-html=\"([^\"]+)\"[^>]*)>(.*?)</\1>", re.S
)
_I18N_ATTR_RE_ATTR = re.compile(
    r"<(\w+)([^>]*?\sdata-i18n-(placeholder|aria|label)=\"([^\"]+)\"[^>]*)>"
)
_ATTR_FOR_KIND = {"placeholder": "placeholder", "aria": "aria-label", "label": "title"}


def _apply_data_i18n(html: str, strings: dict) -> str:
    """Bake data-i18n strings into the static HTML so server-rendered pages are
    translated without JS (the client script keeps working for live switches)."""

    def repl_text(m: re.Match) -> str:
        tag, attrs, key, inner = m.group(1), m.group(2), m.group(3), m.group(4)
        val = strings.get(key)
        if not val or "<" in inner:
            return m.group(0)
        return f"<{tag}{attrs}>{val}</{tag}>"

    def repl_html(m: re.Match) -> str:
        tag, attrs, key, inner = m.group(1), m.group(2), m.group(3), m.group(4)
        val = strings.get(key)
        if not val:
            return m.group(0)
        return f"<{tag}{attrs}>{val}</{tag}>"

    def repl_attr(m: re.Match) -> str:
        tag, attrs, kind, key = m.group(1), m.group(2), m.group(3), m.group(4)
        val = strings.get(key)
        if not val:
            return m.group(0)
        attr = _ATTR_FOR_KIND[kind]
        pattern = re.compile(rf"\s{attr}=\"[^\"]*\"")
        if pattern.search(attrs):
            attrs = pattern.sub(f' {attr}="{val}"', attrs)
        else:
            attrs = f"{attrs} {attr}=\"{val}\""
        return f"<{tag}{attrs}>"

    html = _I18N_TEXT_RE.sub(repl_text, html)
    html = _I18N_HTML_RE.sub(repl_html, html)
    html = _I18N_ATTR_RE_ATTR.sub(repl_attr, html)
    return html


def _postprocess(
    html: str,
    *,
    locale: str,
    page_suffix: str,
    static_base: str,
    strings: dict | None = None,
) -> str:
    static_base = static_base.rstrip("/")

    html = html.replace('="/static/', f'="{static_base}/')
    html = html.replace("='/static/", f"='{static_base}/")

    # Bake translations into the static HTML for every locale except en, whose
    # hardcoded template text already is the English source.
    if strings and locale != "en":
        html = _apply_data_i18n(html, strings)

    inject = (
        "<script>"
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
    for match in _EXTENDS_RE.finditer(source):
        keys |= _used_string_keys(app, match.group(1), seen)
    return keys


def _public_strings(app, page: dict, locale: str) -> dict:
    """Marketing pages ship only the strings they use — never the app catalog."""
    catalog = string_catalog(locale)
    used = _used_string_keys(app, page["template"])
    # landing-demo.js resolves keys dynamically ("landing.demo." + name).
    filtered = {k: v for k, v in catalog.items() if k in used or k.startswith("landing.demo.")}
    badge = filtered.get("landing.hero.badge")
    if badge:  # "... 8 languages" follows the number of published locales
        filtered["landing.hero.badge"] = re.sub(r"\d+", str(len(publish_locales())), badge, count=1)
    return filtered


def _render_page(app, page: dict, locale: str) -> str:
    extra = dict(page.get("extra") or {})
    with app.app_context():
        with app.test_request_context(path="/", query_string={"lang": locale}):
            session["lang"] = locale
            meta_prefix = TEMPLATE_TO_META.get(page["template"])
            full = string_catalog(locale)
            page_title = full.get(f"meta.{meta_prefix}.title") if meta_prefix else None
            if not page_title:
                page_title = full.get("brand.page_title", "Assure")
            page_description = (
                full.get(f"meta.{meta_prefix}.description") if meta_prefix else None
            ) or full.get("brand.meta_description", "")
            path = _locale_paths()[locale]
            page_url = "https://getassureai.com" + path + page["suffix"]
            en_url = "https://getassureai.com" + "/" + page["suffix"]
            og_locale = LOCALE_TO_OG.get(locale, "en_US")

            is_home = page["template"] == "landing.html" and page["suffix"] == ""
            json_ld = []
            if is_home:
                json_ld.append(
                    {
                        "@context": "https://schema.org",
                        "@type": "Organization",
                        "name": "Assure",
                        "url": "https://getassureai.com",
                        "logo": "https://getassureai.com/static/assure-logo.png",
                        "inLanguage": locale,
                    }
                )
                json_ld.append(
                    {
                        "@context": "https://schema.org",
                        "@type": "WebSite",
                        "name": "Assure",
                        "url": "https://getassureai.com/",
                        "inLanguage": locale,
                    }
                )
                json_ld.append(
                    {
                        "@context": "https://schema.org",
                        "@type": "SoftwareApplication",
                        "name": "Assure",
                        "applicationCategory": "BusinessApplication",
                        "description": page_description,
                        "url": page_url,
                        "inLanguage": locale,
                    }
                )
                faq = [(full.get(f"home.faq.{i}.q"), full.get(f"home.faq.{i}.a")) for i in range(1, 5)]
                if all(q and a for q, a in faq):
                    json_ld.append(
                        {
                            "@context": "https://schema.org",
                            "@type": "FAQPage",
                            "inLanguage": locale,
                            "mainEntity": [
                                {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}}
                                for q, a in faq
                            ],
                        }
                    )
            else:
                home_name = full.get("meta.home.name", "Home")
                json_ld.append(
                    {
                        "@context": "https://schema.org",
                        "@type": "WebPage",
                        "name": page_title,
                        "url": page_url,
                        "inLanguage": locale,
                    }
                )
                json_ld.append(
                    {
                        "@context": "https://schema.org",
                        "@type": "BreadcrumbList",
                        "inLanguage": locale,
                        "itemListElement": [
                            {
                                "@type": "ListItem",
                                "position": 1,
                                "name": home_name,
                                "item": "https://getassureai.com/",
                            },
                            {
                                "@type": "ListItem",
                                "position": 2,
                                "name": page_title,
                                "item": page_url,
                            },
                        ],
                    }
                )

            ctx: dict = {
                "locale": locale,
                "languages": {loc: LOCALE_LABELS[loc] for loc in publish_locales()},
                "strings": _public_strings(app, page, locale),
                "locale_paths": _locale_paths(),
                "page_suffix": page["suffix"],
                "page_title": page_title,
                "page_description": page_description,
                "page_url": page_url,
                "en_url": en_url,
                "og_locale": og_locale,
                "og_image": OG_IMAGE,
                "json_ld": json_ld,
                "locale_path": path,
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
) -> list[Path]:
    app = create_static_app()
    written: list[Path] = []
    output_dir.mkdir(parents=True, exist_ok=True)

    for page in PAGES:
        if page.get("locales") and locale not in page["locales"]:
            continue
        html = _render_page(app, page, locale)
        html = _postprocess(
            html,
            locale=locale,
            page_suffix=page["suffix"],
            static_base=static_base,
            strings=string_catalog(locale),
        )
        out_path = output_dir / page["output"]
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html, encoding="utf-8")
        written.append(out_path)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Render marketing HTML for one locale.")
    parser.add_argument("--locale", required=True, choices=MARKETING_LOCALES)
    parser.add_argument(
        "--output", required=True, type=Path, help="Locale root (e.g. dist or dist/es)"
    )
    parser.add_argument(
        "--static-base",
        default=os.environ.get("CDN_BASE_URL", "/static"),
        help="Prefix for /static assets (default: /static or CDN_BASE_URL)",
    )
    args = parser.parse_args()

    paths = render_locale(
        args.locale,
        args.output,
        static_base=args.static_base,
    )
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
