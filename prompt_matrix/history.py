"""Opt-in local SQLite log.

Hashes and token counts: PEM_ENABLE_HISTORY=1 or --history.
Full compiled prompt and final reply: PEM_STORE_PROMPTS=1 (separate table).
"""

from __future__ import annotations

import difflib
import hashlib
import html
import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from .paths import user_data_dir
except ImportError:
    from paths import user_data_dir


def _resolve_db_path() -> Path:
    override = (os.environ.get("DATABASE_PATH") or "").strip()
    if override:
        return Path(override)
    return user_data_dir() / "history.sqlite"


DB_PATH = _resolve_db_path()


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    # SQLite ships with foreign_keys OFF and it is per-connection, so every
    # `ON DELETE CASCADE` in the schema was inert: `DELETE FROM projects WHERE id
    # = ?` (routers/project_routes.py) removed the project row and left its
    # children behind. Seven tables declare a cascade to projects — drafts,
    # jdf_revisions, substrate_vault, substrates, project_comments,
    # workspace_settings, daily_compile_limits — and no table declares NO ACTION
    # against it, so turning enforcement on can only complete a delete that was
    # already meant to cascade; it cannot make one fail. runs.workspace_id is
    # ON DELETE SET NULL and stays as declared.
    #
    # Set here because this is the one place every connection passes through:
    # _new_connection below, the SQLAlchemy `connect` event in db/pool.py, and
    # the two re-applications in db/connection.py. It must run outside a
    # transaction, which holds at all four call sites.
    #
    # Six of the nine tables a project delete orphans — jdf_documents,
    # node_revisions, audit_log, project_budgets, token_ledger_entries,
    # pipeline_cache — carry a project_id with no FOREIGN KEY clause at all, so
    # no pragma can reach them; they still need the schema migration.
    conn.execute("PRAGMA foreign_keys=ON;")


def _new_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    use_pool = os.environ.get("SQLITE_USE_POOL", "1").lower() not in ("0", "false", "no")
    if use_pool:
        try:
            from .db.pool import checkout_dbapi_connection

            return checkout_dbapi_connection()
        except ImportError:
            pass
        except Exception as exc:
            # Fall back to a direct connection so the request still works, but make
            # the saturated/misconfigured pool visible instead of stalling silently.
            logging.getLogger("assure").warning(
                "SQLite pool checkout failed (%s: %s); using a direct connection",
                exc.__class__.__name__,
                exc,
            )
    # check_same_thread=False to match db/pool.py's connect_args: the SSE keepalive
    # pump runs blocking work on a worker thread, which reaches this fallback path
    # when the pool is saturated (or SQLITE_USE_POOL=0).
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _apply_pragmas(conn)
    return conn


def _release_connection(conn: sqlite3.Connection | None) -> None:
    """Return a pooled checkout to SQLAlchemy; plain close for direct sqlite3."""
    if conn is None:
        return
    try:
        from .db.pool import connection_is_pooled, release_dbapi_connection

        if connection_is_pooled(conn):
            release_dbapi_connection(conn)
            return
    except Exception:
        pass
    try:
        conn.close()
    except Exception:
        pass


def get_db() -> sqlite3.Connection:
    """Request-scoped SQLite handle in Flask; standalone connection elsewhere."""
    try:
        from flask import g, has_app_context

        if has_app_context():
            db = g.get("db")
            if db is None:
                db = _new_connection()
                g.db = db
            return db
    except ImportError:
        pass
    return _new_connection()


def close_db(e=None) -> None:
    try:
        from flask import g, has_app_context

        if has_app_context():
            db = g.pop("db", None)
            if db is not None:
                _release_connection(db)
    except ImportError:
        pass


