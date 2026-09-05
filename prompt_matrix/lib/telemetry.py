"""System metrics collection and audit retention pruning."""

from __future__ import annotations

import sqlite3

try:
    from .logger import get_assure_logger, resolve_db_path
except ImportError:
    from logger import get_assure_logger, resolve_db_path


def collect_system_metrics(db_path: str | None = None) -> None:
    try:
        import psutil

        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        disk = psutil.disk_usage("/")
        path = db_path or resolve_db_path()
        conn = sqlite3.connect(path, timeout=5.0)
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.execute(
            """
            INSERT INTO system_metrics
            (cpu_percent, memory_used_mb, memory_total_mb, swap_used_mb,
             disk_used_percent, disk_free_gb)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                psutil.cpu_percent(interval=None),
                mem.used / 1024**2,
                mem.total / 1024**2,
                swap.used / 1024**2,
                disk.percent,
                disk.free / 1024**3,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        get_assure_logger().error(f"System metrics collection failed: {exc}")


def prune_old_logs(db_path: str | None = None) -> None:
    try:
        path = db_path or resolve_db_path()
        conn = sqlite3.connect(path, timeout=30.0)
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.execute("DELETE FROM audit_log WHERE created_at < datetime('now', '-90 days')")
        conn.execute("DELETE FROM system_metrics WHERE created_at < datetime('now', '-30 days')")
        conn.commit()
        conn.execute("VACUUM")
        conn.commit()
        conn.close()
    except Exception as exc:
        get_assure_logger().error(f"Log pruning failed: {exc}")
