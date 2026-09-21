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
import re
from datetime import datetime, timezone
from typing import Any

OMP_SERVER = os.getenv("OMP_SERVER", "http://localhost:3456")
API_KEY_PATH = os.path.expanduser("~/.omp/api_key")
DEFAULT_NAMESPACE = "project:prompt-matrix"
OMP_CONTENT_MAX = 9000
_TAG_RE = re.compile(r"[^a-z0-9_:\-]+")
SAFE_OMP_TIMEOUT = 2.0

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
    timeout: float = 5,
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
        with urllib.request.urlopen(req, timeout=timeout) as resp:
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


def sanitize_omp_tag(value: str, *, max_len: int = 50) -> str:
    """OMP tags must match ``^[a-z0-9_:-]+$`` and are at most 50 characters."""
    cleaned = _TAG_RE.sub("-", str(value or "").lower()).strip("-")
    return (cleaned or "x")[:max_len]


def _sanitize_tags(tags: list | None, key: str = "") -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in list(tags or []) + ([key] if key else []):
        tag = sanitize_omp_tag(str(raw))
        if tag and tag not in seen:
            seen.add(tag)
            out.append(tag)
        if len(out) >= 20:
            break
    return out


def omp_remember(key: str, content: str, tags: list | None = None) -> dict[str, Any]:
    """Store a memory. ``key`` is kept as a tag; OMP identifies rows by ``id``."""
    text = str(content or "")
    if len(text) > OMP_CONTENT_MAX:
        text = text[: OMP_CONTENT_MAX - 1] + "…"
    payload = {
        "content": text,
        "type": "semantic",
        "tags": _sanitize_tags(tags, key),
        "namespace": DEFAULT_NAMESPACE,
        "source": {"tool": "assure", "timestamp": _now()},
    }
    return _request("POST", "/v1/memories", body=payload, timeout=SAFE_OMP_TIMEOUT)


def omp_recall(key: str, limit: int = 10) -> dict[str, Any]:
    """Search memories for ``key`` (keyword recall, not a REST path key).

    ``limit`` is the ranking window: a caller whose payload competes with large
    cache blobs must widen it, or its memories never appear in the result.
    """
    query = str(key or "").strip()
    if not query:
        return {"memories": [], "total": 0}
    return _request(
        "POST",
        "/v1/memories/search",
        body={"q": query, "limit": int(limit), "mode": "keyword"},
        timeout=SAFE_OMP_TIMEOUT,
    )


def _best_memory_content(raw: dict[str, Any] | None, key: str) -> str | None:
    if not raw or not isinstance(raw, dict):
        return None
    memories = raw.get("memories") or raw.get("results") or []
    if raw.get("error") and not memories:
        return None
    if not isinstance(memories, list) or not memories:
        return None
    key_l = str(key or "").lower()
    for mem in memories:
        if not isinstance(mem, dict):
            continue
        content = str(mem.get("content") or "").strip()
        if not content:
            continue
        tags = [str(t).lower() for t in (mem.get("tags") or [])]
        if key_l and (key_l in tags or key_l in content.lower() or any(key_l in t for t in tags)):
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                return content
    first = memories[0]
    if not isinstance(first, dict):
        return None
    content = str(first.get("content") or "").strip()
    if not content:
        return None
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return content


def safe_omp_remember(key: str, content: Any, tags: list | None = None) -> dict[str, Any] | None:
    """Best-effort store. Never raises; returns None if OMP is down or rejects."""
    if not key:
        return None
    try:
        text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
        result = omp_remember(key, text, tags=tags)
        if result.get("id"):
            return result
        if result.get("error") or int(result.get("status") or 0) >= 400:
            return None
        return result
    except Exception as exc:
        _log.debug("safe_omp_remember(%s) skipped: %s", key, exc)
        return None


def safe_omp_recall(key: str) -> Any | None:
    """Best-effort recall. Returns memory text, or None on miss / downtime."""
    if not key:
        return None
    try:
        raw = omp_recall(key)
        return _best_memory_content(raw, key)
    except Exception as exc:
        _log.debug("safe_omp_recall(%s) skipped: %s", key, exc)
        return None


def omp_list_memories(
    tags: list | None = None,
    *,
    limit: int = 20,
    offset: int = 0,
    namespace: str | None = DEFAULT_NAMESPACE,
) -> dict[str, Any]:
    """List memories, optionally filtered by tags.

    ``tags`` is applied by the server *after* its own LIMIT/OFFSET, so it is only
    a convenience for the first page; callers that need every row of a namespace
    (e.g. the jdf prune pass) pass a large ``limit`` and page with ``offset``.
    """
    params: dict[str, Any] = {"limit": int(limit), "offset": int(offset)}
    if namespace:
        params["namespace"] = namespace
    if tags:
        params["tags"] = ",".join(str(t) for t in tags if t)
    return _request("GET", "/v1/memories", params=params)


def omp_delete_memory(memory_id: str) -> bool:
    """Delete one memory by id. True when the server removed it.

    OMP identifies rows by id: the key this app writes is only a tag, so
    replacing a document's chunk index means deleting the rows it wrote before.
    """
    if not memory_id:
        return False
    result = _request(
        "DELETE",
        f"/v1/memories/{urllib.parse.quote(str(memory_id), safe='')}",
        timeout=SAFE_OMP_TIMEOUT,
    )
    if result.get("ok") or int(result.get("status") or 0) == 204:
        return True
    _log_error(f"OMP DELETE /v1/memories/{memory_id} failed: {result.get('error')}")
    return False


def omp_health() -> dict[str, Any]:
    """Unauthenticated ping of the local OMP server."""
    base = (os.getenv("OMP_SERVER") or OMP_SERVER).rstrip("/")
    req = urllib.request.Request(base + "/v1/health", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=SAFE_OMP_TIMEOUT) as resp:
            raw = resp.read()
            parsed = json.loads(raw) if raw else {}
            return parsed if isinstance(parsed, dict) else {"status": "ok"}
    except Exception as exc:
        return {"status": "down", "error": str(exc)}