def ensure_user_subscriptions_table(conn: sqlite3.Connection | None = None) -> None:
    db = conn or get_db()
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS user_subscriptions (
            clerk_user_id TEXT PRIMARY KEY,
            tier TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def upsert_user_subscription(clerk_user_id: str, tier: str) -> None:
    user_id = (clerk_user_id or "").strip()
    if not user_id:
        return
    db = get_db()
    standalone = True
    try:
        from flask import has_app_context

        standalone = not has_app_context()
    except ImportError:
        pass
    try:
        ensure_user_subscriptions_table(db)
        db.execute(
            """
            INSERT INTO user_subscriptions (clerk_user_id, tier, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(clerk_user_id) DO UPDATE SET
                tier = excluded.tier,
                updated_at = datetime('now')
            """,
            (user_id, tier),
        )
        db.commit()
    finally:
        if standalone:
            _release_connection(db)


def history_enabled(flag: bool = False) -> bool:
    if flag:
        return True
    return os.environ.get("PEM_ENABLE_HISTORY", "").strip().lower() in {"1", "true", "yes", "on"}


def store_prompts_enabled() -> bool:
    return bool(os.environ.get("PEM_STORE_PROMPTS", "").strip())


def run_hash(compiled_prompt: str) -> str:
    return hashlib.sha256((compiled_prompt or "").encode("utf-8")).hexdigest()[:16]


def migrate_to_full_storage() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = _new_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS prompt_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_hash TEXT NOT NULL,
                compiled_prompt TEXT,
                final_response TEXT,
                model TEXT,
                intent TEXT,
                workflow TEXT,
                input_tokens INTEGER,
                output_tokens INTEGER,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(run_hash)
            )
            """
        )
        conn.commit()
    finally:
        _release_connection(conn)


def store_full_run(
    run_hash: str,
    compiled_prompt: str,
    final_response: str,
    model: str,
    intent: str,
    workflow: str,
    input_tokens: int,
    output_tokens: int,
    force: bool = False,
) -> None:
    if not force and not store_prompts_enabled():
        return
    migrate_to_full_storage()
    conn = _new_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO prompt_versions
            (run_hash, compiled_prompt, final_response, model, intent, workflow, input_tokens, output_tokens)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_hash,
                compiled_prompt,
                final_response,
                model,
                intent,
                workflow,
                input_tokens,
                output_tokens,
            ),
        )
        conn.commit()
    finally:
        _release_connection(conn)


def record_run(
    *,
    target_ai: str,
    intent: str,
    workflow: str,
    task: str,
    prompt: str,
    reply: str | None,
    note: str | None = None,
    persona: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    total_tokens: int = 0,
    extra_targets: list[str] | None = None,
    ground: bool = False,
    cheap: bool = False,
    local: bool = False,
    estimated_cost: float | None = None,
    class_id: str | None = None,
    critic: str | None = None,
    edition: str | None = None,
    context: str | None = None,
) -> int:
    conn = _new_connection()
    try:
        _ensure_executions(conn)
        prompt = prompt or ""
        reply = reply or ""
        extras = [name for name in (extra_targets or []) if name and name != target_ai]
        cur = conn.execute(
            """
            INSERT INTO executions (
                timestamp, target_ai, intent, workflow, persona, task,
                prompt_hash, prompt_chars, reply_chars, token_delta, note,
                input_tokens, output_tokens, total_tokens,
                extra_targets, ground, cheap, local, estimated_cost,
                class_id, critic, edition, context
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                target_ai,
                intent,
                workflow,
                persona,
                (task or "")[:2000],
                run_hash(prompt),
                len(prompt),
                len(reply),
                output_tokens - input_tokens if reply else None,
                (note or "")[:1000],
                input_tokens,
                output_tokens,
                total_tokens,
                json.dumps(extras),
                1 if ground else 0,
                1 if cheap else 0,
                1 if local else 0,
                estimated_cost,
                class_id,
                critic,
                edition,
                (context or "")[:8000],
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        _release_connection(conn)


def prune_old_executions(days: int) -> None:
    if days <= 0:
        return
    if not DB_PATH.exists():
        return
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    conn = _new_connection()
    try:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "executions" not in tables:
            return
        conn.execute("DELETE FROM executions WHERE timestamp < ?", (cutoff,))
        conn.commit()
    finally:
        _release_connection(conn)


def _ensure_sends_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_sends (
            day TEXT PRIMARY KEY,
            count INTEGER NOT NULL
        )
        """
    )


def count_sends_today() -> int:
    try:
        from flask import has_app_context

        if has_app_context():
            conn = get_db()
            _ensure_sends_table(conn)
            day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            row = conn.execute("SELECT count FROM daily_sends WHERE day = ?", (day,)).fetchone()
            return int(row[0]) if row else 0
    except ImportError:
        pass
    conn = _new_connection()
    try:
        _ensure_sends_table(conn)
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        row = conn.execute("SELECT count FROM daily_sends WHERE day = ?", (day,)).fetchone()
        return int(row[0]) if row else 0
    finally:
        _release_connection(conn)


def record_send() -> None:
    try:
        from flask import has_app_context

        if has_app_context():
            conn = get_db()
            _ensure_sends_table(conn)
            day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            conn.execute(
                """
                INSERT INTO daily_sends (day, count) VALUES (?, 1)
                ON CONFLICT(day) DO UPDATE SET count = count + 1
                """,
                (day,),
            )
            conn.commit()
            return
    except ImportError:
        pass
    conn = _new_connection()
    try:
        _ensure_sends_table(conn)
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        conn.execute(
            """
            INSERT INTO daily_sends (day, count) VALUES (?, 1)
            ON CONFLICT(day) DO UPDATE SET count = count + 1
            """,
            (day,),
        )
        conn.commit()
    finally:
        _release_connection(conn)


def _ensure_executions(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS executions (
            id INTEGER PRIMARY KEY,
            timestamp TEXT NOT NULL,
            target_ai TEXT,
            intent TEXT,
            workflow TEXT,
            persona TEXT,
            task TEXT,
            prompt_hash TEXT,
            prompt_chars INTEGER,
            reply_chars INTEGER,
            token_delta INTEGER,
            note TEXT
        )
        """
    )
    cols = {row[1] for row in conn.execute("PRAGMA table_info(executions)")}
    adds = {
        "input_tokens": "INTEGER",
        "output_tokens": "INTEGER",
        "total_tokens": "INTEGER",
        "extra_targets": "TEXT",
        "ground": "INTEGER",
        "cheap": "INTEGER",
        "local": "INTEGER",
        "estimated_cost": "REAL",
        "class_id": "TEXT",
        "critic": "TEXT",
        "edition": "TEXT",
        "context": "TEXT",
    }
    for name, kind in adds.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE executions ADD COLUMN {name} {kind}")


