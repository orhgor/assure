"""Cloud billing: Supabase user row + Stripe test checkout.

Stores email and tier only. Never prompts, never API keys.
No extra pip packages: REST over urllib.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from flask import session


class BillingError(ValueError):
    pass


def load_cloud_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    from pathlib import Path

    pkg = Path(__file__).resolve().parent
    load_dotenv(pkg / ".env", override=False)
    load_dotenv(pkg.parent / ".env", override=False)


def supabase_url() -> str:
    url = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
    if url.endswith("/rest/v1"):
        url = url[: -len("/rest/v1")].rstrip("/")
    return url


def supabase_key() -> str:
    """Prefer the server write key. New Console names first, then the old ones."""
    return (
        (os.environ.get("SUPABASE_SECRET_KEY") or "").strip()
        or (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
        or (os.environ.get("SUPABASE_PUBLISHABLE_KEY") or "").strip()
        or (os.environ.get("SUPABASE_ANON_KEY") or "").strip()
    )


def supabase_configured() -> bool:
    return bool(supabase_url() and supabase_key())


def stripe_secret() -> str:
    return (os.environ.get("STRIPE_SECRET_KEY") or "").strip()


def stripe_webhook_secret() -> str:
    return (os.environ.get("STRIPE_WEBHOOK_SECRET") or "").strip()


def stripe_price_id() -> str:
    return (os.environ.get("STRIPE_PRICE_ID") or "").strip()


def stripe_configured() -> bool:
    return bool(stripe_secret())


def is_cloud_mode() -> bool:
    try:
        from .cloud_auth import is_self_hosted
    except ImportError:
        from cloud_auth import is_self_hosted
    if is_self_hosted():
        return False
    env = (os.environ.get("ASSURE_EDITION") or os.environ.get("PEM_EDITION") or "").strip().lower()
    return env.replace("_", "-") == "cloud"


def bind_request_tier() -> None:
    """Point editions.current_edition at this request's free/pro row."""
    try:
        from .cloud_auth import current_user_id, is_self_hosted
        from .editions import set_request_plan
    except ImportError:
        from cloud_auth import current_user_id, is_self_hosted
        from editions import set_request_plan
    if is_self_hosted() or not is_cloud_mode():
        set_request_plan(None)
        return
    user_id = current_user_id()
    email = session.get("clerk_email") or ""
    if user_id and supabase_configured():
        row = ensure_user(user_id, email)
        tier = (row.get("tier") or "free").strip().lower()
        if tier not in {"free", "pro"}:
            tier = "free"
        session["assure_tier"] = tier
        set_request_plan(tier)
        return
    set_request_plan(session.get("assure_tier") or "free")


def ensure_user(user_id: str, email: str = "") -> dict[str, Any]:
    if not supabase_configured():
        return {"id": user_id, "email": email, "tier": session.get("assure_tier") or "free"}
    existing = get_user(user_id)
    if existing:
        if email and existing.get("email") != email:
            return _supabase_patch(user_id, {"email": email, "updated_at": "now()"})
        return existing
    payload = {
        "id": user_id,
        "email": email or f"{user_id}@local.invalid",
        "tier": "free",
    }
    return _supabase_insert(payload)


def get_user(user_id: str) -> dict[str, Any] | None:
    rows = _supabase_get(f"/rest/v1/users?id=eq.{urllib.parse.quote(user_id)}&select=*")
    if not rows:
        return None
    return rows[0]


def set_tier(user_id: str, tier: str, *, stripe_customer_id: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"tier": tier, "updated_at": "now()"}
    if stripe_customer_id:
        body["stripe_customer_id"] = stripe_customer_id
    if supabase_configured():
        return _supabase_patch(user_id, body)
    session["assure_tier"] = tier
    return {"id": user_id, "tier": tier}


def delete_user(user_id: str) -> None:
    if supabase_configured():
        quoted = urllib.parse.quote(user_id)
        for path in (
            f"/rest/v1/credit_transactions?user_id=eq.{quoted}",
            f"/rest/v1/credit_wallets?user_id=eq.{quoted}",
            f"/rest/v1/user_settings?user_id=eq.{quoted}",
            f"/rest/v1/users?id=eq.{quoted}",
        ):
            try:
                _supabase_delete(path)
            except Exception:
                continue
    session.pop("assure_tier", None)


