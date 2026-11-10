"""Enterprise privacy policy HTML bodies (marketing site)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_CONTENT_DIR = Path(__file__).resolve().parent / "content" / "privacy"

# Short summary shown in the summary-box at the top of the privacy page.
# Kept here (not in i18n.py dicts) so every locale gets it via the catalog
# fallback, the same way the policy body is joined.
PRIVACY_SUMMARY = {
    "en": "This policy covers the getassureai.com website only. The site uses Google Analytics (G-54F5NE9Y0P) to measure visits. We use your contact details only to reply and arrange a review.",
    "es": "Esta política cubre únicamente el sitio web getassureai.com. El sitio utiliza Google Analytics (G-54F5NE9Y0P) para medir las visitas. Usamos sus datos de contacto solo para responder y coordinar una revisión.",
    "zh": "本政策仅适用于 getassureai.com 网站。本网站使用 Google Analytics（G-54F5NE9Y0P）统计访问。我们仅使用您的联系方式回复您并安排评估。",
    "fr": "Cette politique couvre uniquement le site web getassureai.com. Le site utilise Google Analytics (G-54F5NE9Y0P) pour mesurer les visites. Nous utilisons vos coordonnées uniquement pour vous répondre et organiser un examen.",
    "de": "Diese Richtlinie gilt nur für die Website getassureai.com. Die Website verwendet Google Analytics (G-54F5NE9Y0P), um Besuche zu messen. Wir verwenden Ihre Kontaktdaten ausschließlich, um zu antworten und eine Überprüfung zu vereinbaren.",
    "ja": "このポリシーはgetassureai.comウェブサイトのみに適用されます。当サイトでは、訪問を測定するために Google Analytics（G-54F5NE9Y0P）を使用しています。お客様の連絡先は、ご返信およびレビューの手配にのみ使用します。",
    "tr": "Bu politika yalnızca getassureai.com web sitesini kapsar. Site, ziyaretleri ölçmek için Google Analytics (G-54F5NE9Y0P) kullanır. İletişim bilgilerinizi yalnızca yanıt vermek ve bir değerlendirme ayarlamak için kullanırız.",
    "ar": "تغطي هذه السياسة موقع getassureai.com فقط. يستخدم الموقع Google Analytics (G-54F5NE9Y0P) لقياس الزيارات. نستخدم تفاصيل الاتصال الخاصة بك فقط للرد وترتيب مراجعة.",
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
