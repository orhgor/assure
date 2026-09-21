"""2A — the post-migration pin: served asset hashes and the demo document's state.

Prints the md5 of what the static service actually serves (not the working tree),
the full /health body from the app, and the demo project's document gate, so the
verification can be pinned to bytes rather than a path that peers are editing.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
import urllib.request
from pathlib import Path

APP = "http://127.0.0.1:8890"
STATIC = "http://127.0.0.1:8891"
DB = "/home/ubuntu/assure-prototype/prompt_matrix/history.sqlite"
DEMO = "demo-3235f5"


def key() -> str:
    raw = Path("/etc/assure/shell-access.env").read_text()
    m = re.search(r"SHELL_ACCESS_KEY\s*=\s*[\"']?([^\"'\n]+)", raw)
    return m.group(1).strip() if m else ""


def get(path: str, base: str = STATIC, gated: bool = True):
    h = {"User-Agent": "curl/8.7.1"}
    if gated:
        h["X-Shell-Key"] = key()
    r = urllib.request.Request(base + path, headers=h)
    with urllib.request.urlopen(r, timeout=60) as resp:
        return resp.status, resp.read()


def main() -> int:
    st, body = get("/health", base=APP, gated=False)
    print(f"=== /health (app 8890) http {st} ===")
    try:
        print(json.dumps(json.loads(body), indent=2)[:1400])
    except Exception:
        print(body[:600].decode("utf-8", "replace"))

    print("\n=== served shell assets (bytes, not the working tree) ===")
    for path in ("/shell.js", "/index.html", "/shell.css"):
        try:
            st, blob = get(path)
            print(f"  {path:<14} http {st} bytes {len(blob):<8} md5 {hashlib.md5(blob).hexdigest()}")
        except Exception as exc:
            print(f"  {path:<14} ERR {type(exc).__name__}: {exc}")

    print("\n=== demo project document ===")
    try:
        st, body = get(f"/api/projects/{DEMO}/files")
        doc = json.loads(body)
        for k, v in (doc or {}).items():
            s = json.dumps(v) if not isinstance(v, str) else v
            print(f"  {k}: {s[:160]}")
    except Exception as exc:
        print(f"  ERR {type(exc).__name__}: {exc}")

    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    p = c.execute("select id,title,current_version,updated_at,length(last_compiled_json) lc "
                  "from projects where id=?", (DEMO,)).fetchone()
    print("  projects row:", dict(p) if p else None)
    for t, col in (("jdf_documents", "project_id"), ("jdf_revisions", "project_id"),
                   ("node_revisions", "project_id"), ("substrate_vault", "project_id"),
                   ("audit_log", "project_id")):
        n = c.execute(f"select count(*) from {t} where {col}=?", (DEMO,)).fetchone()[0]
        print(f"  {t:<20} rows={n}")
    c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
