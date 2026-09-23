"""Database access core (PostgreSQL via db/pg_compat) and the opt-in usage log.

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
import sys
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

try:
    from .paths import user_data_dir
except ImportError:
    from paths import user_data_dir


def _resolve_db_path() -> Path:
    """The data-directory key. There is no SQLite file behind it.

    The database is PostgreSQL (``DATABASE_URL``, see ``db/pg_compat``). This
    path survives for two reasons: its *parent* is the instance-local data
    directory (upload staging, OMP artifact mirror, logs) — set with
    ``ASSURE_DATA_DIR`` — and the test-suite still points each test at a
    distinct ``DATABASE_PATH`` for isolation, which the PostgreSQL backend maps
    to a schema of its own. Nothing is ever created at the path itself.
    """
    override = (os.environ.get("DATABASE_PATH") or "").strip()
    if override:
        return Path(override)
    data_dir = (os.environ.get("ASSURE_DATA_DIR") or "").strip()
    if data_dir:
        return Path(data_dir) / "history.sqlite"
    return user_data_dir() / "history.sqlite"


def _using_postgres() -> bool:
    """Always true for a configured server; raises a clear error when it is not."""
    try:
        from .db.pg_compat import is_postgres
    except ImportError:
        from db.pg_compat import is_postgres
    return is_postgres()


def _db_present() -> bool:
    """Whether there is a database to read at all: the server is the database."""
    return True


DB_PATH = _resolve_db_path()

#: The connection an open `db_scope()` owns, per thread. `get_db()` joins it
#: instead of opening another, which is what keeps a standalone unit of work on one
#: connection the way a request is, and what stops a pipeline from taking the whole
#: pool. See db_scope's docstring for the measurement.
_scope_state = threading.local()


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    """Kept for callers that still import it; PostgreSQL has no pragmas to apply.

    Durability, busy handling and foreign-key enforcement are server-side
    properties of PostgreSQL. The compat connection answers ``PRAGMA`` with an
    empty result, so even a stray call is harmless.
    """
    return None


def _caller_site() -> str:
    """`file:line:function` of the first frame outside this module.

    The gauge's site tag. `db_open_connections` answers how many connections are
    held; this answers by whom, from the health payload alone, without a stack
    sample from the process that is holding them. One frame walk per connection
    handed out, against a SQLAlchemy checkout — the cost is not measurable next to
    the thing it describes.
    """
    frame = sys._getframe(1)
    while frame is not None and frame.f_code.co_filename == __file__:
        frame = frame.f_back
    if frame is None:
        return "history.unknown"
    code = frame.f_code
    return f"{os.path.basename(code.co_filename)}:{frame.f_lineno}:{code.co_name}"


def _new_connection() -> sqlite3.Connection:
    """A pooled PostgreSQL connection wrapped in the sqlite3-shaped handle.

    ``DATABASE_URL`` is mandatory. There is no file-based fallback: a database
    on one instance's disk is the single-writer, single-host state that stops
    the app from running as more than one replica, so its absence is a
    configuration error reported at the first query, not a silent downgrade.
    The pool is the choke point and the caller is the holder, so the handle is
    tagged with the caller for the open-connection gauge.
    """
    try:
        from .db.pg_compat import checkout, is_postgres
    except ImportError:
        from db.pg_compat import checkout, is_postgres
    if not is_postgres():
        raise RuntimeError(
            "DATABASE_URL is not set to a PostgreSQL DSN. The database is PostgreSQL only: "
            "set DATABASE_URL=postgresql://user:pass@host:5432/db "
            "(docker compose -f docker-compose.dev.yml up -d provides one locally)."
        )
    conn = checkout(str(DB_PATH))
    note_connection_open(conn, site=_caller_site())
    return conn  # type: ignore[return-value]


def _rollback_quietly(conn: sqlite3.Connection | None) -> None:
    """Discard whatever statement the connection has open. Never raises.

    An uncommitted statement is what holds SQLite's write lock, so this is the one
    line that stops a failed write from stalling the next one: whether that next
    writer is a different connection (waiting on the lock) or this same connection
    (handed to the pool with the failed statement still on it).
    """
    if conn is None:
        return
    try:
        conn.rollback()
    except Exception:
        pass


def _release_connection(conn: sqlite3.Connection | None) -> None:
    """Hand the connection back to its pool.

    One half of ``db_open_connections``: a connection leaves the gauge only once
    it is really back. ``close()`` on the compat handle returns the raw
    connection to the psycopg pool; a release that fails keeps it counted and
    names it in the service log.
    """
    if conn is None:
        return
    try:
        conn.close()
    except Exception as exc:
        logging.getLogger("assure").error(
            "PostgreSQL connection not released — close failed (%s: %s); "
            "db_open_connections still counts it as open",
            exc.__class__.__name__,
            exc,
        )
        return
    note_connection_released(conn)


def _release_direct_connection(conn: sqlite3.Connection | None) -> None:
    """Roll a connection's open statement back, then release it quietly.

    The single exit for every connection this process opens on its own account —
    the audit trail, the metrics collector, the /health probes, the compliance
    export, and `borrowed_connection` below. A caller may swallow the failure of
    the work, which is often the right policy for it; what it cannot swallow is a
    connection left open holding the write lock of the statement that failed.
    """
    _rollback_quietly(conn)
    _release_connection(conn)


@contextmanager
def borrowed_connection() -> Iterator[sqlite3.Connection]:
    """A connection that is rolled back and given back on every path.

    The connection-leakage family's common cause, in one place: `connect →
    execute → commit → close` written as a single `try` whose handler swallows
    leaves the connection open whenever anything between the connect and the close
    raises — and an open connection with an uncommitted statement holds SQLite's
    write lock, so the next writer fails, or inside one request is silently lost.

    The rollback is unconditional rather than only on the failure path: a caller
    that wanted the write commits inside the block, where rollback is then a no-op
    and nothing is left behind for the next writer to trip over.
    """
    conn = _new_connection()
    try:
        yield conn
    finally:
        _release_direct_connection(conn)


@contextmanager
def db_scope() -> Iterator[sqlite3.Connection]:
    """A connection that belongs to the block, in either execution mode.

    Inside a request this is the request's own handle — `get_db()`'s `g.db`,
    released at teardown by `close_db` — so a route's behaviour does not change:
    one connection for the whole request, every nested `get_db()` joining it.

    Outside one there is no teardown, and that is the whole defect. `get_db()`
    handed out a NEW pooled checkout per call that nothing returns. Measured on a
    standalone `run_draft_pipeline` (a probe with stubbed models, no network):

        after one start   db_open_connections=20  pool_holders=20  59 open fds
        after the second  db_open_connections=35  89 open fds   75s spent
        after the third   db_open_connections=50  119 open fds  75s spent

    20 is pool_size 5 + max_overflow 15 — the entire pool, taken by one pipeline
    start. Every later `get_db()` then waited out pool_timeout, `_new_connection`
    swallowed that TimeoutError and opened a direct connection instead, and the
    process sat at 0% CPU holding both. That is the state a stub harness and the
    pipeline were each found in, and the 119-descriptor count is what it looks
    like from outside.

    So the scope gives a standalone caller what a request already has: one
    connection for the whole unit of work, which nested `get_db()` calls join
    through the thread-local below, released when the block ends — including when
    the generator holding it is closed early.
    """
    existing = getattr(_scope_state, "connection", None)
    if existing is not None:
        # Nested scope: the outermost one owns the connection and its release.
        yield existing
        return
    try:
        from flask import has_app_context

        if has_app_context():
            # Nothing to install: inside a request get_db() already answers with
            # g.db, and the teardown closes it.
            yield get_db()
            return
    except ImportError:
        pass
    conn = _new_connection()
    _scope_state.connection = conn
    try:
        yield conn
    finally:
        _scope_state.connection = None
        _release_direct_connection(conn)


def get_db() -> sqlite3.Connection:
    """Request-scoped SQLite handle in Flask; standalone connection elsewhere.

    Third answer, between those two: inside an open `db_scope()` this joins the
    connection that scope owns rather than opening another. A request already had
    that property through `g.db`; outside one this is what keeps a unit of work on
    a single connection instead of one per call — which is the defect db_scope
    documents.
    """
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
    scoped = getattr(_scope_state, "connection", None)
    if scoped is not None:
        return scoped
    return _new_connection()


def close_db(e=None) -> None:
    try:
        from flask import g, has_app_context

        if has_app_context():
            db = g.pop("db", None)
            if db is not None:
                # Rolled back as well as released: a request that died between an
                # execute and its commit hands back a connection whose statement
                # would otherwise outlive the request that made it.
                _release_direct_connection(db)
    except ImportError:
        pass


# The gauge for connections taken and not returned. Imported here rather than at
# the top of the module on purpose: db/__init__.py imports db/connection.py, which
# imports `_apply_pragmas`, `_new_connection` and `get_db` from *this* module, so a
# top-of-file import would re-enter this module — via db/__init__ — before those
# three names exist. Below them, both orders resolve: history imported first, or
# db.connection imported first.
try:
    from .db.open_connections import note_connection_open, note_connection_released
except ImportError:  # the CLI's flat layout puts prompt_matrix/ on sys.path
    from db.open_connections import note_connection_open, note_connection_released


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
    if not _db_present():
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
    if not _db_present():
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
    if not _db_present():
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
    if not _db_present():
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
    if not _db_present():
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
    if not _db_present():
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
