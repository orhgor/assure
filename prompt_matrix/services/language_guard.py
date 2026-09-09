"""LLM language-switching guards — keep responses aligned with UI locale."""

from __future__ import annotations

import re
from contextvars import ContextVar
from typing import Any

try:
    from ..i18n import LOCALES, normalize_locale
except ImportError:
    from i18n import LOCALES, normalize_locale

LANGUAGE_SYSTEM_INSTRUCTION = (
    "HARD LANGUAGE RULE: You MUST write the entire user-visible response in {language}. "
    "Do not switch languages, mix languages, or translate quoted source text unless the user "
    "explicitly requests another language. Keep code snippets, JSON keys, URLs, and proper nouns "
    "in their original form."
)

LOCALE_LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "es": "Spanish",
    "zh": "Chinese",
    "fr": "French",
    "de": "German",
    "ja": "Japanese",
    "tr": "Turkish",
}

# Native-language one-liners — models follow the target language more reliably
# when the instruction itself is in that language.
LOCALE_NATIVE_INSTRUCTION: dict[str, str] = {
    "en": "Always respond in English.",
    "tr": "Her zaman Türkçe yanıt ver.",
    "es": "Responde siempre en español.",
    "fr": "Réponds toujours en français.",
    "de": "Antworte immer auf Deutsch.",
    "ja": "常に日本語で応答してください。",
    "zh": "请始终用中文回复。",
}

_JSON_MODE = frozenset({"json_object", "json_schema"})

_request_locale: ContextVar[str | None] = ContextVar("assure_request_locale", default=None)


def set_request_locale(locale: str | None) -> None:
    _request_locale.set(normalize_locale(locale) if locale else None)


def get_request_locale() -> str | None:
    return _request_locale.get()


def locale_to_language_name(locale: str | None) -> str:
    code = normalize_locale(locale) if locale else "en"
    return LOCALE_LANGUAGE_NAMES.get(code, "English")


def get_language_instruction(locale: str | None = None) -> str:
    """Return a native-language system instruction for the UI locale."""
    code = normalize_locale(locale) if locale else "en"
    return LOCALE_NATIVE_INSTRUCTION.get(code, LOCALE_NATIVE_INSTRUCTION["en"])


def append_language_instruction(system_content: str, locale: str | None = None) -> str:
    """Append native + English hard language rules to system content."""
    loc = normalize_locale(locale) if locale else "en"
    native = get_language_instruction(loc)
    rule = LANGUAGE_SYSTEM_INSTRUCTION.format(language=locale_to_language_name(loc))
    block = f"{native}\n{rule}"
    base = (system_content or "").strip()
    if "HARD LANGUAGE RULE" in base:
        return base
    if not base:
        return block
    return f"{base}\n\n{block}"


def build_messages_with_language_guard(
    system: str,
    user: str,
    locale: str | None = None,
) -> list[dict[str, str]]:
    """Build a standard system+user pair with the language guard applied."""
    loc = _effective_locale(locale)
    return [
        {"role": "system", "content": append_language_instruction(system, loc)},
        {"role": "user", "content": user},
    ]


def resolve_request_locale() -> str:
    """Resolve locale from Flask request (body, query, session, Accept-Language) or context."""
    ctx = get_request_locale()
    if ctx:
        return ctx

    try:
        from flask import has_request_context, request, session
    except ImportError:
        return "en"

    if not has_request_context():
        return "en"

    if request.is_json:
        data = request.get_json(silent=True)
        if isinstance(data, dict):
            for key in ("locale", "lang"):
                raw = data.get(key)
                if raw:
                    return normalize_locale(str(raw))

    for key in ("locale", "lang"):
        arg = request.args.get(key)
        if arg:
            return normalize_locale(arg)

    if session.get("lang"):
        return normalize_locale(session.get("lang"))

    cookie = request.cookies.get("assure_lang")
    if cookie:
        return normalize_locale(cookie)

    return "en"


def _effective_locale(locale: str | None) -> str:
    if locale is not None:
        return normalize_locale(locale)
    return resolve_request_locale()


def is_json_response_mode(extra: dict[str, Any] | None) -> bool:
    rf = (extra or {}).get("response_format")
    if not isinstance(rf, dict):
        return False
    return str(rf.get("type") or "").lower() in _JSON_MODE


_is_json_response_mode = is_json_response_mode


def guard_messages(
    messages: list[dict[str, Any]],
    locale: str | None = None,
    *,
    skip: bool = False,
) -> list[dict[str, Any]]:
    """Return a copy of messages with the language guard applied to the system role."""
    if skip or not messages:
        return list(messages)

    loc = _effective_locale(locale)
    out = [dict(msg) for msg in messages]

    for index, msg in enumerate(out):
        if msg.get("role") != "system":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            out[index] = {**msg, "content": append_language_instruction(content, loc)}
            return out

    return [{"role": "system", "content": append_language_instruction("", loc)}] + out


_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
_KANA_RE = re.compile(r"[\u3040-\u30ff]")
_LATIN_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]")


def ensure_response_language(text: str, target_locale: str | None) -> str:
    """Lightweight post-check without langdetect; returns text unchanged when uncertain."""
    content = (text or "").strip()
    if not content or content.startswith("ERROR:"):
        return text

    locale = normalize_locale(target_locale)
    if locale == "en":
        return text

    letters = _LATIN_RE.findall(content)
    cjk = len(_CJK_RE.findall(content))
    kana = len(_KANA_RE.findall(content))

    if locale == "zh" and cjk == 0 and len(letters) > 40:
        return text
    if locale == "ja" and kana == 0 and cjk == 0 and len(letters) > 40:
        return text

    return text
