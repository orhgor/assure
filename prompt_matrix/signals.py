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
        from prompt_matrix.celery_app import broker_supports_control, celery_app, celery_broker_disabled
    except ImportError:
        from celery_app import broker_supports_control, celery_app, celery_broker_disabled
    if celery_broker_disabled() or not broker_supports_control():
        return
    try:
        celery_app.control.revoke(task_id, terminate=True)
    except Exception:
        pass


def _redis_debounce(project_id: str, fire) -> bool:
    """Debounce across replicas: the first edit in a window arms one timer.

    A per-process ``threading.Timer`` debounces only the edits this replica
    saw; with N replicas each would fire its own audit. With Redis, one
    ``SET NX EX`` key per project gates the window, so exactly one replica
    arms the timer and the others return without scheduling. Returns False
    when Redis is not configured so the caller keeps the local behaviour.
    """
    try:
        from prompt_matrix.services.redis_client import acquire_lock, redis_configured
    except ImportError:
        from services.redis_client import acquire_lock, redis_configured
    if not redis_configured():
        return False
    if acquire_lock(f"redhat-debounce:{project_id}", int(_DEBOUNCE_S) + 1):
        timer = threading.Timer(_DEBOUNCE_S, fire)
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
        def _fire_shared() -> None:
            schedule_redhat_multipass(
                project_id, current_jdf, previous_jdf, run_id=run_id, debounce=False
            )

        if _redis_debounce(project_id, _fire_shared):
            return None
        with _debounce_lock:
            existing = _debounce_timers.pop(project_id, None)
            if existing:
                existing.cancel()

            def _fire() -> None:
                with _debounce_lock:
                    _debounce_timers.pop(project_id, None)
                schedule_redhat_multipass(
                    project_id,
                    current_jdf,
                    previous_jdf,
                    run_id=run_id,
                    debounce=False,
                )

            timer = threading.Timer(_DEBOUNCE_S, _fire)
            _debounce_timers[project_id] = timer
            timer.daemon = True
            timer.start()
        return None

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
