"""Native operator entry — run Assure without Docker.

Usage (local):
  python -m prompt_matrix.app
  ASSURE_ENV=staging python -m prompt_matrix.app

Usage (EC2 systemd — WorkingDirectory = repo root or prompt_matrix/):
  python prompt_matrix/app.py --host 127.0.0.1 --port 8765 --no-browser
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PKG_ROOT = Path(__file__).resolve().parent


def _ensure_import_path() -> None:
    for path in (_REPO_ROOT, _PKG_ROOT):
        entry = str(path)
        if entry not in sys.path:
            sys.path.insert(0, entry)


def _default_daemon_argv() -> list[str]:
    host = os.environ.get("ASSURE_HOST", "127.0.0.1")
    port = os.environ.get("PORT", "8765")
    return ["--host", host, "--port", port, "--no-browser"]


def main(argv: list[str] | None = None) -> int:
    _ensure_import_path()
    os.environ.setdefault("ASSURE_QUIET_START", "1")

    cli_argv = list(argv if argv is not None else sys.argv[1:])
    if not cli_argv and (
        os.environ.get("ASSURE_DAEMON", "").strip().lower() in ("1", "true", "yes")
        or not sys.stdout.isatty()
    ):
        cli_argv = _default_daemon_argv()

    from prompt_matrix.web import serve

    return serve(cli_argv)


if __name__ == "__main__":
    raise SystemExit(main())