def _ensure_token_columns(conn: sqlite3.Connection) -> None:
    _ensure_executions(conn)


def _connect() -> sqlite3.Connection:
    return _new_connection()


def _parse_ts(value: str | None) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _bucket(ts: str | None, now: datetime) -> str:
    dt = _parse_ts(ts)
    if not dt:
        return "older"
    day = dt.date()
    today = now.date()
    if day == today:
        return "today"
    if day == today - timedelta(days=1):
        return "yesterday"
    if day >= today - timedelta(days=7):
        return "week"
    return "older"


def _extras(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [str(item) for item in data if item]


def _cutoff_iso(days: int | None) -> str | None:
    if not days:
        return None
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


def _row_public(row: sqlite3.Row, *, full: bool) -> dict:
    extras = _extras(row["extra_targets"] if "extra_targets" in row.keys() else None)
    primary = row["target_ai"] or ""
    models = [primary] + [name for name in extras if name != primary]
    ground = bool(row["ground"]) if "ground" in row.keys() and row["ground"] else False
    reply_chars = int(row["reply_chars"] or 0)
    inn = int(row["input_tokens"] or 0) if "input_tokens" in row.keys() else 0
    out = int(row["output_tokens"] or 0) if "output_tokens" in row.keys() else 0
    tot = int(row["total_tokens"] or 0) if "total_tokens" in row.keys() else inn + out
    cost = row["estimated_cost"] if "estimated_cost" in row.keys() else None
    badge = "ground" if ground else ("ready" if reply_chars else "copy")
    item = {
        "id": row["id"],
        "run_hash": row["prompt_hash"],
        "task": row["task"] or "",
        "timestamp": row["timestamp"],
        "target_ai": primary,
        "extra_targets": extras,
        "models": models,
        "intent": row["intent"] or "",
        "workflow": row["workflow"] or "single",
        "persona": row["persona"],
        "ground": ground,
        "cheap": bool(row["cheap"]) if "cheap" in row.keys() and row["cheap"] else False,
        "local": bool(row["local"]) if "local" in row.keys() and row["local"] else False,
        "class_id": row["class_id"] if "class_id" in row.keys() else None,
        "critic": row["critic"] if "critic" in row.keys() else None,
        "edition": row["edition"] if "edition" in row.keys() else None,
        "tokens": {"input": inn, "output": out, "total": tot},
        "estimated_cost": float(cost) if cost is not None else None,
        "badge": badge,
        "has_answer": reply_chars > 0,
    }
    if full:
        item["note"] = row["note"] or ""
        item["context"] = row["context"] if "context" in row.keys() else ""
    else:
        item["note"] = ""
        item["context"] = ""
    return item


def list_works(
    *, days: int | None, full: bool, q: str = "", limit: int = 50, offset: int = 0
) -> dict:
    if not DB_PATH.exists():
        return {"groups": [], "total": 0}
    q = (q or "").strip()
    cutoff = _cutoff_iso(days)
    conn = _connect()
    rows = []
    previews: dict[str, str] = {}
    total = 0
    try:
        _ensure_executions(conn)
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "executions" not in tables:
            return {"groups": [], "total": 0}
        where = ["1=1"]
        args: list = []
        if cutoff:
            where.append("e.timestamp >= ?")
            args.append(cutoff)
        join = ""
        if q:
            like = f"%{q}%"
            if full and "prompt_versions" in tables:
                join = "LEFT JOIN prompt_versions p ON e.prompt_hash = p.run_hash"
                where.append(
                    "(e.task LIKE ? OR e.intent LIKE ? OR e.workflow LIKE ? "
                    "OR IFNULL(p.final_response,'') LIKE ? OR IFNULL(p.compiled_prompt,'') LIKE ?)"
                )
                args.extend([like, like, like, like, like])
            else:
                where.append("e.task LIKE ?")
                args.append(like)
        clause = " AND ".join(where)
        total = conn.execute(
            f"SELECT COUNT(*) FROM executions e {join} WHERE {clause}", args
        ).fetchone()[0]
        rows = conn.execute(
            f"""
            SELECT e.* FROM executions e {join}
            WHERE {clause}
            ORDER BY e.timestamp DESC, e.id DESC
            LIMIT ? OFFSET ?
            """,
            [*args, max(1, min(int(limit or 50), 100)), max(0, int(offset or 0))],
        ).fetchall()
        previews: dict[str, str] = {}
        if full and rows:
            hashes = [row["prompt_hash"] for row in rows if row["prompt_hash"]]
            if hashes and "prompt_versions" in (
                {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            ):
                qmarks = ",".join("?" * len(hashes))
                for digest, text in conn.execute(
                    f"SELECT run_hash, substr(IFNULL(final_response,''), 1, 160) "
                    f"FROM prompt_versions WHERE run_hash IN ({qmarks})",
                    hashes,
                ).fetchall():
                    previews[digest] = (text or "").strip()
    finally:
        _release_connection(conn)
    now = datetime.now(timezone.utc)
    buckets = {"today": [], "yesterday": [], "week": [], "older": []}
    for row in rows:
        item = _row_public(row, full=full)
        item["preview"] = previews.get(row["prompt_hash"] or "", "") if full else ""
        buckets[_bucket(row["timestamp"], now)].append(item)
    groups = []
    for key in ("today", "yesterday", "week", "older"):
        if buckets[key]:
            groups.append({"id": key, "items": buckets[key]})
    return {"groups": groups, "total": int(total)}


def get_work(item_id: int, *, full: bool, days: int | None = None) -> dict | None:
    if not DB_PATH.exists():
        return None
    conn = _connect()
    try:
        _ensure_executions(conn)
        row = conn.execute("SELECT * FROM executions WHERE id = ?", (item_id,)).fetchone()
        if not row:
            return None
        cutoff = _cutoff_iso(days)
        if cutoff and (row["timestamp"] or "") < cutoff:
            return None
        item = _row_public(row, full=full)
        answer = None
        compiled = None
        if full:
            migrate_to_full_storage()
            ver = conn.execute(
                "SELECT compiled_prompt, final_response FROM prompt_versions WHERE run_hash = ?",
                (row["prompt_hash"],),
            ).fetchone()
            if ver:
                compiled = ver["compiled_prompt"]
                answer = ver["final_response"]
        item["answer"] = answer if full else None
        item["compiled"] = compiled if full else None
        item["full"] = full
        return item
    finally:
        _release_connection(conn)


def get_run_by_hash(run_hash: str) -> dict | None:
    """Retrieve a single stored reply by its run_hash.

    Replies live in ``prompt_versions`` (not ``executions``). There is no
    ``get_db()`` in this module; this uses ``_connect()``.
    """
    digest = (run_hash or "").strip()
    if not digest:
        return None
    conn = _connect()
    try:
        migrate_to_full_storage()
        _ensure_executions(conn)
        ver = conn.execute(
            """
            SELECT run_hash, compiled_prompt, final_response, model, intent, timestamp
            FROM prompt_versions WHERE run_hash = ?
            """,
            (digest,),
        ).fetchone()
        if not ver:
            return None
        exe = conn.execute(
            """
            SELECT timestamp, target_ai, intent
            FROM executions WHERE prompt_hash = ?
            ORDER BY id DESC LIMIT 1
            """,
            (digest,),
        ).fetchone()
        return {
            "run_hash": ver["run_hash"],
            "reply": ver["final_response"] or "",
            "prompt": ver["compiled_prompt"] or "",
            "model": ver["model"] or (exe["target_ai"] if exe else None),
            "intent": ver["intent"] or (exe["intent"] if exe else ""),
            "created_at": (exe["timestamp"] if exe else ver["timestamp"]),
        }
    finally:
        _release_connection(conn)


def diff_runs(left_hash: str, right_hash: str) -> str:
    """Generate a unified diff between the reply fields of two run hashes.

    Raises ValueError if either hash is not found.
    """
    left = get_run_by_hash(left_hash)
    right = get_run_by_hash(right_hash)

    if not left:
        raise ValueError(f"Run hash '{left_hash}' not found in history.")
    if not right:
        raise ValueError(f"Run hash '{right_hash}' not found in history.")

    left_lines = (left["reply"] or "").splitlines(keepends=True)
    right_lines = (right["reply"] or "").splitlines(keepends=True)

    diff = difflib.unified_diff(
        left_lines,
        right_lines,
        fromfile=f"{left_hash} (reply)",
        tofile=f"{right_hash} (reply)",
    )
    return "".join(diff)


def delete_work(item_id: int) -> bool:
    if not DB_PATH.exists():
        return False
    conn = _connect()
    try:
        _ensure_executions(conn)
        row = conn.execute("SELECT prompt_hash FROM executions WHERE id = ?", (item_id,)).fetchone()
        if not row:
            return False
        digest = row["prompt_hash"]
        conn.execute("DELETE FROM executions WHERE id = ?", (item_id,))
        leftover = conn.execute(
            "SELECT COUNT(*) FROM executions WHERE prompt_hash = ?", (digest,)
        ).fetchone()[0]
        if leftover == 0:
            tables = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if "prompt_versions" in tables:
                conn.execute("DELETE FROM prompt_versions WHERE run_hash = ?", (digest,))
        conn.commit()
        return True
    finally:
        _release_connection(conn)


def clear_works() -> int:
    if not DB_PATH.exists():
        return 0
    conn = _connect()
    try:
        _ensure_executions(conn)
        cur = conn.execute("DELETE FROM executions")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "prompt_versions" in tables:
            conn.execute("DELETE FROM prompt_versions")
        conn.commit()
        return int(cur.rowcount)
    finally:
        _release_connection(conn)


def export_work(item: dict, fmt: str) -> tuple[bytes, str, str]:
    fmt = (fmt or "markdown").strip().lower()
    if fmt in {"md", "text", "txt"}:
        fmt = "markdown"
    question = item.get("task") or ""
    answer = item.get("answer") or ""
    models = ", ".join(item.get("models") or [])
    intent = item.get("intent") or ""
    workflow = item.get("workflow") or ""
    ts = item.get("timestamp") or ""
    tokens = item.get("tokens") or {}
    cost = item.get("estimated_cost")
    cost_line = f"{cost:.2f}" if isinstance(cost, (int, float)) else "n/a"
    slug = f"assure-answer-{item.get('id') or 'work'}"
    if fmt == "markdown":
        body = (
            f"# {question}\n\n"
            f"{answer or '_Answer was not stored._'}\n\n"
            f"- Models: {models}\n"
            f"- Intent: {intent}\n"
            f"- Workflow: {workflow}\n"
            f"- Tokens: {tokens.get('input', 0)} in + {tokens.get('output', 0)} out "
            f"= {tokens.get('total', 0)}\n"
            f"- Cost est. USD: {cost_line}\n"
            f"- When: {ts}\n"
        )
        return body.encode("utf-8"), f"{slug}.md", "text/markdown; charset=utf-8"
    if fmt == "html":
        body = (
            "<!DOCTYPE html><html><head><meta charset='utf-8'><title>Assure</title>"
            "<style>body{font-family:sans-serif;max-width:720px;margin:32px auto;color:#2D3748}"
            "h1{color:#1A4B8C} .meta{color:#5c6b7a;font-size:14px}</style></head><body>"
            f"<h1>{html.escape(question)}</h1>"
            f"<pre style='white-space:pre-wrap'>{html.escape(answer or 'Answer was not stored.')}</pre>"
            f"<p class='meta'>Models: {html.escape(models)} · Intent: {html.escape(intent)} · "
            f"Workflow: {html.escape(workflow)} · Tokens {tokens.get('total', 0)} · "
            f"${html.escape(cost_line)} · {html.escape(ts)}</p></body></html>"
        )
        return body.encode("utf-8"), f"{slug}.html", "text/html; charset=utf-8"
    if fmt == "prompty":
        body = (
            "---\n"
            "name: Assure previous work\n"
            "authors:\n  - Assure\n"
            f"model:\n  api: chat\n"
            f"metadata:\n  intent: {intent}\n  workflow: {workflow}\n"
            "---\n\n"
            f"{question}\n\n"
            f"{answer}\n"
        )
        return body.encode("utf-8"), f"{slug}.prompty", "text/plain; charset=utf-8"
    if fmt == "pdf":
        text = (
            f"{question}\n\n{answer or 'Answer was not stored.'}\n\n"
            f"Models: {models}\nIntent: {intent}\nWorkflow: {workflow}\n"
            f"Tokens: {tokens.get('total', 0)}\nCost est. USD: {cost_line}\nWhen: {ts}\n"
        )
        return _simple_pdf(question, text), f"{slug}.pdf", "application/pdf"
    if fmt == "plain":
        body = f"{question}\n\n{answer}\n"
        return body.encode("utf-8"), f"{slug}.txt", "text/plain; charset=utf-8"
    raise ValueError(fmt)


def _simple_pdf(title: str, body: str) -> bytes:
    """Minimal PDF 1.4. No WeasyPrint. ASCII Helvetica only."""

    def pdf_str(value: str) -> str:
        cleaned = "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in (value or ""))
        return cleaned.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    wrapped: list[str] = []
    for raw in (body or "").splitlines() or [""]:
        line = raw if raw else " "
        while len(line) > 88:
            wrapped.append(line[:88])
            line = line[88:]
        wrapped.append(line)
    wrapped = wrapped[:90]
    y = 770
    cmds = ["BT", "/F1 11 Tf", "14 TL", "48 770 Td"]
    for i, line in enumerate(wrapped):
        if i:
            cmds.append("T*")
        cmds.append(f"({pdf_str(line)}) Tj")
    cmds.append("ET")
    stream = "\n".join(cmds).encode("latin-1", "replace")
    objects = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj\n",
        b"4 0 obj << /Length "
        + str(len(stream)).encode()
        + b" >> stream\n"
        + stream
        + b"\nendstream endobj\n",
        b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(out))
        out.extend(obj)
    xref = len(out)
    out.extend(f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode())
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode())
    out.extend(
        f"trailer << /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(out)


def usage_summary(*, days: int = 30) -> dict:
    """Aggregate local executions. Latency is not stored."""
    n_days = max(1, int(days or 30))
    empty = {
        "days": n_days,
        "runs": 0,
        "with_reply": 0,
        "tokens": 0,
        "estimated_cost": None,
        "models": [],
        "latency": None,
    }
    if not DB_PATH.exists():
        return empty
    cutoff = (datetime.now(timezone.utc) - timedelta(days=n_days)).isoformat(timespec="seconds")
    conn = _connect()
    try:
        _ensure_executions(conn)
        rows = conn.execute(
            """
            SELECT target_ai, COUNT(*) AS runs,
                   SUM(CASE WHEN COALESCE(reply_chars, 0) > 0 THEN 1 ELSE 0 END) AS with_reply,
                   SUM(COALESCE(total_tokens, 0)) AS tokens,
                   SUM(estimated_cost) AS estimated_cost
            FROM executions
            WHERE timestamp >= ?
            GROUP BY target_ai
            ORDER BY runs DESC
            """,
            (cutoff,),
        ).fetchall()
        models = []
        runs = 0
        with_reply = 0
        tokens = 0
        cost = 0.0
        cost_any = False
        for row in rows:
            r = int(row["runs"] or 0)
            w = int(row["with_reply"] or 0)
            t = int(row["tokens"] or 0)
            c = row["estimated_cost"]
            runs += r
            with_reply += w
            tokens += t
            if c is not None:
                cost_any = True
                cost += float(c)
            models.append(
                {
                    "model": row["target_ai"] or "(unknown)",
                    "runs": r,
                    "with_reply": w,
                    "tokens": t,
                    "estimated_cost": None if c is None else round(float(c), 6),
                    "reply_rate": round(w / r, 4) if r else None,
                }
            )
        return {
            "days": n_days,
            "runs": runs,
            "with_reply": with_reply,
            "tokens": tokens,
            "estimated_cost": round(cost, 6) if cost_any else None,
            "models": models,
            "latency": None,
        }
    finally:
        _release_connection(conn)
