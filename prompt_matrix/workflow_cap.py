"""Cap the full red-hat Send loop. SIGALRM on the main thread; a timer elsewhere."""

from __future__ import annotations

import os
import signal
import threading
from collections.abc import Iterator
from contextlib import contextmanager

DEFAULT_SECONDS = 120


class WorkflowTimeout(Exception):
    """Full workflow aborted by PEM_WORKFLOW_TIMEOUT or a cancellation token."""


def workflow_timeout_seconds() -> int:
    raw = os.getenv("PEM_WORKFLOW_TIMEOUT", str(DEFAULT_SECONDS))
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_SECONDS


class WorkflowCap:
    def __init__(self) -> None:
        self.cancelled = threading.Event()

    def check(self) -> None:
        if self.cancelled.is_set():
            raise WorkflowTimeout("Workflow aborted by user or system cap")


def timeout_handler(signum, frame) -> None:
    raise WorkflowTimeout("Workflow aborted by user or system cap")


@contextmanager
def workflow_deadline(seconds: int | None = None) -> Iterator[WorkflowCap]:
    cap = WorkflowCap()
    limit = workflow_timeout_seconds() if seconds is None else seconds
    old_handler = None
    used_alarm = False
    timer: threading.Timer | None = None
    try:
        # SIGALRM is process-wide: under gunicorn/gevent every greenlet reports
        # as the main thread and concurrent requests would clobber one alarm.
        # Only a bare CLI run (no server) may use it; servers use the timer.
        allow_alarm = os.environ.get("ASSURE_WORKFLOW_SIGALRM", "").strip().lower() in ("1", "true", "yes")
        if (
            limit > 0
            and allow_alarm
            and hasattr(signal, "SIGALRM")
            and threading.current_thread() is threading.main_thread()
        ):
            old_handler = signal.getsignal(signal.SIGALRM)
            signal.signal(signal.SIGALRM, timeout_handler)
            signal.alarm(limit)
            used_alarm = True
        elif limit > 0:
            timer = threading.Timer(limit, cap.cancelled.set)
            timer.daemon = True
            timer.start()
        yield cap
        cap.check()
    finally:
        if used_alarm:
            signal.alarm(0)
            if old_handler is not None:
                signal.signal(signal.SIGALRM, old_handler)
        if timer is not None:
            timer.cancel()
