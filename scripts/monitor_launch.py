#!/usr/bin/env python3
"""Lightweight launch telemetry from local SQLite (no external deps)."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def _db_path() -> Path:
    return Path(os.environ.get("DATABASE_PATH", "./data/history.sqlite"))


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _collect(conn: sqlite3.Connection) -> dict:
    out: dict = {
        "total_runs": 0,
        "models": {},
        "feedback_up": 0,
        "feedback_down": 0,
        "feedback_total": 0,
        "pro_users": 0,
        "pro_rows": [],
        "tables": [],
    }

    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    ).fetchall()
    out["tables"] = [row[0] for row in rows]

    if _table_exists(conn, "executions"):
        out["total_runs"] = int(
            conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0]
        )
        for row in conn.execute(
            """
            SELECT COALESCE(NULLIF(TRIM(target_ai), ''), 'unknown') AS model, COUNT(*) AS n
            FROM executions
            GROUP BY 1
            ORDER BY n DESC
            """
        ):
            out["models"][str(row[0])] = int(row[1])

    if _table_exists(conn, "prompt_performance"):
        up = conn.execute(
            "SELECT COUNT(*) FROM prompt_performance WHERE rating = 1"
        ).fetchone()[0]
        down = conn.execute(
            "SELECT COUNT(*) FROM prompt_performance WHERE rating = 0"
        ).fetchone()[0]
        out["feedback_up"] = int(up)
        out["feedback_down"] = int(down)
        out["feedback_total"] = out["feedback_up"] + out["feedback_down"]

        if not out["total_runs"]:
            out["total_runs"] = int(
                conn.execute(
                    "SELECT COUNT(DISTINCT run_hash) FROM prompt_performance WHERE run_hash IS NOT NULL AND run_hash != ''"
                ).fetchone()[0]
            )

        for row in conn.execute(
            """
            SELECT COALESCE(NULLIF(TRIM(model), ''), 'unknown') AS model, COUNT(*) AS n
            FROM prompt_performance
            WHERE model IS NOT NULL AND TRIM(model) != ''
            GROUP BY 1
            ORDER BY n DESC
            """
        ):
            key = str(row[0])
            out["models"][key] = out["models"].get(key, 0) + int(row[1])

    if _table_exists(conn, "user_subscriptions"):
        pro_rows = conn.execute(
            """
            SELECT clerk_user_id, tier, updated_at
            FROM user_subscriptions
            WHERE LOWER(TRIM(tier)) = 'pro'
            ORDER BY updated_at DESC
            """
        ).fetchall()
        out["pro_users"] = len(pro_rows)
        out["pro_rows"] = [
            {"clerk_user_id": r[0], "tier": r[1], "updated_at": r[2]} for r in pro_rows
        ]

    gemini = sum(n for k, n in out["models"].items() if "gemini" in k.lower())
    claude = sum(n for k, n in out["models"].items() if "claude" in k.lower())
    out["gemini_runs"] = gemini
    out["claude_runs"] = claude
    return out


def _print_report(path: Path, metrics: dict) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"Assure launch monitor — {now}")
    print(f"Database: {path.resolve()}")
    print(f"Tables: {', '.join(metrics['tables']) or '(none)'}")
    print()
    print(f"Total runs logged:     {metrics['total_runs']}")
    print(f"Gemini usage:          {metrics['gemini_runs']}")
    print(f"Claude usage:          {metrics['claude_runs']}")
    if metrics["models"]:
        print("Model distribution:")
        for model, count in sorted(metrics["models"].items(), key=lambda item: (-item[1], item[0])):
            print(f"  - {model}: {count}")
    print()
    print(f"Feedback 👍 (rating=1): {metrics['feedback_up']}")
    print(f"Feedback 👎 (rating=0): {metrics['feedback_down']}")
    print(f"Feedback total:        {metrics['feedback_total']}")
    print()
    print(f"Active Pro subscriptions: {metrics['pro_users']}")
    for row in metrics["pro_rows"][:10]:
        print(f"  - {row['clerk_user_id']} ({row['updated_at']})")
    if metrics["pro_users"] > 10:
        print(f"  … and {metrics['pro_users'] - 10} more")


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor Assure launch SQLite telemetry.")
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Refresh metrics every 5 seconds.",
    )
    parser.add_argument(
        "--db",
        default="",
        help="SQLite path (overrides DATABASE_PATH).",
    )
    args = parser.parse_args()

    path = Path(args.db) if args.db else _db_path()
    if not path.is_file():
        print(f"Database not found: {path}", file=sys.stderr)
        return 1

    while True:
        if args.watch:
            print("\033[2J\033[H", end="")
        conn = sqlite3.connect(str(path))
        try:
            metrics = _collect(conn)
        finally:
            conn.close()
        _print_report(path, metrics)
        if not args.watch:
            break
        print("\nRefreshing in 5s… (Ctrl+C to stop)")
        try:
            time.sleep(5)
        except KeyboardInterrupt:
            print()
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
