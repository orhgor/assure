"""Production health diagnostics (database, disk, backup freshness, optional OMP)."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from flask import Blueprint, jsonify

try:
    from ..db.connection import closing_connection
    from ..lib.logger import resolve_db_path
except ImportError:
    from db.connection import closing_connection
    from lib.logger import resolve_db_path

health_bp = Blueprint("health", __name__)


@lru_cache(maxsize=1)
def _deployed_commit() -> str:
    """Commit the running checkout is on.

    Used when ASSURE_BUILD_SHA is unset — the systemd staging app, where the
    docker/GHCR deploy path that exports that var never runs. Without it the
    deploy postflight gate sees no build_sha and degrades to a 200-only check,
    so a stale process serving old code cannot be detected. Cached: /health is
    polled and `git` is not cheap.
    """
    repo_root = Path(__file__).resolve().parents[2]
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except Exception:
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def _data_dir() -> Path:
    return Path(resolve_db_path()).parent


def _parse_created_at(raw: str) -> datetime:
    return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).replace(tzinfo=None)


def _local_models_check(timeout_s: float = 1.5) -> dict:
    """Are the configured local models actually on the Ollama daemon?

    Only meaningful with ``ASSURE_LLM_BACKEND=ollama`` (every compose target
    since 2026-09-25); the cloud backends have no local inventory. Reads
    ``GET <OLLAMA_API_BASE>/api/tags`` — the same list ``ollama list`` prints —
    and compares it with ``cost_governance.local_model`` for both roles. A
    ``:latest`` suffix on the daemon side is treated as equal to a bare tag.
    """
    try:
        from ..cost_governance import llm_backend, local_model
    except ImportError:
        from cost_governance import llm_backend, local_model
    backend = llm_backend()
    if backend == "bedrock":
        try:
            from ..cost_governance import bedrock_model
        except ImportError:
            from cost_governance import bedrock_model
        return {
            "backend": "bedrock",
            "status": "not local",
            "draft": bedrock_model("draft").split("/", 1)[-1],
            "analysis": bedrock_model("analysis").split("/", 1)[-1],
            "compare_b": bedrock_model("b").split("/", 1)[-1],
        }
    if backend != "ollama":
        return {"backend": backend or "cloud", "status": "not local"}
    base = (os.environ.get("OLLAMA_API_BASE") or os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434").rstrip("/")
    wanted = []
    for role in ("a", "b"):
        name = local_model(role).split("/", 1)[-1]
        if name not in wanted:
            wanted.append(name)
    try:
        import json as _json
        import urllib.request

        with urllib.request.urlopen(f"{base}/api/tags", timeout=timeout_s) as resp:  # noqa: S310 — fixed local URL
            payload = _json.loads(resp.read().decode("utf-8") or "{}")
    except Exception as exc:
        return {"backend": "ollama", "api_base": base, "status": "unreachable", "error": exc.__class__.__name__, "wanted": wanted}
    have = set()
    for m in payload.get("models") or []:
        name = str(m.get("name") or m.get("model") or "")
        if name:
            have.add(name)
            if name.endswith(":latest"):
                have.add(name[: -len(":latest")])
    present = [w for w in wanted if w in have or f"{w}:latest" in have]
    missing = [w for w in wanted if w not in present]
    return {
        "backend": "ollama",
        "api_base": base,
        "status": "ok" if not missing else "missing",
        "present": present,
        "missing": missing,
    }


@health_bp.route("/health", methods=["GET"])
def health_check():
    status: dict = {"ok": True, "status": "healthy", "checks": {}}
    db_path = resolve_db_path()

    try:
        # A probe of a database that may be exactly the thing that is broken: the
        # connection is closed on the failure path too. It did not used to be, and
        # /health is polled — so a failing probe leaked one connection per poll,
        # each holding whatever read transaction its last statement left open.
        with closing_connection(db_path, site="routers.health.db_probe") as conn:
            conn.execute("SELECT 1")
        # The probe runs against PostgreSQL (db/pg_compat); it was reported as
        # ``sqlite`` from the SQLite days. ``db`` is the key now; ``sqlite`` is
        # kept as an alias for one release because scripts/aws/_2a_reconfirm.py
        # and docs/post-launch-ops.md still read ``.checks.sqlite``.
        status["checks"]["db"] = "ok"
        status["checks"]["sqlite"] = status["checks"]["db"]
        try:
            from ..db.pg_compat import is_postgres
        except ImportError:
            from db.pg_compat import is_postgres
        status["checks"]["database"] = "postgresql" if is_postgres() else "sqlite"
    except Exception as exc:
        status["ok"] = False
        status["status"] = "unhealthy"
        status["checks"]["db"] = f"error: {exc}"
        status["checks"]["sqlite"] = status["checks"]["db"]

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
        with closing_connection(db_path, site="routers.health.backup_probe") as conn:
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

    try:
        from ..ui_cache import APP_CSS, APP_JS
    except ImportError:
        from ui_cache import APP_CSS, APP_JS
    status["ui"] = {
        "css_version": APP_CSS,
        "js_version": APP_JS,
        "jdf_workbench": True,
    }
    try:
        from ..llm.orchestrator import orchestrator_model_pairs, use_free_models
    except ImportError:
        from llm.orchestrator import orchestrator_model_pairs, use_free_models
    status["use_free_models"] = use_free_models()
    try:
        from ..llm.orchestrator import get_active_model_stack
    except ImportError:
        from llm.orchestrator import get_active_model_stack
    status["stack"] = get_active_model_stack()
    if use_free_models():
        status["orchestrator_models"] = {
            key: pair.get("litellm_model") or "" for key, pair in orchestrator_model_pairs().items()
        }
    try:
        from ..upload_limits import limits_snapshot
    except ImportError:
        from upload_limits import limits_snapshot
    status["limits"] = limits_snapshot()
    try:
        from ..omp_client import omp_configured, omp_health
    except ImportError:
        from omp_client import omp_configured, omp_health
    # OMP is optional, so it never fails the probe (503) — but a configured OMP
    # that is down is not "healthy" either. Reported as degraded: 200, so the
    # load balancer keeps the replica, and ``status`` says what is wrong.
    # Unconfigured deployments used to report ``omp: "down"`` for a server that
    # was never meant to exist.
    if omp_configured():
        omp = omp_health()
        omp_state = str(omp.get("status") or "down")
        status["checks"]["omp"] = omp_state
        if omp.get("version"):
            status["checks"]["omp_version"] = omp["version"]
        if omp_state not in ("ok", "healthy", "up"):
            status["degraded"] = True
            status["checks"]["omp_error"] = str(omp.get("error") or "unreachable").splitlines()[0][:200]
            if status["ok"]:
                status["status"] = "degraded"
    else:
        status["checks"]["omp"] = "not configured"
    try:
        from ..services.textract_budget import usage as _textract_usage
    except ImportError:
        from services.textract_budget import usage as _textract_usage
    try:
        status["checks"]["textract_budget"] = _textract_usage()
    except Exception as exc:  # the cap is enforced at call time; here it is only reported
        status["checks"]["textract_budget"] = {"error": exc.__class__.__name__}
    models = _local_models_check()
    status["checks"]["models"] = models
    if models.get("status") in ("missing", "unreachable"):
        # The box was asked to run without provider keys (2026-09-25): a compile
        # with the model absent fails at the first token, so the daemon being up
        # is not enough — the configured model files must be present.
        status["degraded"] = True
        if status["status"] == "healthy":
            status["status"] = "degraded"
    status.setdefault("degraded", False)
    build_sha = (os.environ.get("ASSURE_BUILD_SHA") or "").strip() or _deployed_commit()
    if build_sha:
        status["build_sha"] = build_sha

    code = 200 if status["ok"] else 503
    return jsonify(status), code


@health_bp.route("/ready", methods=["GET"])
def readiness():
    """Readiness for the load balancer: this replica can serve a request.

    Only the dependencies a request needs — the database and, when configured,
    Redis. Nothing informational (OMP, git, backups, disk) belongs here: a
    replica must not be pulled from rotation for a condition that does not
    stop it serving.
    """
    checks: dict[str, str] = {}
    ok = True
    try:
        with closing_connection(resolve_db_path(), site="routers.health.readiness") as conn:
            conn.execute("SELECT 1")
        checks["database"] = "ok"
    except Exception as exc:
        ok = False
        checks["database"] = f"error: {exc.__class__.__name__}"
    try:
        from ..services.redis_client import ping, redis_configured
    except ImportError:
        from services.redis_client import ping, redis_configured
    if redis_configured():
        alive = ping()
        checks["redis"] = "ok" if alive else "error: unreachable"
        ok = ok and alive
    else:
        checks["redis"] = "not configured"
    return jsonify({"ok": ok, "checks": checks}), (200 if ok else 503)


def register_health_routes(app) -> None:
    app.register_blueprint(health_bp)
