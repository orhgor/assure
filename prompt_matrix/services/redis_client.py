"""One Redis client for the process, from ``REDIS_URL``.

Redis carries the state that must be shared across web replicas and workers
and that has no business in PostgreSQL: the Celery broker and result backend,
flask-limiter's counters, and the short-lived debounce / de-duplication locks
around Red-Hat scheduling. Locally it is the ``redis`` service in
``docker-compose.yml`` / ``docker-compose.dev.yml``; on AWS it is
ElastiCache (Redis OSS, single small node or Serverless) reached over the VPC.

Without ``REDIS_URL`` every caller falls back to its process-local behaviour
(memory rate limits, thread-timer debounce, eager Celery) — correct on one
process, and stated as such by ``redis_configured()`` so a multi-replica
deployment can refuse to start without it (``web.create_app``).
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

log = logging.getLogger(__name__)

_client: Any | None = None
_client_url: str | None = None
_lock = threading.Lock()


def redis_url() -> str | None:
    raw = (os.environ.get("REDIS_URL") or "").strip()
    return raw or None


def redis_configured() -> bool:
    return redis_url() is not None


def get_redis() -> Any | None:
    """The shared client, or ``None`` when ``REDIS_URL`` is unset.

    Connections are lazy and pooled by redis-py; the client is safe to share
    across threads/greenlets. A Redis that is configured but unreachable raises
    at first use — deliberately, because silently degrading to per-process
    state on a multi-replica deployment is the failure this module exists to
    prevent.
    """
    global _client, _client_url
    url = redis_url()
    if url is None:
        return None
    if _client is not None and _client_url == url:
        return _client
    with _lock:
        if _client is not None and _client_url == url:
            return _client
        import redis

        _client = redis.Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=float(os.environ.get("REDIS_CONNECT_TIMEOUT", "2")),
            socket_timeout=float(os.environ.get("REDIS_SOCKET_TIMEOUT", "5")),
            health_check_interval=30,
        )
        _client_url = url
        return _client


def acquire_lock(key: str, ttl_seconds: int, *, token: str = "1") -> bool:
    """SET NX EX: True when this caller now holds ``key`` for ``ttl_seconds``."""
    client = get_redis()
    if client is None:
        return True
    return bool(client.set(f"assure:lock:{key}", token, nx=True, ex=ttl_seconds))


def release_lock(key: str) -> None:
    client = get_redis()
    if client is None:
        return
    try:
        client.delete(f"assure:lock:{key}")
    except Exception as exc:
        log.warning("redis lock release failed for %s: %s", key, exc)


def ping() -> bool:
    client = get_redis()
    if client is None:
        return False
    try:
        return bool(client.ping())
    except Exception:
        return False
