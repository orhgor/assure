#!/usr/bin/env python3
"""Ephemeral Flask server for Playwright JS tests (golden path, etc.)."""

from __future__ import annotations

import argparse
import os
import sqlite3
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

    tmp = tempfile.TemporaryDirectory()
    db_path = Path(tmp.name) / "playwright.sqlite"

    def _getter():
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    class _StatVfs:
        f_frsize = 4096
        f_bavail = 2_000_000

    patches = [
        patch("prompt_matrix.history.DB_PATH", db_path),
        patch("prompt_matrix.history._resolve_db_path", return_value=db_path),
        patch("prompt_matrix.db.pool._resolve_db_path", return_value=db_path),
        patch("prompt_matrix.lib.logger.DB_PATH", db_path),
        patch("prompt_matrix.lib.logger.resolve_db_path", return_value=str(db_path)),
        patch("prompt_matrix.history.get_db", side_effect=_getter),
        patch("prompt_matrix.db.connection.get_db", side_effect=_getter),
        patch("prompt_matrix.db.jdf_repository.get_db", side_effect=_getter),
        patch("prompt_matrix.db.feedback_repository.get_db", side_effect=_getter),
        patch("prompt_matrix.routers.health.os.statvfs", return_value=_StatVfs()),
        patch.dict(
            os.environ,
            {
                "CLERK_PUBLISHABLE_KEY": "",
                "CLERK_SECRET_KEY": "",
                "SQLITE_USE_POOL": "0",
                "ASSURE_REQUIRE_LOGIN": "false",
            },
            clear=False,
        ),
    ]
    for item in patches:
        item.start()

    from prompt_matrix.db.pool import reset_engine_for_tests

    reset_engine_for_tests()

    conn = _getter()
    from prompt_matrix.db.connection import init_db
    from prompt_matrix.web import create_app

    init_db(conn)
    app = create_app(require_auth=False)
    server = make_server(args.host, args.port, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="playwright-dev")
    thread.start()
    print(f"Playwright dev server listening on http://{args.host}:{args.port}", flush=True)

    try:
        thread.join()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
