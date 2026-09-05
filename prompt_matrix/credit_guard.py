"""Credit wallet: signup bonus, pre-Send check, atomic deduct after success.

CLI and source-install (no CLERK_PUBLISHABLE_KEY) skip every check.
Never stores prompt text. Supabase failures fall back to local unlimited.
"""

from __future__ import annotations

from typing import Any

CREDIT_EXHAUSTED = "You have 0 credits. Subscribe to Pro to continue."
SIGNUP_BONUS = 100
PRO_MONTHLY_CREDITS = 100

try:
    from .engine import MatrixError
except ImportError:
    from engine import MatrixError


class CreditExhaustedError(MatrixError):
    def __init__(self, message: str | None = None):
        super().__init__(message or CREDIT_EXHAUSTED)


def credits_enforced() -> bool:
    """False for CLI, self-hosted, and source-install without a Clerk publishable key."""
    try:
        from .cloud_auth import auth_required, clerk_publishable_key
    except ImportError:
        from cloud_auth import auth_required, clerk_publishable_key
    if not clerk_publishable_key():
        return False
    return auth_required()


def active_user_id(user_id: str | None = None) -> str | None:
    if not credits_enforced():
        return None
    if user_id:
        return str(user_id)
    try:
        from flask import has_request_context
    except ImportError:
        return None
    if not has_request_context():
        return None
    try:
        from .cloud_auth import current_user_id
    except ImportError:
        from cloud_auth import current_user_id
    return current_user_id()


def _client():
    try:
        from .supabase_client import get_supabase
    except ImportError:
        from supabase_client import get_supabase
    return get_supabase()


def ensure_wallet(user_id: str | None = None) -> dict[str, Any] | None:
    """Insert 100 free credits on first Clerk login. Idempotent. Never logs prompts."""
    uid = (user_id or "").strip()
    if not uid:
        return None
    sb = _client()
    if sb is None:
        return None
    try:
        rows = sb.request(
            "GET",
            "/rest/v1/credit_wallets?user_id=eq." + _q(uid) + "&select=*",
        )
        if isinstance(rows, list) and rows:
            return rows[0] if isinstance(rows[0], dict) else None
        sb.request(
            "POST",
            "/rest/v1/credit_wallets",
            {"user_id": uid, "balance": SIGNUP_BONUS, "tier": "free"},
            extra={"Prefer": "return=representation,resolution=merge-duplicates"},
        )
        sb.request(
            "POST",
            "/rest/v1/credit_transactions",
            {"user_id": uid, "amount": SIGNUP_BONUS, "type": "signup_bonus"},
            extra={"Prefer": "return=minimal"},
        )
        rows = sb.request(
            "GET",
            "/rest/v1/credit_wallets?user_id=eq." + _q(uid) + "&select=*",
        )
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            return rows[0]
    except Exception:
        return None
    return {"user_id": uid, "balance": SIGNUP_BONUS, "tier": "free"}


def get_wallet(user_id: str | None = None) -> dict[str, Any] | None:
    uid = (user_id or "").strip()
    if not uid:
        return None
    sb = _client()
    if sb is None:
        return None
    try:
        rows = sb.request(
            "GET",
            "/rest/v1/credit_wallets?user_id=eq." + _q(uid) + "&select=*",
        )
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            return rows[0]
    except Exception:
        return None
    return None


def list_transactions(user_id: str, *, limit: int = 10) -> list[dict[str, Any]]:
    uid = (user_id or "").strip()
    if not uid:
        return []
    sb = _client()
    if sb is None:
        return []
    cap = max(1, min(int(limit), 50))
    try:
        rows = sb.request(
            "GET",
            "/rest/v1/credit_transactions?user_id=eq."
            + _q(uid)
            + "&select=id,user_id,amount,type,created_at&order=created_at.desc&limit="
            + str(cap),
        )
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    except Exception:
        return []
    return []


def assert_has_credit(user_id: str | None = None, cost: int = 1) -> None:
    """Fail fast before a Send. Does not deduct."""
    uid = active_user_id(user_id)
    if not uid:
        return
    sb = _client()
    if sb is None:
        return
    try:
        ensure_wallet(uid)
        row = get_wallet(uid) or {}
        balance = int(row.get("balance") or 0)
    except Exception:
        return
    if balance < max(1, int(cost)):
        raise CreditExhaustedError()


def check_and_deduct(user_id: str | None = None, cost: int = 1) -> None:
    """Atomic deduct after a successful Send. No-op for CLI / source-install.

    Call only after a reply is in hand. Failed Sends must not reach this.
    """
    uid = active_user_id(user_id)
    if not uid:
        return
    sb = _client()
    if sb is None:
        return
    amount = max(1, int(cost))
    try:
        ok = sb.rpc("spend_credit", {"p_user_id": uid, "p_cost": amount})
    except Exception:
        return
    if ok is False:
        raise CreditExhaustedError()


def apply_subscription(user_id: str, amount: int = PRO_MONTHLY_CREDITS) -> None:
    """Pro checkout: add credits to the existing balance. Do not block if a wallet exists."""
    uid = (user_id or "").strip()
    if not uid:
        return
    sb = _client()
    if sb is None:
        return
    try:
        sb.rpc(
            "add_wallet_credits",
            {
                "p_user_id": uid,
                "p_amount": int(amount),
                "p_type": "subscription_renewal",
                "p_tier": "pro",
            },
        )
    except Exception:
        return


def usage_payload(user_id: str | None = None) -> dict[str, Any]:
    uid = (user_id or "").strip()
    if not uid or _client() is None:
        return {
            "balance": None,
            "tier": "local",
            "unlimited": True,
            "transactions": [],
        }
    ensure_wallet(uid)
    row = get_wallet(uid) or {}
    return {
        "balance": int(row.get("balance") or 0),
        "tier": str(row.get("tier") or "free"),
        "unlimited": False,
        "transactions": list_transactions(uid, limit=10),
    }


def _q(value: str) -> str:
    import urllib.parse

    return urllib.parse.quote(value, safe="")
