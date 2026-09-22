#!/usr/bin/env python3
"""Ephemeral Flask server for the Playwright JS suite (playwright.config.js).

PostgreSQL-only, like the app: the server gets its own schema, derived from a
temporary ``DATABASE_PATH`` (``db/pg_compat.current_schema`` with
``ASSURE_PG_SCHEMA_FROM_DB_PATH=1`` — the mechanism tests/conftest.py uses), so a
run never touches the development schema and is dropped on exit. The previous
version opened a temporary SQLite file through ``sqlite3.connect``; it stopped
working when the backend became PostgreSQL-only (2026-09-22).

    DATABASE_URL=postgresql://assure:assure@localhost:5432/assure \\
    .venv/bin/python scripts/playwright_dev_server.py --port 8801
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

from werkzeug.serving import make_server


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=int(os.environ.get("PLAYWRIGHT_PORT", "8801")))
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    if not (os.environ.get("DATABASE_URL") or "").startswith("postgres"):
        os.environ["DATABASE_URL"] = "postgresql://assure:assure@localhost:5432/assure"
        print(
            "DATABASE_URL not set; using the docker-compose.dev.yml default "
            f"{os.environ['DATABASE_URL']}",
            file=sys.stderr,
            flush=True,
        )

    tmp = tempfile.TemporaryDirectory()
    db_path = Path(tmp.name) / "playwright.sqlite"  # a schema name seed, not a file
    os.environ.update(
        {
            "DATABASE_PATH": str(db_path),
            "ASSURE_PG_SCHEMA_FROM_DB_PATH": "1",
            "ASSURE_DATA_DIR": tmp.name,
            "CLERK_PUBLISHABLE_KEY": "",
            "CLERK_SECRET_KEY": "",
            "ASSURE_REQUIRE_LOGIN": "false",
            "ASSURE_CLERK_ONLY": "0",
            "WTF_CSRF_ENABLED": "0",
            "PARSE_ASYNC": "0",
            "SUBSTRATE_ASYNC_UPLOAD": "0",
            "ENVIRONMENT": os.environ.get("ENVIRONMENT") or "development",
        }
    )
    os.environ.setdefault("PEM_SECRET_KEY", "playwright-dev-server-secret")

    class _StatVfs:
        f_frsize = 4096
        f_bavail = 2_000_000

    statvfs_patch = patch("prompt_matrix.routers.health.os.statvfs", return_value=_StatVfs())
    statvfs_patch.start()

    import prompt_matrix.history as history_mod
    from prompt_matrix.db.connection import init_db
    from prompt_matrix.db.pg_compat import drop_derived_schemas
    from prompt_matrix.web import create_app

    history_mod.DB_PATH = history_mod._resolve_db_path()
    init_db()
    app = create_app(require_auth=False)
    server = make_server(args.host, args.port, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="playwright-dev")
    thread.start()
    print(f"Playwright dev server listening on http://{args.host}:{args.port}", flush=True)

    try:
        thread.join()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        try:
            drop_derived_schemas()
        except Exception as exc:  # best effort: the schema is disposable
            print(f"schema cleanup skipped: {exc}", file=sys.stderr)
        statvfs_patch.stop()
        tmp.cleanup()


if __name__ == "__main__":
    main()
