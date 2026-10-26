"""Enterprise privacy policy HTML bodies (marketing site)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_CONTENT_DIR = Path(__file__).resolve().parent / "content" / "privacy"

# Short summary shown in the summary-box at the top of the privacy page.
# Kept here (not in i18n.py dicts) so every locale gets it via the catalog
# fallback, the same way the policy body is joined.
PRIVACY_SUMMARY = {
    "en": "We store only the data needed to run the service. We never use your data to train models.",
    "es": "Solo almacenamos los datos necesarios para operar el servicio. Nunca usamos sus datos para entrenar modelos.",
    "zh": "我们仅存储运营服务所需的数据。我们从不使用您的数据训练模型。",
    "fr": "Nous ne stockons que les données nécessaires au fonctionnement du service. Nous n'utilisons jamais vos données pour entraîner des modèles.",
    "de": "Wir speichern nur die Daten, die für den Betrieb des Dienstes nötig sind. Wir verwenden Ihre Daten niemals zum Training von Modellen.",
    "ja": "サービスの運営に必要なデータのみを保存します。お客様のデータをモデルの学習に使用することはありません。",
    "tr": "Yalnızca hizmetin işletilmesi için gerekli verileri saklıyoruz. Verilerinizi hiçbir zaman model eğitmek için kullanmayız.",
    "ar": "نخزن فقط البيانات اللازمة لتشغيل الخدمة. لا نستخدم بياناتك أبدًا لتدريب نماذجنا.",
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
