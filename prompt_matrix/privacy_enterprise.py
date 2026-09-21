"""Enterprise privacy policy HTML bodies (marketing site)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_CONTENT_DIR = Path(__file__).resolve().parent / "content" / "privacy"


@lru_cache(maxsize=len(("en", "es", "zh", "fr", "de", "ja", "tr")))
def privacy_body_html(locale: str) -> str:
    code = (locale or "en").split("-")[0].lower()
    path = _CONTENT_DIR / f"{code}.html"
    if not path.is_file():
        path = _CONTENT_DIR / "en.html"
    return path.read_text(encoding="utf-8")
