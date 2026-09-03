"""Production health diagnostics (SQLite, disk, backup freshness)."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify

try:
    from ..lib.logger import resolve_db_path
except ImportError:
    from lib.logger import resolve_db_path

health_bp = Blueprint("health", __name__)


def _data_dir() -> Path:
    return Path(resolve_db_path()).parent


def _parse_created_at(raw: str) -> datetime:
    return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).replace(tzinfo=None)


@health_bp.route("/health", methods=["GET"])
def health_check():
    status: dict = {"ok": True, "status": "healthy", "checks": {}}
    db_path = resolve_db_path()

    try:
        conn = sqlite3.connect(db_path, timeout=5.0)
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.execute("SELECT 1")
        conn.close()
        status["checks"]["sqlite"] = "ok"
    except Exception as exc:
        status["ok"] = False
        status["status"] = "unhealthy"
        status["checks"]["sqlite"] = f"error: {exc}"

    try:
        data_dir = _data_dir()
        if data_dir.exists():
            statvfs = os.statvfs(str(data_dir))
            free_gb = (statvfs.f_frsize * statvfs.f_bavail) / (1024**3)
        else:
            import shutil

            usage = shutil.disk_usage(str(data_dir.parent if data_dir.parent.exists() else "/"))
            free_gb = usage.free / (1024**3)
        status["checks"]["disk_free_gb"] = round(free_gb, 2)
        if free_gb < 1.0:
            status["ok"] = False
            status["status"] = "unhealthy"
            status["checks"]["disk"] = "critical (low space)"
        else:
            status["checks"]["disk"] = "ok"
    except Exception as exc:
        status["ok"] = False
        status["status"] = "unhealthy"
        status["checks"]["disk"] = f"error: {exc}"

    try:
        conn = sqlite3.connect(db_path, timeout=5.0)
        row = conn.execute(
            """
            SELECT created_at FROM audit_log
            WHERE action='BACKUP' AND success=1
            ORDER BY created_at DESC LIMIT 1
            """
        ).fetchone()
        metrics = conn.execute(
            """
            SELECT memory_used_mb, memory_total_mb, cpu_percent
            FROM system_metrics
            ORDER BY created_at DESC LIMIT 1
            """
        ).fetchone()
        conn.close()
        if row:
            last_backup = _parse_created_at(row[0])
            hours_since = round((datetime.now() - last_backup).total_seconds() / 3600, 2)
            backup_status = "ok" if hours_since <= 24 else "stale"
            status["checks"]["backup"] = {
                "status": backup_status,
                "hours_since_last": hours_since,
                "last_at": last_backup.isoformat(),
            }
            if backup_status != "ok":
                status["checks"]["backup_warning"] = "stale (>24h)"
        else:
            status["checks"]["backup"] = {"status": "never_run", "hours_since_last": None}
        if metrics:
            status["checks"]["memory"] = {
                "used_mb": round(float(metrics[0]), 1),
                "total_mb": round(float(metrics[1]), 1),
                "cpu_percent": round(float(metrics[2]), 1),
            }
    except Exception as exc:
        status["checks"]["backup"] = {"status": "error", "error": str(exc)}

    try:
        with open("/proc/uptime", "r", encoding="utf-8") as handle:
            uptime_seconds = float(handle.read().split()[0])
        status["checks"]["uptime_seconds"] = int(uptime_seconds)
    except OSError:
        pass

    if not status["ok"]:
        status["status"] = "unhealthy"

    code = 200 if status["ok"] else 503
    return jsonify(status), code


def register_health_routes(app) -> None:
    app.register_blueprint(health_bp)
