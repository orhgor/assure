"""Enterprise privacy policy HTML bodies (marketing site)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_CONTENT_DIR = Path(__file__).resolve().parent / "content" / "privacy"

# Short summary shown in the summary-box at the top of the privacy page.
# Kept here (not in i18n.py dicts) so every locale gets it via the catalog
# fallback, the same way the policy body is joined.
PRIVACY_SUMMARY = {
    "en": "This policy covers the getassureai.com website only. The site uses privacy-friendly analytics and no advertising trackers. We use your contact details only to reply and arrange a review.",
    "es": "Esta política cubre únicamente el sitio web getassureai.com. El sitio utiliza analíticas que respetan la privacidad y no emplea rastreadores publicitarios. Usamos sus datos de contacto solo para responder y coordinar una revisión.",
    "zh": "本政策仅适用于 getassureai.com 网站。本网站使用保护隐私的分析工具，不使用广告追踪器。我们仅使用您的联系方式回复您并安排评估。",
    "fr": "Cette politique couvre uniquement le site web getassureai.com. Le site utilise des analyses respectueuses de la vie privée et aucun traqueur publicitaire. Nous utilisons vos coordonnées uniquement pour vous répondre et organiser un examen.",
    "de": "Diese Richtlinie gilt nur für die Website getassureai.com. Die Website verwendet datenschutzfreundliche Analysen und keine Werbe-Tracker. Wir verwenden Ihre Kontaktdaten ausschließlich, um zu antworten und eine Überprüfung zu vereinbaren.",
    "ja": "このポリシーはgetassureai.comウェブサイトのみに適用されます。当サイトでは、プライバシーに配慮した分析機能を使用しており、広告トラッカーは使用していません。お客様の連絡先は、ご返信およびレビューの手配にのみ使用します。",
    "tr": "Bu politika yalnızca getassureai.com web sitesini kapsar. Site, gizliliği gözeten analiz araçları kullanır ve reklam izleyicileri kullanmaz. İletişim bilgilerinizi yalnızca yanıt vermek ve bir değerlendirme ayarlamak için kullanırız.",
    "ar": "تغطي هذه السياسة موقع getassureai.com فقط. يستخدم الموقع تحليلات تراعي الخصوصية ولا يستخدم أدوات تتبع إعلانية. نستخدم تفاصيل الاتصال الخاصة بك فقط للرد وترتيب مراجعة.",
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
