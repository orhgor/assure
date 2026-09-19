"""System metrics collection and audit retention pruning."""

from __future__ import annotations

try:
    from ..db.connection import closing_connection
    from .logger import get_assure_logger, resolve_db_path
except ImportError:
    from db.connection import closing_connection
    from logger import get_assure_logger, resolve_db_path


def collect_system_metrics(db_path: str | None = None) -> None:
    try:
        import psutil

        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        disk = psutil.disk_usage("/")
        path = db_path or resolve_db_path()
        # The insert is best-effort: a sample that cannot be written must not break
        # the caller. The connection is not best-effort — when the insert or its
        # commit raised, this used to leave the connection open with the statement
        # uncommitted, holding SQLite's write lock for the next writer to wait on.
        with closing_connection(
            path, site="lib.telemetry.collect_system_metrics"
        ) as conn:
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
    except Exception as exc:
        get_assure_logger().error(f"System metrics collection failed: {exc}")


def prune_old_logs(db_path: str | None = None) -> None:
    try:
        path = db_path or resolve_db_path()
        with closing_connection(path, timeout=30.0, site="lib.telemetry.prune_old_logs") as conn:
            conn.execute("DELETE FROM audit_log WHERE created_at < datetime('now', '-90 days')")
            conn.execute(
                "DELETE FROM system_metrics WHERE created_at < datetime('now', '-30 days')"
            )
            conn.commit()
            conn.execute("VACUUM")
            conn.commit()
    except Exception as exc:
        get_assure_logger().error(f"Log pruning failed: {exc}")