def export_user(user_id: str) -> dict[str, Any]:
    row = get_user(user_id) if supabase_configured() else None
    return {
        "id": user_id,
        "email": (row or {}).get("email") or session.get("clerk_email") or "",
        "tier": (row or {}).get("tier") or session.get("assure_tier") or "free",
        "stripe_customer_id": (row or {}).get("stripe_customer_id") or "",
    }


def subscription_payload() -> dict[str, Any]:
    try:
        from .cloud_auth import current_user_id
        from .editions import current_edition
    except ImportError:
        from cloud_auth import current_user_id
        from editions import current_edition
    plan = current_edition()
    user_id = current_user_id()
    row = get_user(user_id) if (user_id and supabase_configured()) else None
    return {
        "tier": plan.id if plan.id in {"free", "pro", "team", "self-hosted"} else "free",
        "label": plan.label,
        "cloud": is_cloud_mode(),
        "signed_in": bool(user_id),
        "supabase": supabase_configured(),
        "stripe": stripe_configured(),
        "email": (row or {}).get("email") or session.get("clerk_email") or "",
        "user_id": user_id or "",
    }


def create_checkout_url(*, user_id: str, email: str, origin: str) -> str:
    if not stripe_configured():
        raise BillingError("Stripe is not set up")
    origin = origin.rstrip("/")
    fields: list[tuple[str, str]] = [
        ("mode", "subscription"),
        ("success_url", origin + "/account?upgraded=1"),
        ("cancel_url", origin + "/pricing"),
        ("client_reference_id", user_id),
        ("metadata[user_id]", user_id),
        ("line_items[0][quantity]", "1"),
    ]
    if email:
        fields.append(("customer_email", email))
    price = stripe_price_id()
    if price:
        fields.append(("line_items[0][price]", price))
    else:
        fields.extend(
            [
                ("line_items[0][price_data][currency]", "usd"),
                ("line_items[0][price_data][unit_amount]", "500"),
                ("line_items[0][price_data][recurring][interval]", "month"),
                ("line_items[0][price_data][product_data][name]", "Assure Pro"),
            ]
        )
    row = get_user(user_id) if supabase_configured() else None
    cust = (row or {}).get("stripe_customer_id")
    if cust:
        fields.append(("customer", str(cust)))
        fields = [item for item in fields if item[0] != "customer_email"]
    data = _stripe_post("/v1/checkout/sessions", fields)
    url = data.get("url")
    if not isinstance(url, str) or not url:
        raise BillingError("Stripe did not return a checkout URL")
    return url


def create_portal_url(*, user_id: str, origin: str) -> str:
    if not stripe_configured():
        raise BillingError("Stripe is not set up")
    row = get_user(user_id) if supabase_configured() else None
    cust = (row or {}).get("stripe_customer_id")
    if not cust:
        raise BillingError("No Stripe customer yet")
    data = _stripe_post(
        "/v1/billing_portal/sessions",
        [
            ("customer", str(cust)),
            ("return_url", origin.rstrip("/") + "/account"),
        ],
    )
    url = data.get("url")
    if not isinstance(url, str) or not url:
        raise BillingError("Stripe did not return a portal URL")
    return url


def apply_stripe_event(event: dict[str, Any]) -> None:
    kind = str(event.get("type") or "")
    obj = event.get("data", {}).get("object") or {}
    if not isinstance(obj, dict):
        return
    if kind == "checkout.session.completed":
        user_id = str(
            obj.get("client_reference_id") or (obj.get("metadata") or {}).get("user_id") or ""
        )
        customer = str(obj.get("customer") or "")
        if user_id:
            set_tier(user_id, "pro", stripe_customer_id=customer or None)
            try:
                from .credit_guard import apply_subscription
            except ImportError:
                from credit_guard import apply_subscription
            try:
                apply_subscription(user_id)
            except Exception:
                pass
        return
    if kind in {"customer.subscription.deleted", "customer.subscription.canceled"}:
        customer = str(obj.get("customer") or "")
        user_id = _user_id_for_customer(customer)
        if user_id:
            set_tier(user_id, "free")
        return
    if kind == "customer.subscription.updated":
        status = str(obj.get("status") or "")
        customer = str(obj.get("customer") or "")
        user_id = _user_id_for_customer(customer)
        if user_id and status in {"canceled", "unpaid", "incomplete_expired"}:
            set_tier(user_id, "free")
        elif user_id and status in {"active", "trialing"}:
            set_tier(user_id, "pro", stripe_customer_id=customer or None)


