"""Blinker signals for Red-Hat multi-pass audit triggers."""

from __future__ import annotations

import threading
from typing import Any

from blinker import Namespace

_signals = Namespace()

# Emitted after Z3 verification succeeds in compile/draft stream.
z3_verified = _signals.signal("z3:verified")

# Emitted when founder draft content is saved (debounced handler schedules audit).
draft_changed = _signals.signal("draft:changed")

_debounce_lock = threading.Lock()
_debounce_timers: dict[str, threading.Timer] = {}
_DEBOUNCE_S = 2.0


def _revoke_task(task_id: str | None) -> None:
    """Best-effort cancel of the previous audit task.

    Remote control needs a broker with broadcast (Redis, AMQP). SQS has none:
    the call would be a no-op at best, so it is skipped there and the
    generation check inside the task (``is_stale``) retires the old run.
    """
    if not task_id:
        return
    try:
        from prompt_matrix.celery_app import (
            broker_supports_control,
            celery_app,
            celery_broker_disabled,
        )
    except ImportError:
        from celery_app import broker_supports_control, celery_app, celery_broker_disabled
    if celery_broker_disabled() or not broker_supports_control():
        return
    try:
        celery_app.control.revoke(task_id, terminate=True)
    except Exception:
        pass


def _payload_key(project_id: str) -> str:
    return f"assure:redhat-debounce:payload:{project_id}"


# How long the latest debounced payload may sit in Redis. Far above the debounce
# window so an arming replica that is slow to fire still finds it; short enough
# that a payload orphaned by a crashed replica does not linger.
_PAYLOAD_TTL_S = 60


def _redis_debounce(project_id: str, payload: dict[str, Any]) -> bool:
    """Debounce across replicas: one timer per window, fed the *latest* edit.

    A per-process ``threading.Timer`` debounces only the edits this replica
    saw; with N replicas each would fire its own audit. With Redis, one
    ``SET NX EX`` key per project gates the window, so exactly one replica
    arms the timer and the others return without scheduling. Returns False
    when Redis is not configured so the caller keeps the local behaviour.

    The lock only carries the project id. Every edit in the window writes its
    payload to a companion key, and the timer reads that key when it fires, so
    the audit runs on the last edit of the burst. Before 2026-09-23 the timer
    closed over the *first* caller's ``current_jdf`` and later callers returned
    on the held lock: the audit ran on stale content and the final edit was
    never audited, while the local-timer path below always re-armed with the
    newest payload. The write happens before the lock attempt so a caller that
    loses the lock has already handed its payload to whoever holds it.
    """
    try:
        from prompt_matrix.services.redis_client import (
            acquire_lock,
            get_redis,
            redis_configured,
            release_lock,
        )
    except ImportError:
        from services.redis_client import acquire_lock, get_redis, redis_configured, release_lock
    if not redis_configured():
        return False
    import json

    client = get_redis()
    try:
        client.set(_payload_key(project_id), json.dumps(payload), ex=_PAYLOAD_TTL_S)
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning("[redhat] debounce payload not stored (non-fatal): %s", exc)
    lock_key = f"redhat-debounce:{project_id}"
    if not acquire_lock(lock_key, int(_DEBOUNCE_S) + 1):
        return True

    def _fire_latest() -> None:
        # Release first, then take the payload: an edit that lands after the
        # release re-arms its own timer, one that landed before is read here.
        # Either way nothing is audited twice and nothing is dropped.
        release_lock(lock_key)
        try:
            try:
                raw = client.getdel(_payload_key(project_id))
            except AttributeError:  # redis-py < 4 / a client without GETDEL
                raw = client.get(_payload_key(project_id))
                client.delete(_payload_key(project_id))
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning("[redhat] debounce payload unreadable, auditing the arming edit: %s", exc)
            latest = payload
        else:
            if raw is None:
                return  # a later timer already took the newest payload
            latest = json.loads(raw)
        _enqueue_multipass(
            str(latest.get("project_id") or project_id),
            latest.get("current_jdf") or {},
            latest.get("previous_jdf"),
            latest.get("run_id"),
        )

    timer = threading.Timer(_DEBOUNCE_S, _fire_latest)
    timer.daemon = True
    timer.start()
    return True


