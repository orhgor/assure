"""Encrypted API-key sync. Decrypt into the request only. Never write keys to disk."""

from __future__ import annotations

import json
from typing import Any

from flask import g, has_request_context

PROVIDER_JSON_TO_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "claude": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "kimi": "MOONSHOT_API_KEY",
}


def _client():
    try:
        from .supabase_client import get_supabase
    except ImportError:
        from supabase_client import get_supabase
    return get_supabase()


def _q(value: str) -> str:
    import urllib.parse

    return urllib.parse.quote(value, safe="")


def load_row(user_id: str) -> dict[str, Any] | None:
    uid = (user_id or "").strip()
    sb = _client()
    if not uid or sb is None:
        return None
    try:
        rows = sb.request(
            "GET",
            "/rest/v1/user_settings?user_id=eq." + _q(uid) + "&select=*",
        )
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            return rows[0]
    except Exception:
        return None
    return None


def decrypt_api_keys(blob: str | None) -> dict[str, str]:
    if not blob:
        return {}
    try:
        from .encryption import decrypt_text
    except ImportError:
        from encryption import decrypt_text
    try:
        parsed = json.loads(decrypt_text(blob))
    except Exception:
        return {}
    if not isinstance(parsed, dict):
        return {}
    out: dict[str, str] = {}
    for key, val in parsed.items():
        if isinstance(key, str) and isinstance(val, str) and val.strip():
            out[key] = val.strip()
    return out


def encrypt_api_keys(api_keys: dict[str, Any]) -> str:
    try:
        from .encryption import encrypt_text
    except ImportError:
        from encryption import encrypt_text
    clean = {}
    if isinstance(api_keys, dict):
        for key, val in api_keys.items():
            if isinstance(key, str) and isinstance(val, str) and val.strip():
                clean[key] = val.strip()
    return encrypt_text(json.dumps(clean, separators=(",", ":")))


def get_settings(user_id: str) -> dict[str, Any]:
    row = load_row(user_id)
    if not row:
        return {"api_keys": {}, "preferences": {}}
    prefs = row.get("preferences")
    if not isinstance(prefs, dict):
        prefs = {}
    return {
        "api_keys": decrypt_api_keys(row.get("encrypted_api_keys")),
        "preferences": prefs,
    }


def save_settings(user_id: str, *, api_keys: dict | None = None, preferences: dict | None = None) -> None:
    uid = (user_id or "").strip()
    sb = _client()
    if not uid or sb is None:
        raise RuntimeError("Supabase is not set up")
    payload: dict[str, Any] = {"user_id": uid}
    if api_keys is not None:
        payload["encrypted_api_keys"] = encrypt_api_keys(api_keys)
    if preferences is not None:
        payload["preferences"] = preferences if isinstance(preferences, dict) else {}
    sb.request(
        "POST",
        "/rest/v1/user_settings?on_conflict=user_id",
        payload,
        extra={"Prefer": "return=minimal,resolution=merge-duplicates"},
    )


def apply_cloud_keys(user_id: str | None = None) -> None:
    """Put decrypted cloud keys on flask.g for this request. Never writes .env."""
    if not has_request_context():
        return
    try:
        from .cloud_auth import current_user_id
    except ImportError:
        from cloud_auth import current_user_id
    uid = (user_id or current_user_id() or "").strip()
    if not uid:
        return
    try:
        keys = get_settings(uid).get("api_keys") or {}
    except Exception:
        return
    if not keys:
        return
    mapped: dict[str, str] = {}
    for name, value in keys.items():
        env_name = PROVIDER_JSON_TO_ENV.get(name.lower())
        if env_name and value:
            mapped[env_name] = value
            if env_name == "ANTHROPIC_API_KEY":
                mapped["ANTHROPIC_API_KEY"] = value
            if env_name == "GEMINI_API_KEY":
                mapped["GOOGLE_API_KEY"] = value
            if env_name == "MOONSHOT_API_KEY":
                mapped["KIMI_API_KEY"] = value
    g.cloud_api_keys = mapped
