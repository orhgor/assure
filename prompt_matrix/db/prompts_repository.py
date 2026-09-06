"""SQLite prompt library (replaces IndexedDB vault prompts)."""

from __future__ import annotations

import json
import uuid
from typing import Any

try:
    from ..db.connection import init_db
    from ..history import get_db
except ImportError:
    from db.connection import init_db
    from history import get_db


def _row_to_prompt(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "id": row[0],
        "user_id": row[1],
        "name": row[2],
        "class": row[3],
        "tags": json.loads(row[4] or "[]"),
        "content": row[5],
        "version_history": json.loads(row[6] or "[]"),
        "is_global": bool(row[7]),
        "created_at": row[8],
        "updated_at": row[9],
    }


def list_prompts(
    *, user_id: str | None = None, include_global: bool = True
) -> list[dict[str, Any]]:
    init_db()
    db = get_db()
    if include_global:
        rows = db.execute(
            """
            SELECT id, user_id, name, class, tags, content, version_history,
                   is_global, created_at, updated_at
            FROM prompts
            WHERE is_global = 1 OR user_id IS ? OR user_id = ?
            ORDER BY updated_at DESC, name ASC
            """,
            (user_id, user_id or ""),
        ).fetchall()
    else:
        rows = db.execute(
            """
            SELECT id, user_id, name, class, tags, content, version_history,
                   is_global, created_at, updated_at
            FROM prompts
            WHERE user_id IS ?
            ORDER BY updated_at DESC, name ASC
            """,
            (user_id,),
        ).fetchall()
    return [_row_to_prompt(r) for r in rows]


def fetch_prompt(prompt_id: str) -> dict[str, Any] | None:
    init_db()
    db = get_db()
    row = db.execute(
        """
        SELECT id, user_id, name, class, tags, content, version_history,
               is_global, created_at, updated_at
        FROM prompts WHERE id = ?
        """,
        (prompt_id,),
    ).fetchone()
    return _row_to_prompt(row) if row else None


def create_prompt(
    *,
    name: str,
    content: str,
    prompt_class: str = "research",
    tags: list[str] | None = None,
    user_id: str | None = None,
    is_global: bool = False,
) -> dict[str, Any]:
    init_db()
    pid = uuid.uuid4().hex
    tags_json = json.dumps(tags or [])
    history = json.dumps([{"version": 1, "content": content}])
    db = get_db()
    db.execute(
        """
        INSERT INTO prompts
          (id, user_id, name, class, tags, content, version_history, is_global)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (pid, user_id, name, prompt_class, tags_json, content, history, 1 if is_global else 0),
    )
    db.commit()
    return fetch_prompt(pid) or {}


def update_prompt(
    prompt_id: str,
    *,
    name: str | None = None,
    content: str | None = None,
    prompt_class: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any] | None:
    init_db()
    existing = fetch_prompt(prompt_id)
    if not existing:
        return None
    new_name = name if name is not None else existing["name"]
    new_content = content if content is not None else existing["content"]
    new_class = prompt_class if prompt_class is not None else existing["class"]
    new_tags = tags if tags is not None else existing["tags"]
    history = list(existing.get("version_history") or [])
    if content is not None and content != existing["content"]:
        history.append({"version": len(history) + 1, "content": content})
    db = get_db()
    db.execute(
        """
        UPDATE prompts
        SET name = ?, class = ?, tags = ?, content = ?,
            version_history = ?, updated_at = datetime('now')
        WHERE id = ?
        """,
        (
            new_name,
            new_class,
            json.dumps(new_tags),
            new_content,
            json.dumps(history),
            prompt_id,
        ),
    )
    db.commit()
    return fetch_prompt(prompt_id)


def delete_prompt(prompt_id: str) -> bool:
    init_db()
    db = get_db()
    cur = db.execute("DELETE FROM prompts WHERE id = ?", (prompt_id,))
    db.commit()
    return cur.rowcount > 0
