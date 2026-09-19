"""2A — the mechanism behind the residual orphan, demonstrated on a COPY of the live DB.

The live DB now holds one audit_log row naming project_id='nope' (a sibling probe's
row) with PRAGMA foreign_key_check reporting one violation, while the app's own
connections set foreign_keys=ON. This experiment reproduces both sides on a throwaway
copy: the same insert under foreign_keys=ON is rejected; under OFF (the state of a raw
connection like lib/logger.py:111) it lands. Nothing here touches the live DB: the copy
is made with sqlite3's backup API through a read-only connection.
"""
from __future__ import annotations

import os
import sqlite3
import sys

LIVE = "/home/ubuntu/assure-prototype/prompt_matrix/history.sqlite"
COPY = "/tmp/_2a_fk_proof.sqlite"


def main() -> int:
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(COPY + suffix):
            os.unlink(COPY + suffix)

    src = sqlite3.connect(f"file:{LIVE}?mode=ro", uri=True)
    dst = sqlite3.connect(COPY)
    with dst:
        src.backup(dst)
    src.close()

    live_check = sqlite3.connect(f"file:{LIVE}?mode=ro", uri=True)
    n = live_check.execute(
        "select count(*) from audit_log where project_id='nope'").fetchone()[0]
    viol = live_check.execute("PRAGMA foreign_key_check").fetchall()
    proj = live_check.execute("select count(*) from projects where id='nope'").fetchone()[0]
    print(f"LIVE: audit_log rows with project_id='nope' = {n}; "
          f"projects with id='nope' = {proj}; foreign_key_check violations = {len(viol)}")
    for v in viol[:3]:
        print("   violation:", tuple(v))
    live_check.close()

    insert = ("insert into audit_log (id, request_id, project_id, action, success, created_at) "
              "values (?, ?, ?, ?, ?, datetime('now'))")
    row = ("2aproof0000000000000000000000000", "2a-fk-proof", "nope", "RETRIEVAL_SEARCH", 1)

    for mode in ("ON", "OFF"):
        con = sqlite3.connect(COPY)
        con.execute(f"PRAGMA foreign_keys={mode};")
        try:
            con.execute(insert, row)
            con.commit()
            outcome = "INSERT LANDED"
        except sqlite3.IntegrityError as exc:
            outcome = f"REJECTED — {exc}"
        print(f"  foreign_keys={mode:<3} -> {outcome}")
        con.close()

    con = sqlite3.connect(COPY)
    print("copy: foreign_key_check violations now =",
          len(con.execute("PRAGMA foreign_key_check").fetchall()))
    con.close()
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(COPY + suffix):
            os.unlink(COPY + suffix)
    print("copy removed; the live DB was only ever opened read-only")
    return 0


if __name__ == "__main__":
    sys.exit(main())
