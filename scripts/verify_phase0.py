#!/usr/bin/env python3
"""Phase 0 pre-flight validation against a live Assure container."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = os.environ.get("ASSURE_BASE_URL", "http://127.0.0.1:8765").rstrip("/")
WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "whsec_test_secret_for_local_validation")
DB_PATH = Path(os.environ.get("DATABASE_PATH", "./data/history.sqlite"))
TEST_USER = "clerk_test_user_001"
COMPOSE_SERVICE = os.environ.get("COMPOSE_SERVICE", "assure-app")


def _request(method: str, path: str, *, headers: dict | None = None, body: bytes | None = None):
    req = urllib.request.Request(
        BASE_URL + path,
        data=body,
        headers=headers or {},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def check_health() -> tuple[bool, str]:
    status, _, body = _request("GET", "/api/health")
    if status != 200:
        return False, f"HTTP {status}"
    try:
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        return False, "invalid JSON"
    if payload.get("status") != "ok":
        return False, f"status={payload.get('status')!r}"
    return True, "HTTP 200, status=ok"


def check_cors() -> tuple[bool, str]:
    headers = {
        "Origin": "https://getassureai.com",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "Authorization, X-Gemini-Key, X-Claude-Key",
    }
    status, resp_headers, _ = _request("OPTIONS", "/api/health", headers=headers)
    if status != 200:
        return False, f"HTTP {status}"
    allow_origin = resp_headers.get("Access-Control-Allow-Origin", "")
    allow_headers = resp_headers.get("Access-Control-Allow-Headers", "")
    if allow_origin not in {"https://getassureai.com", "*"}:
        return False, f"Allow-Origin={allow_origin!r}"
    needed = {"authorization", "x-gemini-key", "x-claude-key"}
    got = {h.strip().lower() for h in allow_headers.split(",") if h.strip()}
    if not needed.issubset(got) and allow_headers != "*":
        return False, f"Allow-Headers={allow_headers!r}"
    return True, f"HTTP 200, Allow-Origin={allow_origin}"


def _stripe_signature(payload: bytes, secret: str) -> str:
    timestamp = str(int(time.time()))
    signed = timestamp.encode("utf-8") + b"." + payload
    digest = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def check_webhook_sqlite() -> tuple[bool, str]:
    event = {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "client_reference_id": TEST_USER,
            }
        },
    }
    payload = json.dumps(event).encode("utf-8")
    sig = _stripe_signature(payload, WEBHOOK_SECRET)
    status, _, body = _request(
        "POST",
        "/api/webhooks/stripe",
        headers={
            "Content-Type": "application/json",
            "Stripe-Signature": sig,
        },
        body=payload,
    )
    if status != 200:
        detail = body.decode("utf-8", errors="replace")[:200]
        return False, f"HTTP {status}: {detail}"

    if not DB_PATH.is_file():
        return False, f"missing sqlite file {DB_PATH}"

    conn = sqlite3.connect(str(DB_PATH))
    try:
        row = conn.execute(
            "SELECT clerk_user_id, tier FROM user_subscriptions WHERE clerk_user_id = ?",
            (TEST_USER,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return False, "no user_subscriptions row"
    if row[1] != "pro":
        return False, f"tier={row[1]!r}"
    return True, f"HTTP 200, {TEST_USER} tier=pro in {DB_PATH}"


def check_docker_memory() -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["docker", "compose", "ps", "-q", COMPOSE_SERVICE],
            capture_output=True,
            text=True,
            check=True,
            cwd=Path(__file__).resolve().parents[1],
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        return False, f"compose ps failed: {exc}"

    container_id = proc.stdout.strip().splitlines()
    if not container_id:
        return False, "container not running"
    cid = container_id[0]

    try:
        stats = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", cid],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        return False, f"docker stats failed: {exc}"

    usage = stats.stdout.strip()
    if not usage:
        return False, "empty docker stats"
    return True, usage


def main() -> int:
    checks = [
        ("Healthcheck", check_health),
        ("CORS preflight", check_cors),
        ("Webhook + SQLite", check_webhook_sqlite),
        ("Container RAM", check_docker_memory),
    ]
    results: list[tuple[str, bool, str]] = []
    for name, fn in checks:
        ok, detail = fn()
        results.append((name, ok, detail))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    print()
    print("| Check | Status | Detail |")
    print("| --- | --- | --- |")
    for name, ok, detail in results:
        print(f"| {name} | {'PASS' if ok else 'FAIL'} | {detail} |")

    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
