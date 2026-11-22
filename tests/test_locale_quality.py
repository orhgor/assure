"""Locale quality gate.

Encodes every locale bug class that actually shipped (see PRs #148-#154):
English fallbacks, missing dynamic Jinja keys, calques, register slips,
wrong-content sections, and meta titles without the brand. Standard
library only. Each banned phrase below corresponds to a real defect that
reached production once - do not remove an entry without fixing the
underlying translation, and add a new entry whenever a new class ships.

The reachable-key set is built the same way render.py consumes strings:
static data-i18n attributes, Jinja-resolved uc.* loops, macro call-site
literals, every TEMPLATE_TO_META prefix, and the dynamic crumb./faq./howto.
lookups. Audits that skip any of those channels are how English fallbacks
stayed behind (PR #153).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LOCALES_DIR = ROOT / "marketing" / "locales"
TEMPLATES = ROOT / "marketing" / "templates"
RENDER = ROOT / "marketing" / "render.py"
DIST = ROOT / "dist"
LOCALES = ("es", "zh", "fr", "de", "ja", "tr", "ar")

# Keys whose correct value is identical to English: brand/product names,
# same-word cases (Solutions, Architecture, Contact, Questions, Source A/B,
# Name, Workflows, Enterprise), the Japanese legal formula, empty labels.
SAME_AS_ENGLISH = {
    "brand.name",
    "differs.assure.name",
    "howto.1.label",
    "howto.2.label",
    "howto.4.label",
    "landing.footer.owner",
    # fr same-word cases
    "arch.title",
    "crumb.architecture",
    "crumb.contact",
    "crumb.solutions",
    "faq.title",
    "landing.nav.contact",
    "pricing.faq.title",
    "proof.a.kicker",
    "proof.b.kicker",
    "solutions.title",
    "uc.h.faq",
    # de same-word cases
    "contact.f_name",
    "footer.workflows",
    "pricing.pro",
    # fr: same word
    "uc.fuel.check.2.title",
    "uc.ins.check.2.title",
    "uc.fuel.delta",
}

# Banned substrings per locale: calques, false friends, colloquialisms and
# register slips that shipped. Keep the list append-only.
BANNED_PHRASES = {
    "tr": ("hava boşluk", "S3 kova", "rakap"),           # air-gap calque, water-bucket, typo
    "ar": ("عزل هوائي", "لأساس", "تمت فوترتها"),          # air-gap calque, brand mistranslation, colloquial
    "ja": ("クレーム", "編んだ"),                           # false friend (complaints), "knitted text"
    "de": ("Polizzen", "führen es durch"),                # Austrian spelling, broken grammar
    "es": ("Eres ", "tus contratos", "disputa cada", "Verifica por tu cuenta"),
    "fr": ("reste reste", "Copier garde"),
    "zh": (),  # formal-you is asserted separately (test_zh_terms_use_formal_you)
    "en": (),
}

# Old terms.send copy (described Copy/Send prompt behaviour) that sat under
# the wrong heading in six locales until PRs #150/#152.
TERMS_SEND_OLD_MARKERS = {
    "fr": "Copier garde",
    "de": "Kopieren lässt",
    "es": "Copiar deja",
    "ja": "コピーは",
    "zh": "复制把",
    "ar": "النسخ يبقي",
}


def _en() -> dict:
    return json.loads((LOCALES_DIR / "en.json").read_text(encoding="utf-8"))


def _locale(locale: str) -> dict:
    return json.loads((LOCALES_DIR / f"{locale}.json").read_text(encoding="utf-8"))


def _meta_prefixes() -> list[str]:
    render = RENDER.read_text(encoding="utf-8")
    start = render.find("TEMPLATE_TO_META")
    end = render.find("}", start)
    return re.findall(r'"[a-z0-9_.]+":\s*"([a-z0-9_]+)"', render[start:end])


def _uc_keys() -> set[str]:
    keys = set()
    for tpl in sorted(TEMPLATES.glob("use_case_*.html")):
        m = re.search(r"set uc_key = '([a-z]+)'", tpl.read_text(encoding="utf-8"))
        if m:
            keys.add(m.group(1))
    return keys
def _reachable_keys(en: dict) -> set[str]:
    keys: set[str] = set()
    for tpl in sorted(TEMPLATES.rglob("*.html")):
        text = tpl.read_text(encoding="utf-8")
        keys |= set(re.findall(r'data-i18n(?:-html|-placeholder|-aria|-label)?="([^"{]+)"', text))
        for m in re.finditer(r"(?:components\.)?(?:button|section_header)\s*\(([^)]*)", text):
            keys |= {lit for lit in re.findall(r"'([a-z0-9_.]+)'", m.group(1)) if "." in lit}
    for uc in _uc_keys():
        for suffix in ("check.1", "check.2", "check.3", "doc.1", "doc.2", "doc.3", "example",
                       "faq.1.a", "faq.1.q", "faq.2.a", "faq.2.q", "faq.3.a", "faq.3.q"):
            keys.add(f"uc.{uc}.{suffix}")
    for prefix in _meta_prefixes():
        keys.add(f"meta.{prefix}.title")
        keys.add(f"meta.{prefix}.description")
    for literal in re.findall(r'[\'"]([a-z][a-z0-9_.]+)[\'"]', RENDER.read_text(encoding="utf-8")):
        if "." in literal:
            keys.add(literal)
    for prefix in ("crumb", "faq", "howto"):
        keys |= {k for k in en if k.startswith(prefix + ".")}
    return {k for k in keys if k in en}


def _dist_pages() -> list[Path]:
    pages = sorted(DIST.rglob("index.html"))
    if not pages:
        pytest.skip("marketing dist is not built")
    return pages


def _locale_of(page: Path) -> str:
    rel = page.relative_to(DIST).as_posix()
    return rel.split("/", 1)[0] if "/" in rel else "en"


@pytest.fixture(scope="module")
def en() -> dict:
    return _en()


@pytest.fixture(scope="module")
def reachable(en) -> set[str]:
    return _reachable_keys(en)
def test_dynamic_uc_keys_are_enumerated() -> None:
    """Guards the audit blind spot: every uc_key used by a template must be
    covered by the generated key set (PR #153 shipped this class)."""
    assert _uc_keys() == {"gov", "ins", "fuel", "proc", "fin"}


def test_every_reachable_key_exists_in_every_locale(en, reachable) -> None:
    missing = {}
    for locale in LOCALES:
        data = _locale(locale)
        absent = sorted(k for k in reachable if k not in data)
        if absent:
            missing[locale] = absent[:10]
    assert not missing, f"keys absent (render as English fallback): {missing}"


def test_no_locale_value_equals_english(en, reachable) -> None:
    equal = {}
    for locale in LOCALES:
        data = _locale(locale)
        same = sorted(
            k for k in reachable
            if k in data and k not in SAME_AS_ENGLISH
            and data[k].strip() and data[k].strip() == en[k].strip()
        )
        if same:
            equal[locale] = same[:10]
    assert not equal, f"untranslated (byte-equal to EN): {equal}"


def test_no_empty_values_where_english_has_text(en, reachable) -> None:
    empty = {}
    for locale in LOCALES:
        data = _locale(locale)
        blanks = sorted(k for k in reachable if k in data and not data[k].strip() and en[k].strip())
        if blanks:
            empty[locale] = blanks[:10]
    assert not empty, f"empty values render as blank text: {empty}"


def test_no_banned_calque_phrases() -> None:
    bad = {}
    for locale in LOCALES:
        banned = BANNED_PHRASES.get(locale, ())
        if not banned:
            continue
        data = _locale(locale)
        hits = sorted(
            f"{k}: {data[k][:40]}"
            for k, v in data.items()
            if isinstance(v, str) and any(b in v for b in banned)
        )
        if hits:
            bad[locale] = hits[:5]
    assert not bad, f"known calques/typos are back: {bad}"


def test_zh_terms_use_formal_you() -> None:
    data = _locale("zh")
    informal = sorted(k for k, v in data.items() if k.startswith("terms.") and "你" in v)
    assert not informal, f"informal 你 back in zh terms (site register is 您): {informal}"


def test_es_terms_use_usted() -> None:
    data = _locale("es")
    informal = sorted(
        k for k, v in data.items()
        if k.startswith("terms.") and re.search(r"\b(Eres|Sube |tus |tu uso|Debes cumplir)", v)
    )
    assert not informal, f"informal register back in es terms (site register is usted): {informal}"


def test_terms_send_describes_source_pages() -> None:
    """terms.send carried Copy/Send prompt copy under the wrong heading in
    six locales (PRs #150/#152). The real text names AssureProof."""
    bad = {}
    for locale in LOCALES:
        value = _locale(locale).get("terms.send", "")
        marker = TERMS_SEND_OLD_MARKERS.get(locale)
        if marker and marker in value:
            bad[locale] = "old Copy/Send copy"
        elif "AssureProof" not in value:
            bad[locale] = "brand missing"
    assert not bad, bad


def test_security_pillar1_mentions_bedrock() -> None:
    """The deployment choice sentence (Bedrock vs self-hosted) was dropped
    by under-translations in six locales (PR #152)."""
    missing = [locale for locale in LOCALES
               if "Amazon Bedrock" not in _locale(locale).get("security.pillar1.desc", "")]
    assert not missing, f"pillar1 lost the Bedrock/self-hosted choice: {missing}"


def test_meta_titles_carry_the_brand(en) -> None:
    missing = {}
    for locale in LOCALES:
        data = _locale(locale)
        lost = sorted(
            k for k in data
            if k.startswith("meta.") and k.endswith(".title") and k in en
            and "AssureProof" not in data[k]
        )
        if lost:
            missing[locale] = lost[:5]
    assert not missing, f"meta titles without the brand: {missing}"


def test_meta_values_are_not_english_fallbacks(en) -> None:
    """meta.* values byte-equal to EN shipped three times (#151 x2, #153)."""
    equal = {}
    for locale in LOCALES:
        data = _locale(locale)
        same = sorted(k for k in data if k.startswith("meta.") and k in en
                      and data[k].strip() == en[k].strip() and en[k].strip())
        if same:
            equal[locale] = same[:10]
    assert not equal, f"meta values still English: {equal}"


def test_dist_locale_titles_are_localized(en) -> None:
    """No non-English page may carry the English homepage title (the
    homepage itself shipped it in seven locales until PR #154)."""
    pages = _dist_pages()
    en_title = en.get("meta.landing.title", "")
    bad = []
    for page in pages:
        rel = page.relative_to(DIST).as_posix()
        if rel == "index.html":  # the en homepage IS this title, by definition
            continue
        m = re.search(r"<title>(.*?)</title>", page.read_text(encoding="utf-8"), re.S)
        if m and en_title and en_title in m.group(1):
            bad.append(rel)
    assert not bad, f"generic English title on locale pages: {bad[:10]}"


def test_dist_finance_detail_pages_are_translated() -> None:
    """uc.fin.* renders via Jinja loops; six locales shipped it as English
    (PR #153). Checked on dist because the keys are template-resolved."""
    pages = _dist_pages()
    markers = ("Purchase order", "three-way match")
    bad = []
    for page in pages:
        rel = page.relative_to(DIST).as_posix()
        if "/use-cases/finance/" in rel and _locale_of(page) != "en":
            text = page.read_text(encoding="utf-8")
            if any(marker in text for marker in markers):
                bad.append(rel)
    assert not bad, f"English finance detail content: {bad[:10]}"
