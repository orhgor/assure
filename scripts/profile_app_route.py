#!/usr/bin/env python3
"""Time each step on the /app request path (run on staging box)."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "prompt_matrix")]

for env_file in (ROOT / ".env.staging", ROOT / "prompt_matrix" / ".env", ROOT / ".env"):
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))
        break

_t0 = time.perf_counter()


def tick(label: str) -> None:
    print(f"[timing] {label}: {time.perf_counter() - _t0:.3f}s", flush=True)


def main() -> int:
    tick("start")
    from prompt_matrix.web import create_app

    tick("import web")
    app = create_app(require_auth=False)
    tick("create_app")

    with app.test_request_context("/app"):
        tick("request context")
        from flask import request

        try:
            from prompt_matrix.db.jdf_repository import (
                DEFAULT_PROJECT_ID,
                fetch_latest_jdf_or_empty,
            )
        except ImportError:
            from db.jdf_repository import DEFAULT_PROJECT_ID, fetch_latest_jdf_or_empty

        project_id = (request.args.get("project") or "").strip() or DEFAULT_PROJECT_ID
        tick("parsed project")
        fetch_latest_jdf_or_empty(project_id)
        tick("fetch_latest_jdf_or_empty")

        try:
            from prompt_matrix.editions import snapshot as edition_snapshot
        except ImportError:
            from editions import snapshot as edition_snapshot

        edition_snapshot()
        tick("edition_snapshot")

        try:
            from prompt_matrix.i18n import catalog as string_catalog
        except ImportError:
            from i18n import catalog as string_catalog

        string_catalog("en")
        tick("string_catalog")

        from prompt_matrix.cloud_auth import template_state

        template_state(include_pk=True)
        tick("template_state")

        from flask import render_template

        render_template(
            "index.html",
            locale="en",
            initial_jdf=None,
            project_id=project_id,
            initial_pane="compose",
            include_pk=True,
        )
        tick("render_template (minimal ctx)")

    with app.test_client() as client:
        tick("test_client ready")
        t_req = time.perf_counter()
        resp = client.get("/app")
        elapsed = time.perf_counter() - t_req
        print(f"[timing] GET /app: {elapsed:.3f}s status={resp.status_code}", flush=True)

    tick("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
