"""Public landing waitlist. urllib REST to Supabase. No official SDK."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class DuplicateWaitlistError(Exception):
    """Email already on the waitlist (UNIQUE). The API treats this as success."""


class WaitlistUnavailableError(Exception):
    def __init__(self, message: str = "The waitlist is not open yet. Try again later.") -> None:
        super().__init__(message)


def is_valid_email(value: str) -> bool:
    raw = (value or "").strip()
    return bool(raw) and EMAIL_RE.match(raw) is not None


def insert_waitlist(name: str, email: str) -> None:
    """Insert one waitlist row. Omit created_at so the DB default applies.

    Duplicate email raises DuplicateWaitlistError. Missing Supabase raises
    WaitlistUnavailableError. Never logs keys.
    """
    try:
        from .cloud_billing import supabase_configured, supabase_key, supabase_url
    except ImportError:
        from cloud_billing import supabase_configured, supabase_key, supabase_url

    if not supabase_configured():
        raise WaitlistUnavailableError()
    url = supabase_url().rstrip("/") + "/rest/v1/waitlist"
    key = supabase_key()
    payload = json.dumps({"name": name, "email": email}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "apikey": key,
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 409:
            raise DuplicateWaitlistError() from exc
        raise WaitlistUnavailableError(
            "Could not save to the waitlist right now. Try again."
        ) from exc
    except urllib.error.URLError as exc:
        raise WaitlistUnavailableError(
            "Could not save to the waitlist right now. Try again."
        ) from exc
