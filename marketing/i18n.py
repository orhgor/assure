"""Assure marketing-site strings. Copied from the app catalog; text identical.

Locales: en, es, zh, fr, de, ja, tr (published) + ar (preview, gated by PUBLISH_LOCALES).
This file is standalone: it must not import or read anything from the workbench tree.
"""

from __future__ import annotations

import json
from pathlib import Path

LOCALES = ("en", "es", "zh", "fr", "de", "ja", "tr", "ar")  # published
MARKETING_LOCALES = ("en", "es", "zh", "fr", "de", "ja", "tr", "ar")  # ar = preview
RTL_LOCALES = frozenset({"ar"})
LOCALE_LABELS = {
    "en": "English",
    "es": "Español",
    "zh": "中文",
    "fr": "Français",
    "de": "Deutsch",
    "ja": "日本語",
    "tr": "Türkçe",
    "ar": "العربية",
}

_LOCALES_DIR = Path(__file__).parent / "locales"


def _load_catalog(locale: str) -> dict[str, str]:
    """Load a locale catalog from its JSON file."""
    path = _LOCALES_DIR / f"{locale}.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


CATALOGS: dict[str, dict[str, str]] = {}
for _locale in LOCALES:
    CATALOGS[_locale] = _load_catalog(_locale)

# Backward-compatible module-level references for code that imports EN, ES, etc.
EN = CATALOGS["en"]
ES = CATALOGS["es"]
ZH = CATALOGS["zh"]
FR = CATALOGS["fr"]
DE = CATALOGS["de"]
JA = CATALOGS["ja"]
TR = CATALOGS["tr"]
AR = CATALOGS["ar"]


def normalize_locale(value: str | None) -> str:
    raw = (value or "en").strip().lower().replace("_", "-")
    for code in ("zh", "es", "fr", "de", "ja", "tr", "ar", "en"):
        if raw.startswith(code):
            return code
    return "en"


def catalog(lang: str | None = None) -> dict[str, str]:
    locale = normalize_locale(lang)
    base = dict(CATALOGS.get(locale, {}))
    # The enterprise privacy body lives in marketing/content/privacy/<locale>.html
    # (own copy of the marketing site; the workbench keeps its own).
    from marketing.privacy_enterprise import privacy_body_html, privacy_summary

    if "privacy.enterprise.body_html" not in base:
        try:
            base["privacy.enterprise.body_html"] = privacy_body_html(locale)
        except OSError:
            pass
    if not base.get("privacy.enterprise.summary"):
        base["privacy.enterprise.summary"] = privacy_summary(locale)
    return base
