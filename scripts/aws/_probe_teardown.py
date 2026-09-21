"""Tear down scratch projects created for Wave 2 closeout verification.

Deletes the `projects` row through the API (the switcher's source) and then the
rows the missing FK cascade leaves behind, plus the project directory.

usage: _probe_teardown.py <project_id> [<project_id> ...]
"""
import json
import os
import re
import shutil
import sqlite3
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8891"
DB = "prompt_matrix/history.sqlite"
ORPHAN_TABLES = [
    "jdf_revisions", "substrate_vault", "substrates", "jdf_documents", "node_revisions",
    "audit_log", "source_conflicts", "project_budgets", "token_ledger_entries",
    "project_comments", "daily_compile_limits", "workspace_settings", "document_locks",
    "sign_offs", "feedback", "runs", "drafts", "executions", "redhat_findings",
    "pipeline_cache", "jdf_cli_documents",
]


def gate_key():
    raw = open("/etc/assure/shell-access.env").read()
    m = re.search(r'SHELL_ACCESS_KEY\s*=\s*["\']?([^"\'\n]+)', raw)
    return m.group(1).strip() if m else ""


def call(method, path):
    req = urllib.request.Request(BASE + path, method=method, headers={"X-Shell-Key": gate_key()})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.read().decode().strip()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200]


c = sqlite3.connect(DB)
for pid in sys.argv[1:]:
    st, body = call("DELETE", "/api/projects/" + pid)
    print("DELETE /api/projects/%s -> %s %s" % (pid, st, body[:80]))
    removed = []
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
                removed.append("%s=%d" % (table, cur.rowcount))
        except sqlite3.OperationalError as exc:
            removed.append("%s ERR %s" % (table, exc))
    c.execute("DELETE FROM projects WHERE id = ?", (pid,))
    c.commit()
    d = os.path.join("prompt_matrix", "projects", pid)
    if os.path.isdir(d):
        shutil.rmtree(d)
        removed.append("dir removed")
    print("   orphans cleared: %s" % (", ".join(removed) or "none"))

st, body = call("GET", "/api/projects")
projects = (json.loads(body) or {}).get("projects") or []
demos = [p for p in projects if (p.get("title") or "") == "demo"]
scratch = [p["id"] for p in projects if "wave2" in (p.get("id") or "")]
print("\nGET /api/projects -> %s  total=%d" % (st, len(projects)))
print("projects titled 'demo': %s" % [p["id"] for p in demos])
print("wave2 scratch projects remaining: %s" % scratch)
c.close()
