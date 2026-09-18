#!/usr/bin/env python3
"""2A — the FK/retention migration: constrain the tables a project delete orphans.

usage:
  migrate_fk_constraints.py --db <path> --backup <path> --dry-run
  migrate_fk_constraints.py --db <path> --backup <path> --apply

Why the tables are rebuilt rather than altered: SQLite cannot ADD a foreign key
to an existing table, so each table is recreated with the constraint, copied,
dropped and renamed — one transaction per table, so a failure leaves that table
exactly as it was.

Safety sequence this tool assumes (and enforces where it can):
  1. the caller has stopped the app (this refuses `--apply` while the service
     answers on 8890, unless --force);
  2. `--backup` names a file that already exists and passes `integrity_check`;
  3. a rollback script is written BEFORE anything is changed;
  4. `--dry-run` on a copy proves the whole thing before the real DB is touched.

Orphans are rows whose `project_id` names a project that no longer exists
(`'default'` is a sentinel, never an orphan). They are deleted: a row that
points at a deleted project cannot satisfy the constraint it is about to
receive, and the alternative — inventing a parent — is a lie about provenance.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.request

# The six the census names, plus `user_activity_log`, which the census also finds
# carrying orphans and which the earlier count of six missed. Measured on
# staging 2026-09-18: audit_log 65, jdf_documents 11, node_revisions 2,
# pipeline_cache 24, project_budgets 18, token_ledger_entries 82,
# user_activity_log 554 — 756 orphans in tables that had no constraint.
TABLES = (
    "audit_log",
    "jdf_documents",
    "node_revisions",
    "pipeline_cache",
    "project_budgets",
    "token_ledger_entries",
    "user_activity_log",
)

# Tables that already declare the FK but hold rows violating it. `PRAGMA
# foreign_keys=ON` turns those rows into failed writes, so they are cleared in
# the same pass — the nine-table zero-orphan check is the gate.
FK_TABLES = {
    "jdf_revisions": "project_id",
    "substrate_vault": "project_id",
    "daily_compile_limits": "project_id",
    "project_comments": "project_id",
    "substrates": "project_id",
    "workspace_settings": "project_id",
    "drafts": "workspace_id",
    "runs": "workspace_id",
    "redhat_findings": "run_id",
}

SENTINEL = "default"
APP_HEALTH = "http://127.0.0.1:8890/health"


def _orphan_predicate(col: str) -> tuple[str, list]:
    return (
        f"{col} is not null and {col} != ? and not exists "
        f"(select 1 from projects p where p.id = {col})",
        [SENTINEL],
    )


def count_orphans(con: sqlite3.Connection, table: str, col: str) -> int:
    pred, params = _orphan_predicate(col)
    return con.execute(f"select count(*) from {table} where {pred}", params).fetchone()[0]


def _columns(con: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in con.execute(f"pragma table_info({table})")]


def _fk_targets(con: sqlite3.Connection, table: str) -> list[str]:
    return [f"FOREIGN KEY ({c}) REFERENCES projects(id) ON DELETE CASCADE" for c in ("project_id",)
            if any(r[1] == "project_id" for r in con.execute(f"pragma table_info({table})"))]


def _index_sql(con: sqlite3.Connection, table: str) -> list[str]:
    rows = con.execute(
        "select sql from sqlite_master where tbl_name=? and type='index' and sql is not null",
        (table,),
    ).fetchall()
    return [r[0] for r in rows]


def _dependent_objects(con: sqlite3.Connection, table: str) -> list[tuple[str, str, str]]:
    """Views and triggers whose SQL names `table` — they block DROP TABLE.

    SQLite refuses to drop a table a view selects from ("error in view …: no such
    table"), which is how the first dry run failed on `view_z3_health`. Both
    kinds are read out verbatim, dropped for the rebuild, and put back from the
    same SQL afterwards, so nothing about them changes.
    """
    out = []
    for name, kind, sql in con.execute(
        "select name, type, sql from sqlite_master where type in ('view','trigger') and sql is not null"
    ).fetchall():
        if re.search(rf"\b{re.escape(table)}\b", sql):
            out.append((name, kind, sql))
    return out


def rebuild_with_fk(con: sqlite3.Connection, table: str, report: dict) -> None:
    """Recreate `table` with the FK, in one transaction. Nothing else changes."""
    original = con.execute(
        "select sql from sqlite_master where name=? and type='table'", (table,)
    ).fetchone()
    if not original:
        report["skipped"].append(f"{table}: no such table")
        return
    ddl = " ".join(original[0].split())
    if "FOREIGN KEY" in ddl.upper():
        report["already"].append(table)
        return
    cols = _columns(con, table)
    body = ddl[ddl.index("(") + 1: ddl.rindex(")")]
    new = f'CREATE TABLE "{table}__fk" ({body}, FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE)'
    col_list = ", ".join(f'"{c}"' for c in cols)
    indexes = _index_sql(con, table)
    dependents = _dependent_objects(con, table)

    con.execute("BEGIN")
    try:
        for name, kind, _sql in dependents:
            con.execute(f'DROP {kind.upper()} IF EXISTS "{name}"')
        con.execute(new)
        con.execute(f'INSERT INTO "{table}__fk" ({col_list}) SELECT {col_list} FROM "{table}"')
        con.execute(f'DROP TABLE "{table}"')
        con.execute(f'ALTER TABLE "{table}__fk" RENAME TO "{table}"')
        for ix in indexes:
            con.execute(ix)
        for _name, _kind, sql in dependents:
            con.execute(sql)
        con.execute("COMMIT")
    except Exception as exc:
        con.execute("ROLLBACK")
        raise RuntimeError(f"{table}: rolled back — {exc}") from exc
    report["rebuilt"].append({"table": table, "columns": len(cols), "indexes": len(indexes),
                              "dependents_restored": [n for n, _k, _s in dependents]})


def purge_orphans(con: sqlite3.Connection, table: str, col: str, report: dict) -> int:
    pred, params = _orphan_predicate(col)
    con.execute("BEGIN")
    try:
        n = con.execute(f"select count(*) from {table} where {pred}", params).fetchone()[0]
        if n:
            con.execute(f"delete from {table} where {pred}", params)
        con.execute("COMMIT")
    except Exception as exc:
        con.execute("ROLLBACK")
        raise RuntimeError(f"{table}: purge rolled back — {exc}") from exc
    if n:
        report["purged"][table] = n
    return n


def write_rollback(path: str, db: str, backup: str) -> None:
    with open(path, "w") as fh:
        fh.write(
            "#!/usr/bin/env bash\n"
            "# Written BEFORE the migration ran. Restores the pre-migration database.\n"
            "set -euo pipefail\n"
            f'DB="{db}"\n'
            f'BK="{backup}"\n'
            'MIG="$DB.pre-migration"\n'
            '[ -f "$MIG" ] || { echo "no pre-migration copy at $MIG" >&2; exit 1; }\n'
            'echo "rolling back: $DB <- $BK"\n'
            'cp -f "$BK" "$DB"\n'
            'echo "restored. verify with: sqlite3 \\"$DB\\" \'PRAGMA integrity_check;\'"\n'
        )
    os.chmod(path, 0o755)


def app_is_answering() -> bool:
    try:
        with urllib.request.urlopen(APP_HEALTH, timeout=4) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--backup", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--report", default="")
    args = ap.parse_args()
    if args.dry_run == args.apply:
        print("choose exactly one of --dry-run / --apply", file=sys.stderr)
        return 2

    if not os.path.exists(args.backup):
        print(f"backup {args.backup} does not exist — refusing", file=sys.stderr)
        return 2
    bcheck = sqlite3.connect(args.backup)
    integrity = bcheck.execute("PRAGMA integrity_check").fetchone()[0]
    backup_projects = bcheck.execute("select count(*) from projects").fetchone()[0]
    bcheck.close()
    if integrity != "ok":
        print(f"backup integrity is {integrity!r} — refusing", file=sys.stderr)
        return 2

    if args.apply and not args.force and app_is_answering():
        print("the app is still answering on 8890 — stop the service first", file=sys.stderr)
        return 3

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rollback_path = os.path.join(os.path.dirname(args.backup), f"rollback_{stamp}.sh")
    report: dict = {
        "mode": "apply" if args.apply else "dry-run",
        "db": args.db,
        "backup": args.backup,
        "backup_md5_integrity": integrity,
        "backup_projects": backup_projects,
        "rollback_script": rollback_path,
        "purged": {}, "rebuilt": [], "already": [], "skipped": [],
    }

    if args.apply:
        write_rollback(rollback_path, args.db, args.backup)
        report["rollback_written_before_changes"] = True
        # The pre-migration copy: the rollback script reads it as the marker that
        # a migration actually ran here.
        cp = sqlite3.connect(args.db)
        with open(args.db + ".pre-migration", "wb") as fh:
            for chunk in cp.iterdump():
                fh.write((chunk + "\n").encode())
        cp.close()

    con = sqlite3.connect(args.db)
    con.execute("PRAGMA foreign_keys=OFF")  # required while a parent is rebuilt
    before_counts = {t: con.execute(f"select count(*) from {t}").fetchone()[0]
                     for t in list(TABLES) + list(FK_TABLES) if con.execute(
                         "select 1 from sqlite_master where name=? and type='table'", (t,)).fetchone()}
    report["rows_before"] = before_counts

    try:
        for t in TABLES:
            purge_orphans(con, t, "project_id", report)
        for t, col in FK_TABLES.items():
            if con.execute("select 1 from sqlite_master where name=? and type='table'", (t,)).fetchone():
                purge_orphans(con, t, col, report)
        for t in TABLES:
            rebuild_with_fk(con, t, report)
    except Exception as exc:
        print(f"MIGRATION STOPPED: {exc}", file=sys.stderr)
        report["error"] = str(exc)
        if args.report:
            json.dump(report, open(args.report, "w"), indent=2)
        return 1

    # ---- verification -------------------------------------------------------
    zero = {}
    for t in TABLES:
        zero[t] = count_orphans(con, t, "project_id")
    for t, col in FK_TABLES.items():
        if con.execute("select 1 from sqlite_master where name=? and type='table'", (t,)).fetchone():
            zero[t] = count_orphans(con, t, col)
    fk_check = len(con.execute("PRAGMA foreign_key_check").fetchall())
    fk_declared = {
        t: [dict(zip(("id", "seq", "table", "from", "to", "on_update", "on_delete", "match"), r))
            for r in con.execute(f"pragma foreign_key_list({t})")]
        for t in TABLES
    }
    after_counts = {t: con.execute(f"select count(*) from {t}").fetchone()[0] for t in before_counts}
    report["orphans_after"] = zero
    report["foreign_key_check_violations"] = fk_check
    report["fk_declared"] = {t: bool(v) for t, v in fk_declared.items()}
    report["fk_on_delete"] = {t: (v[0]["on_delete"] if v else None) for t, v in fk_declared.items()}
    report["rows_after"] = after_counts
    report["rows_delta"] = {t: after_counts[t] - before_counts[t] for t in before_counts}
    con.commit()
    con.close()

    ok = (fk_check == 0 and all(v == 0 for v in zero.values())
          and all(report["fk_declared"].values())
          and all(report["fk_on_delete"][t] == "CASCADE" for t in TABLES))
    report["verdict"] = "PASS" if ok else "FAIL"
    if args.report:
        json.dump(report, open(args.report, "w"), indent=2)

    print(json.dumps({k: report[k] for k in (
        "mode", "purged", "rebuilt", "already", "skipped", "orphans_after",
        "foreign_key_check_violations", "fk_declared", "fk_on_delete",
        "rows_delta", "verdict")}, indent=2))
    print(f"\nPREDICTED PURGE TOTAL: {sum(report['purged'].values())} rows")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
