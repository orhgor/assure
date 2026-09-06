#!/usr/bin/env python3
"""One-time migration: IndexedDB vault prompts (localStorage fallback) → SQLite prompts table."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prompt_matrix.db.connection import init_db  # noqa: E402
from prompt_matrix.db.prompts_repository import create_prompt, list_prompts  # noqa: E402
from prompt_matrix.history import get_db  # noqa: E402

LS_KEY = "assure_vault_prompts_v1"


def load_localstorage_export(path: Path | None) -> list[dict]:
    if path and path.is_file():
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            return raw
        if isinstance(raw, dict) and isinstance(raw.get("prompts"), list):
            return raw["prompts"]
    return []


def migrate_rows(rows: list[dict], *, dry_run: bool) -> int:
    init_db()
    existing = {p["name"] for p in list_prompts(include_global=True)}
    migrated = 0
    for row in rows:
        name = str(row.get("name") or "").strip()
        content = str(row.get("content") or "").strip()
        if not name or not content:
            continue
        if name in existing:
            continue
        if dry_run:
            migrated += 1
            continue
        create_prompt(
            name=name,
            content=content,
            prompt_class=str(row.get("class") or "research"),
            tags=[],
            user_id=None,
            is_global=False,
        )
        existing.add(name)
        migrated += 1
    if not dry_run:
        get_db().commit()
    return migrated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        help="JSON file exported from browser localStorage key assure_vault_prompts_v1",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    rows = load_localstorage_export(args.input)
    if not rows:
        print("No prompts to migrate (pass --input with exported JSON).")
        return 0
    count = migrate_rows(rows, dry_run=args.dry_run)
    label = "would migrate" if args.dry_run else "migrated"
    print(f"{label} {count} prompt(s) into SQLite.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
