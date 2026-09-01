"""Fernet helpers for cloud API-key blobs. Never log plaintext keys."""

from __future__ import annotations

import os


class EncryptionError(ValueError):
    pass


def _key_bytes() -> bytes:
    raw = (os.environ.get("ENCRYPTION_KEY") or "").strip().strip('"').strip("'")
    if not raw:
        raise EncryptionError("ENCRYPTION_KEY is not set")
    return raw.encode("utf-8")


def get_cipher():
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:
        raise EncryptionError("cryptography is not installed") from exc
    try:
        return Fernet(_key_bytes())
    except Exception as exc:
        raise EncryptionError("ENCRYPTION_KEY is not a Fernet key") from exc


def encrypt_text(text: str) -> str:
    return get_cipher().encrypt((text or "").encode("utf-8")).decode("utf-8")


def decrypt_text(encrypted: str) -> str:
    return get_cipher().decrypt((encrypted or "").encode("utf-8")).decode("utf-8")
