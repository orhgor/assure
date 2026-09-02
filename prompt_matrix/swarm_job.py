"""Background swarm job so Cursor MCP does not time out the full pipeline.

Cursor's tools/call deadline is far shorter than architect + developer + review +
red-hat + tester + documenter. swarm_start returns immediately. swarm_status is a
local read. The worker thread runs run_swarm().
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "logs" / "swarm_job.json"
_LOCK = threading.Lock()
_THREAD: threading.Thread | None = None
_JOB: dict[str, Any] | None = None


def _now() -> float:
    return time.time()


def _dump(job: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {key: value for key, value in job.items() if key != "_result"}
    STATE_PATH.write_text(json.dumps(payload, indent=2)[:120_000], encoding="utf-8")


def current_job() -> dict[str, Any] | None:
    with _LOCK:
        if _JOB is not None:
            return dict(_JOB)
    if STATE_PATH.is_file():
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if isinstance(data, dict) and data.get("id"):
            return data
    return None


def format_job(job: dict[str, Any] | None) -> str:
    if not job:
        return (
            "# swarm_status\n"
            "status: idle\n"
            "Call swarm_start with the task and context_files. "
            "Do not call swarm_develop and wait for the full pipeline — Cursor will time out.\n"
        )
    status = str(job.get("status") or "unknown")
    lines = [
        "# swarm_status",
        f"status: {status}",
        f"run_id: {job.get('id') or '(none)'}",
        f"phase: {job.get('phase') or '(none)'}",
        f"task: {str(job.get('task') or '')[:200]}",
    ]
    if job.get("error"):
        lines.append(f"error: {job['error']}")
    if job.get("applied") is not None:
        lines.append(f"applied: {job['applied']}")
    if job.get("patch"):
        lines.append(f"patch: {job['patch']}")
    if job.get("verdict"):
        lines.append(f"verdict: {job['verdict']}")
    elapsed = job.get("started_at")
    if isinstance(elapsed, (int, float)) and elapsed:
        lines.append(f"elapsed_s: {int(max(0, _now() - float(elapsed)))}")
    if status in {"queued", "running"}:
        lines.append(
            "Call swarm_status again until status is done or error. "
            "Do not start a second swarm."
        )
    report = job.get("report") or ""
    if report and status in {"done", "error"}:
        lines.extend(["", "===== QUALITY REPORT =====", str(report).rstrip()])
    return "\n".join(lines).rstrip() + "\n"


def start_job(kwargs: dict[str, Any], *, edition: str | None = None) -> dict[str, Any]:
    global _JOB, _THREAD
    with _LOCK:
        live = _JOB
        if live is not None and live.get("status") in {"queued", "running"}:
            raise RuntimeError(
                "A swarm is already running. Call swarm_status. "
                f"run_id={live.get('id')} phase={live.get('phase')}"
            )
        job = {
            "id": uuid.uuid4().hex[:12],
            "status": "queued",
            "phase": "queued",
            "task": kwargs.get("task") or "",
            "error": "",
            "applied": None,
            "patch": "",
            "verdict": "",
            "report": "",
            "started_at": _now(),
            "edition": edition or "",
        }
        _JOB = job
        _dump(job)

    thread = threading.Thread(
        target=_run,
        args=(job, kwargs, edition),
        name="pem-swarm",
        daemon=True,
    )
    _THREAD = thread
    thread.start()
    return dict(job)


def _run(job: dict[str, Any], kwargs: dict[str, Any], edition: str | None) -> None:
    from .swarm import run_swarm, set_progress_hook

    previous = os.environ.get("ASSURE_EDITION")
    token = set_progress_hook(lambda message: _set_phase(job, message))
    try:
        if edition:
            os.environ["ASSURE_EDITION"] = edition
        _set_status(job, "running", phase="starting")
        result = run_swarm(**kwargs)
        report = getattr(result, "quality_report", None) or getattr(result, "summary", None) or ""
        with _LOCK:
            job["status"] = "done"
            job["phase"] = "done"
            job["report"] = report
            job["verdict"] = getattr(result, "review_verdict", None) or ""
            job["patch"] = getattr(result, "patch_path", None) or ""
            job["applied"] = bool(getattr(result, "applied", False))
            job["error"] = ""
            _dump(job)
    except Exception as exc:
        with _LOCK:
            job["status"] = "error"
            job["phase"] = "error"
            job["error"] = str(exc)
            _dump(job)
    finally:
        from .swarm import reset_progress_hook

        reset_progress_hook(token)
        if edition:
            if previous is None:
                os.environ.pop("ASSURE_EDITION", None)
            else:
                os.environ["ASSURE_EDITION"] = previous


def _set_status(job: dict[str, Any], status: str, *, phase: str | None = None) -> None:
    with _LOCK:
        job["status"] = status
        if phase is not None:
            job["phase"] = phase
        _dump(job)


def _set_phase(job: dict[str, Any], message: str) -> None:
    with _LOCK:
        job["phase"] = message
        if job.get("status") == "queued":
            job["status"] = "running"
        _dump(job)