def verify_stripe_payload(payload: bytes, header: str) -> dict[str, Any]:
    secret = stripe_webhook_secret()
    if not secret:
        raise BillingError("STRIPE_WEBHOOK_SECRET is not set")
    items: dict[str, list[str]] = {}
    for piece in (header or "").split(","):
        key, _, val = piece.partition("=")
        items.setdefault(key.strip(), []).append(val.strip())
    timestamp = (items.get("t") or [""])[0]
    signatures = items.get("v1") or []
    if not timestamp or not signatures:
        raise BillingError("bad Stripe signature")
    signed = timestamp.encode("utf-8") + b"." + payload
    expect = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expect, sig) for sig in signatures):
        raise BillingError("bad Stripe signature")
    try:
        event = json.loads(payload.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise BillingError("bad Stripe body") from exc
    if not isinstance(event, dict):
        raise BillingError("bad Stripe body")
    return event


def _user_id_for_customer(customer: str) -> str:
    if not customer or not supabase_configured():
        return ""
    rows = _supabase_get(
        "/rest/v1/users?stripe_customer_id=eq." + urllib.parse.quote(customer) + "&select=id"
    )
    if not rows:
        return ""
    return str(rows[0].get("id") or "")


def _supabase_headers() -> dict[str, str]:
    key = supabase_key()
    return {
        "apikey": key,
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def _supabase_insert(payload: dict[str, Any]) -> dict[str, Any]:
    rows = _supabase_json(
        "POST",
        "/rest/v1/users",
        payload,
        extra={"Prefer": "return=representation,resolution=merge-duplicates"},
    )
    if isinstance(rows, list) and rows:
        return rows[0]
    if isinstance(rows, dict):
        return rows
    return payload


def _supabase_patch(user_id: str, body: dict[str, Any]) -> dict[str, Any]:
    clean = {k: v for k, v in body.items() if k != "updated_at"}
    rows = _supabase_json(
        "PATCH",
        "/rest/v1/users?id=eq." + urllib.parse.quote(user_id),
        clean,
    )
    if isinstance(rows, list) and rows:
        return rows[0]
    got = get_user(user_id)
    return got or {"id": user_id, **clean}


def _supabase_delete(path: str) -> None:
    _supabase_json("DELETE", path, None)


def _supabase_get(path: str) -> list[dict[str, Any]]:
    data = _supabase_json("GET", path, None)
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    return []


def supabase_request(
    method: str, path: str, body: dict | None = None, extra: dict | None = None
) -> Any:
    """Server-side REST. Callers must never send prompt text in `body`."""
    return _supabase_json(method, path, body, extra)


def _supabase_json(method: str, path: str, body: dict | None, extra: dict | None = None) -> Any:
    if not supabase_configured():
        raise BillingError("Supabase is not set up")
    headers = _supabase_headers()
    if extra:
        headers.update(extra)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(supabase_url() + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise BillingError("Supabase request failed") from exc
    except urllib.error.URLError as exc:
        raise BillingError("could not reach Supabase") from exc
    if not raw:
        return []
    return json.loads(raw)


def _stripe_post(path: str, fields: list[tuple[str, str]]) -> dict[str, Any]:
    secret = stripe_secret()
    body = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(
        "https://api.stripe.com" + path,
        data=body,
        method="POST",
        headers={
            "Authorization": "Bearer " + secret,
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise BillingError("Stripe request failed") from exc
    except urllib.error.URLError as exc:
        raise BillingError("could not reach Stripe") from exc
    parsed = json.loads(raw) if raw else {}
    return parsed if isinstance(parsed, dict) else {}
