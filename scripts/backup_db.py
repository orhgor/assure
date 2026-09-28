#!/usr/bin/env python3
"""Nightly PostgreSQL backup to the object store (2026-09-28).

Why: `/health` reported `backup: never_run` on every deployment — the health
check reads the audit trail for a successful BACKUP action and nothing ever
wrote one. A customer's documents, reports and accounts live in this
database; the box has an S3 bucket (or the local object store) already.

    python scripts/backup_db.py            # one backup now
    python scripts/backup_db.py --loop     # every BACKUP_INTERVAL_S (default 86400)

Writes ``pg_dump --format=custom`` (compressed, restorable with pg_restore) to
``backups/postgres/<UTC timestamp>.dump`` in the object store configured by
ASSURE_S3_BUCKET / ASSURE_DATA_DIR, records the run in ``audit_log``
(action BACKUP, success true/false) so `/health` can say when the last one
was, and prunes local dumps older than BACKUP_KEEP_DAYS (S3 retention is the
bucket's lifecycle rule — this script never lists or deletes in S3). Never
raises out of the loop: a failed run is an audit row with the error and a log
line, and the next run happens on schedule.
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("assure.backup")


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn.startswith("postgresql"):
        raise RuntimeError("DATABASE_URL must be a PostgreSQL DSN")
    return dsn


def run_backup() -> dict:
    started = time.monotonic()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    key = f"backups/postgres/{stamp}.dump"
    request_id = f"backup-{uuid.uuid4().hex[:12]}"
    from prompt_matrix.lib.logger import get_audit_logger
    from prompt_matrix.services.object_store import get_object_store

    audit = get_audit_logger()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "db.dump"
            cmd = ["pg_dump", "--format=custom", "--no-owner", "--no-privileges", "--compress=6", f"--file={out}", _dsn()]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=int(os.environ.get("BACKUP_TIMEOUT_S", "3600")))
            if proc.returncode != 0:
                raise RuntimeError(f"pg_dump exited {proc.returncode}: {proc.stderr.strip()[:400]}")
            data = out.read_bytes()
        store = get_object_store()
        uri = store.put_bytes(key, data, content_type="application/octet-stream")
        duration = int((time.monotonic() - started) * 1000)
        audit.log_audit(request_id, None, "BACKUP", success=True, duration_ms=duration,
                        details={"key": key, "bytes": len(data), "store": type(store).__name__, "uri": uri})
        pruned = prune_local(store)
        log.info("backup ok: %s (%d bytes, %d ms, pruned %d)", uri, len(data), duration, pruned)
        return {"ok": True, "key": key, "bytes": len(data), "duration_ms": duration, "pruned": pruned}
    except Exception as exc:  # noqa: BLE001 — recorded, never raised out of the loop
        duration = int((time.monotonic() - started) * 1000)
        audit.log_audit(request_id, None, "BACKUP", success=False, duration_ms=duration,
                        error_type=type(exc).__name__, error_message=str(exc)[:400], details={"key": key})
        log.error("backup failed: %s", exc)
        return {"ok": False, "error": str(exc), "key": key}


def prune_local(store) -> int:
    """Delete local dumps older than BACKUP_KEEP_DAYS; nothing in S3 is touched."""
    root = getattr(store, "root", None)
    if root is None:
        return 0
    keep_days = int(os.environ.get("BACKUP_KEEP_DAYS", "14"))
    cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
    folder = Path(root) / "backups" / "postgres"
    if not folder.exists():
        return 0
    pruned = 0
    for f in folder.glob("*.dump"):
        try:
            ts = datetime.strptime(f.stem, "%Y-%m-%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if ts < cutoff:
            f.unlink(missing_ok=True)
            pruned += 1
    return pruned


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true", help="run forever, every BACKUP_INTERVAL_S seconds (default 86400)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    interval = int(os.environ.get("BACKUP_INTERVAL_S", "86400"))
    while True:
        result = run_backup()
        if not args.loop:
            return 0 if result.get("ok") else 1
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())
