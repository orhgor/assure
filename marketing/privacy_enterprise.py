"""Enterprise privacy policy HTML bodies (marketing site)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_CONTENT_DIR = Path(__file__).resolve().parent / "content" / "privacy"

# Short summary shown in the summary-box at the top of the privacy page.
# Kept here (not in i18n.py dicts) so every locale gets it via the catalog
# fallback, the same way the policy body is joined.
PRIVACY_SUMMARY = {
    "en": "This policy covers the getassureai.com website only: no analytics, no ad trackers, no cookies. We use your contact details only to reply and arrange a demo.",
    "es": "Esta política cubre solo el sitio getassureai.com: sin analítica, sin rastreadores de anuncios, sin cookies. Usamos sus datos de contacto solo para responderle y coordinar una demo.",
    "zh": "本政策仅覆盖 getassureai.com 网站：无分析脚本、无广告跟踪器、无 Cookie。您的联系方式仅用于回复和安排演示。",
    "fr": "Cette politique couvre uniquement le site getassureai.com : pas d'analytique, pas de traceurs publicitaires, pas de cookies. Vos coordonnées servent seulement à vous répondre et à organiser une démo.",
    "de": "Diese Richtlinie gilt nur für getassureai.com: keine Analyse-Skripte, keine Werbe-Tracker, keine Cookies. Ihre Kontaktdaten verwenden wir nur, um zu antworten und eine Demo zu vereinbaren.",
    "ja": "本ポリシーの対象は getassureai.com のみです。分析スクリプト、広告トラッカー、Cookie はありません。ご連絡先は回答とデモ調整のためだけに使用します。",
    "tr": "Bu politika yalnızca getassureai.com sitesini kapsar: analitik yok, reklam izleyici yok, cookie yok. İletişim bilgilerinizi yalnızca dönmek ve demo planlamak için kullanırız.",
    "ar": "تغطي هذه السياسة موقع getassureai.com فقط: لا تحليلات، ولا متتبعات إعلانات، ولا ملفات تعريف ارتباط. ونستخدم بيانات تواصلك فقط للرد وترتيب التجربة.",
}


def privacy_summary(locale: str) -> str:
    code = (locale or "en").split("-")[0].lower()
    return PRIVACY_SUMMARY.get(code, PRIVACY_SUMMARY["en"])


@lru_cache(maxsize=len(("en", "es", "zh", "fr", "de", "ja", "tr", "ar")))
def privacy_body_html(locale: str) -> str:
    code = (locale or "en").split("-")[0].lower()
    path = _CONTENT_DIR / f"{code}.html"
    if not path.is_file():
        path = _CONTENT_DIR / "en.html"
    return path.read_text(encoding="utf-8")
