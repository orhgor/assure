"""Open Memory Protocol client for the local OMP server.

Talks to ``/v1/memories`` (see ``~/.omp/verify.py``). The API key is read from
``~/.omp/api_key`` or ``OMP_API_KEY`` and is never logged.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

OMP_SERVER = os.getenv("OMP_SERVER", "http://localhost:3456")
API_KEY_PATH = os.path.expanduser("~/.omp/api_key")
DEFAULT_NAMESPACE = "project:prompt-matrix"

_log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log_error(message: str) -> None:
    try:
        from flask import current_app, has_app_context

        if has_app_context():
            current_app.logger.error(message)
            return
    except Exception:
        pass
    _log.error(message)


def _get_headers() -> dict[str, str]:
    api_key = ""
    try:
        with open(API_KEY_PATH, encoding="utf-8") as handle:
            api_key = handle.read().strip()
    except FileNotFoundError:
        api_key = os.getenv("OMP_API_KEY", "")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _request(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base = (os.getenv("OMP_SERVER") or OMP_SERVER).rstrip("/")
    url = base + path
    if params:
        query = urllib.parse.urlencode(
            {k: v for k, v in params.items() if v is not None and v != ""}
        )
        if query:
            url = f"{url}?{query}"
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=_get_headers(), method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read()
            if resp.status == 204 or not raw:
                return {"ok": True, "status": int(resp.status)}
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {"data": parsed}
    except urllib.error.HTTPError as err:
        raw = err.read()
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"error": raw.decode("utf-8", errors="replace")}
        if not isinstance(parsed, dict):
            parsed = {"error": str(parsed)}
        parsed.setdefault("error", f"HTTP {err.code}")
        parsed["status"] = int(err.code)
        _log_error(f"OMP {method} {path} failed: HTTP {err.code}")
        return parsed
    except Exception as exc:
        _log_error(f"OMP {method} {path} failed: {exc}")
        return {"error": str(exc)}


def omp_remember(key: str, content: str, tags: list | None = None) -> dict[str, Any]:
    """Store a memory. ``key`` is kept as a tag; OMP identifies rows by ``id``."""
    tag_list = [str(t) for t in (tags or []) if t]
    if key and key not in tag_list:
        tag_list.append(str(key))
    payload = {
        "content": content,
        "type": "semantic",
        "tags": tag_list,
        "namespace": DEFAULT_NAMESPACE,
        "source": {"tool": "assure", "timestamp": _now()},
    }
    return _request("POST", "/v1/memories", body=payload)


def omp_recall(key: str) -> dict[str, Any]:
    """Search memories for ``key`` (keyword recall, not a REST path key)."""
    return _request(
        "POST",
        "/v1/memories/search",
        body={"q": key, "limit": 10, "mode": "keyword"},
    )


def omp_list_memories(tags: list | None = None) -> dict[str, Any]:
    """List recent memories, optionally filtered by tags."""
    params: dict[str, Any] = {"limit": 20, "namespace": DEFAULT_NAMESPACE}
    if tags:
        params["tags"] = ",".join(str(t) for t in tags if t)
    return _request("GET", "/v1/memories", params=params)


def omp_health() -> dict[str, Any]:
    """Unauthenticated ping of the local OMP server."""
    base = (os.getenv("OMP_SERVER") or OMP_SERVER).rstrip("/")
    req = urllib.request.Request(base + "/v1/health", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=2) as resp:
            raw = resp.read()
            parsed = json.loads(raw) if raw else {}
            return parsed if isinstance(parsed, dict) else {"status": "ok"}
    except Exception as exc:
        return {"status": "down", "error": str(exc)}