def schedule_redhat_multipass(
    project_id: str,
    current_jdf: dict[str, Any],
    previous_jdf: dict[str, Any] | None,
    *,
    run_id: str | None = None,
    debounce: bool = False,
) -> str | None:
    """Enqueue multi-pass audit; optionally debounce overlapping draft edits."""
    if debounce:
        payload = {
            "project_id": project_id,
            "current_jdf": current_jdf,
            "previous_jdf": previous_jdf,
            "run_id": run_id,
        }
        if _redis_debounce(project_id, payload):
            return None
        with _debounce_lock:
            existing = _debounce_timers.pop(project_id, None)
            if existing:
                existing.cancel()

            def _fire() -> None:
                with _debounce_lock:
                    _debounce_timers.pop(project_id, None)
                _enqueue_multipass(project_id, current_jdf, previous_jdf, run_id)

            timer = threading.Timer(_DEBOUNCE_S, _fire)
            _debounce_timers[project_id] = timer
            timer.daemon = True
            timer.start()
        return None
    return _enqueue_multipass(project_id, current_jdf, previous_jdf, run_id)


def _enqueue_multipass(
    project_id: str,
    current_jdf: dict[str, Any],
    previous_jdf: dict[str, Any] | None,
    run_id: str | None,
) -> str | None:
    """The un-debounced enqueue: bump the generation, retire the previous run,
    hand the payload to Celery. Both debounce timers end here, looked up on the
    module at fire time so a test can observe which payload reached the queue."""
    try:
        from prompt_matrix.celery_app import celery_broker_disabled
    except ImportError:
        from celery_app import celery_broker_disabled
    if celery_broker_disabled():
        return None

    try:
        from prompt_matrix.db.redhat_audit_lock_repository import bump_generation
        from prompt_matrix.tasks.redhat import run_redhat_multipass_task
    except ImportError:
        from db.redhat_audit_lock_repository import bump_generation
        from tasks.redhat import run_redhat_multipass_task

    generation, prev_task = bump_generation(project_id)
    _revoke_task(prev_task)
    try:
        async_result = run_redhat_multipass_task.delay(
            project_id,
            current_jdf,
            previous_jdf,
            run_id,
            generation,
        )
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning("[redhat] task scheduling failed (non-fatal): %s", exc)
        return None
    return str(async_result.id or "") or None


def _on_z3_verified(sender: Any, **kwargs: Any) -> None:
    project_id = str(kwargs.get("project_id") or "default")
    current_jdf = kwargs.get("current_jdf") or {}
    previous_jdf = kwargs.get("previous_jdf")
    run_id = kwargs.get("run_id")
    if not isinstance(current_jdf, dict):
        return
    schedule_redhat_multipass(
        project_id,
        current_jdf,
        previous_jdf if isinstance(previous_jdf, dict) else None,
        run_id=str(run_id) if run_id else None,
        debounce=False,
    )


def _on_draft_changed(sender: Any, **kwargs: Any) -> None:
    project_id = str(kwargs.get("project_id") or kwargs.get("workspace_id") or "default")
    current_jdf = kwargs.get("current_jdf") or kwargs.get("content") or {}
    previous_jdf = kwargs.get("previous_jdf")
    run_id = kwargs.get("run_id")
    if not isinstance(current_jdf, dict):
        return
    schedule_redhat_multipass(
        project_id,
        current_jdf,
        previous_jdf if isinstance(previous_jdf, dict) else None,
        run_id=str(run_id) if run_id else None,
        debounce=True,
    )


def connect_redhat_signals() -> None:
    """Idempotent hook registration for Flask app startup."""
    if getattr(connect_redhat_signals, "_connected", False):
        return
    z3_verified.connect(_on_z3_verified, weak=False)
    draft_changed.connect(_on_draft_changed, weak=False)
    connect_redhat_signals._connected = True  # type: ignore[attr-defined]
