"""GitHub Actions included-minute budget (account cap, not live billing unless configured)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

DEFAULT_INCLUDED_MINUTES = 3000
BILLING_URL = "https://github.com/settings/billing"


def _int_env(name: str, default: int | None = None) -> int | None:
    raw = (os.environ.get(name) or "").strip().replace(",", "")
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def _fetch_github_used_minutes() -> tuple[int | None, str]:
    token = (os.environ.get("GITHUB_BILLING_TOKEN") or "").strip()
    owner = (os.environ.get("GITHUB_BILLING_OWNER") or "orhgor").strip()
    if not token:
        return None, "config"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "assure-backstage",
    }
    urls = (
        f"https://api.github.com/users/{owner}/settings/billing/actions",
        f"https://api.github.com/orgs/{owner}/settings/billing/actions",
    )
    for url in urls:
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            continue
        used = payload.get("total_minutes_used")
        if used is None:
            continue
        try:
            return max(0, int(used)), "github_api"
        except (TypeError, ValueError):
            continue
    return None, "config"


def actions_budget() -> dict[str, Any]:
    included = (
        _int_env("GITHUB_ACTIONS_MINUTE_LIMIT", DEFAULT_INCLUDED_MINUTES)
        or DEFAULT_INCLUDED_MINUTES
    )
    used_env = _int_env("GITHUB_ACTIONS_MINUTES_USED")
    used_api, api_source = _fetch_github_used_minutes()
    if used_api is not None:
        used = used_api
        source = api_source
    elif used_env is not None:
        used = used_env
        source = "env"
    else:
        used = None
        source = "config"
    remaining = None if used is None else max(0, included - used)
    return {
        "included_minutes": included,
        "used_minutes": used,
        "remaining_minutes": remaining,
        "source": source,
        "billing_url": BILLING_URL,
        "policy": [
            "Included Actions cap is 3000 minutes for this account.",
            "CI runs on pull requests and on pushes to main or staging only.",
            "Synthetic canary is weekly (or manual), not every 10 minutes.",
        ],
    }
