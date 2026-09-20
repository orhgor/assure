"""A2 — remove the two stray `demo` projects, keep demo-3235f5.

Dry-run by default; pass `--apply` to delete. Deletes the `projects` row through
the shell proxy (the switcher reads GET /api/projects), then removes the rows
the missing FK cascade leaves behind (SQLite runs with foreign_keys OFF — no
ON DELETE CASCADE fires) and the project directory.
"""
import json
import os
import re
import shutil
import sqlite3
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8891")
DB = "prompt_matrix/history.sqlite"
KEEP = "demo-3235f5"
DROP = ["demo-a6fb19", "demo-14e7ed"]
ORPHAN_TABLES = [
    "jdf_revisions", "substrate_vault", "substrates", "jdf_documents", "node_revisions",
    "audit_log", "source_conflicts", "project_budgets", "token_ledger_entries",
    "project_comments", "daily_compile_limits", "workspace_settings", "document_locks",
    "sign_offs", "feedback", "runs", "drafts", "executions", "redhat_findings",
    "pipeline_cache", "jdf_cli_documents",
]


def gate_key():
    raw = open("/etc/assure/shell-access.env").read()
    m = re.search(r"SHELL_ACCESS_KEY\s*=\s*[\"']?([^\"'\n]+)", raw)
    return m.group(1).strip() if m else ""


def req(method, path):
    r = urllib.request.Request(BASE + path, method=method, headers={"X-Shell-Key": gate_key()})
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def demo_rows():
    st, body = req("GET", "/api/projects")
    projects = (json.loads(body) or {}).get("projects") or []
    return st, [p for p in projects if (p.get("title") or "") == "demo"]


apply = "--apply" in sys.argv

st, demos = demo_rows()
print("GET /api/projects -> %s" % st)
print("projects titled 'demo': %s" % [(p["id"], p.get("source_count"), p.get("current_version")) or p for p in demos])
print("id does not exist in GET list" if False else "")

if not apply:
    print("\n[DRY RUN] would DELETE: %s (keeping %s)" % (DROP, KEEP))
    sys.exit(0)

for pid in DROP:
    st, body = req("DELETE", "/api/projects/" + pid)
    print("DELETE /api/projects/%s -> %s %s" % (pid, st, body[:200].strip()))

c = sqlite3.connect(DB)
print("\n== orphan cleanup ==")
for pid in DROP:
    for table in ORPHAN_TABLES:
        try:
            cols = {r[1] for r in c.execute("PRAGMA table_info(%s)" % table)}
        except sqlite3.OperationalError:
            continue
        keycol = "project_id" if "project_id" in cols else ("workspace_id" if "workspace_id" in cols else None)
        if not keycol:
            continue
        try:
            cur = c.execute("DELETE FROM %s WHERE %s = ?" % (table, keycol), (pid,))
            if cur.rowcount:
                print("  %-24s %-14s removed %d" % (table, keycol, cur.rowcount))
        except sqlite3.OperationalError as exc:
            print("  %-24s ERR %s" % (table, exc))
c.commit()
print("  projects rows removed for dropped demos: %s" % [
    c.execute("DELETE FROM projects WHERE id = ?", (pid,)).rowcount for pid in DROP])
c.commit()

for pid in DROP:
    d = os.path.join("prompt_matrix", "projects", pid)
    if os.path.isdir(d):
        shutil.rmtree(d)
        print("  removed dir %s" % d)

st, demos = demo_rows()
print("\nGET /api/projects -> %s" % st)
print("projects titled 'demo' now: %s" % [p["id"] for p in demos])
for pid in DROP:
    n = c.execute("SELECT count(*) FROM jdf_revisions WHERE project_id = ?", (pid,)).fetchone()[0]
    s = c.execute("SELECT count(*) FROM substrate_vault WHERE project_id = ?", (pid,)).fetchone()[0]
    print("  %s residual: jdf_revisions=%d substrate_vault=%d" % (pid, n, s))
