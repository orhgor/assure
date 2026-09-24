"""PostgreSQL behind the sqlite3-shaped connection the rest of the app already uses.

The application talks to its database through ``history.get_db()`` /
``history.db_scope()`` and treats the handle as a ``sqlite3.Connection``:
``conn.execute(sql, params)`` with ``?`` placeholders, cursors with
``fetchone``/``fetchall``/``rowcount``/``lastrowid``, ``sqlite3.Row`` rows
addressable by index *and* by column name, and ``except sqlite3.IntegrityError``
around upserts. Thirty-four modules hold that contract, and a rewrite of every
repository onto SQLAlchemy Core would be the largest change in the repo for no
product gain. So this module keeps the contract and changes the engine.

``Connection`` wraps a psycopg 3 connection and presents that same surface:

- ``?`` placeholders become ``%s`` (outside string literals); a literal ``%``
  is escaped when parameters are present.
- SQLite dialect is rewritten at statement level (``INSERT OR IGNORE``,
  ``INSERT OR REPLACE``, ``PRAGMA table_info``, ``sqlite_master``,
  ``AUTOINCREMENT``, ``DATETIME``, ``CREATE VIEW IF NOT EXISTS`` …). Statements
  that are already portable pass through untouched; translation is cached per
  SQL string.
- SQLite's built-in functions the code relies on (``datetime('now', …)``,
  ``json_extract``, ``hex(randomblob(n))``) are provided as PostgreSQL functions
  installed once per database (``ensure_compat_functions``), so the SQL text
  stays identical across both backends.
- Rows come back as :class:`Row`, a ``tuple`` that also answers ``row["col"]``
  and ``dict(row)``, with ``timestamp`` values rendered the way SQLite renders
  ``CURRENT_TIMESTAMP`` (``YYYY-MM-DD HH:MM:SS``) so callers that parse or
  compare those strings see no difference.
- psycopg errors are re-raised as subclasses of the matching ``sqlite3``
  exception class, so every existing ``except sqlite3.IntegrityError`` keeps
  catching what it caught.
- Every statement runs inside a SAVEPOINT when a transaction is open, because
  SQLite lets a caller catch a failed statement and carry on in the same
  transaction, while PostgreSQL would abort the whole transaction. The
  savepoint restores SQLite's statement-level failure semantics.

Selection: ``DATABASE_URL`` (``postgres://`` / ``postgresql://``) is required;
there is no other backend. ``ASSURE_PG_SCHEMA`` picks the schema (default
``public``). With
``ASSURE_PG_SCHEMA_FROM_DB_PATH=1`` the schema is derived from ``DATABASE_PATH``
instead — that is how the existing test-suite, which points every test at its
own temporary SQLite file for isolation, gets the same isolation on one shared
PostgreSQL server: one schema per temp path.
"""

from __future__ import annotations

import datetime as _dt
import decimal
import functools
import hashlib
import logging
import os
import re
import sqlite3
import threading
import time
from typing import Any, Iterable, Iterator, Sequence

_log = logging.getLogger("assure")

_PG_PREFIXES = ("postgres://", "postgresql://", "postgresql+psycopg://")


# --------------------------------------------------------------------------- #
# Selection
# --------------------------------------------------------------------------- #


def database_url() -> str | None:
    """The PostgreSQL DSN when the app is configured for it, else ``None``."""
    raw = (os.environ.get("DATABASE_URL") or "").strip()
    if not raw:
        return None
    if not raw.lower().startswith(_PG_PREFIXES):
        return None
    if raw.lower().startswith("postgresql+psycopg://"):
        raw = "postgresql://" + raw.split("://", 1)[1]
    return raw


def is_postgres() -> bool:
    return database_url() is not None


def sqlalchemy_url() -> str | None:
    """The same DSN in the form SQLAlchemy / Celery's ``db+`` backend wants."""
    url = database_url()
    if url is None:
        return None
    return "postgresql+psycopg://" + url.split("://", 1)[1]


def schema_from_db_path_enabled() -> bool:
    return os.environ.get("ASSURE_PG_SCHEMA_FROM_DB_PATH", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def current_schema(db_path: str | None = None) -> str:
    """The schema this process should work in.

    ``ASSURE_PG_SCHEMA`` wins. Otherwise, with ``ASSURE_PG_SCHEMA_FROM_DB_PATH``
    on, the schema is derived from the SQLite path the caller resolved (the
    test-suite's per-test temp file) or from ``DATABASE_PATH``; else ``public``.
    """
    explicit = (os.environ.get("ASSURE_PG_SCHEMA") or "").strip()
    if explicit:
        return _safe_ident(explicit)
    if schema_from_db_path_enabled():
        # The environment wins over the caller's cached path: the test-suite
        # points a test at its schema with DATABASE_PATH, and every module that
        # caches a path at import (history.DB_PATH, lib.logger.DB_PATH) must
        # land in that same schema.
        path = (os.environ.get("DATABASE_PATH") or db_path or "").strip()
        if path:
            digest = hashlib.sha1(path.encode("utf-8")).hexdigest()[:16]
            return f"db_{digest}"
    return "public"


def _safe_ident(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError(f"unsafe schema name: {name!r}")
    return name


# --------------------------------------------------------------------------- #
# Exceptions: subclasses of the sqlite3 family so existing handlers still bite
# --------------------------------------------------------------------------- #


class OperationalError(sqlite3.OperationalError):
    """psycopg operational / undefined-object errors, sqlite3-catchable."""


class IntegrityError(sqlite3.IntegrityError):
    """psycopg integrity violations (unique, FK, not-null, check)."""


class ProgrammingError(sqlite3.ProgrammingError):
    """psycopg syntax / programming errors."""


class DatabaseError(sqlite3.DatabaseError):
    """Anything else psycopg raised."""


def _translate_error(exc: Exception) -> sqlite3.Error:
    import psycopg
    from psycopg import errors as pg_errors

    msg = str(exc).strip()
    if isinstance(exc, pg_errors.IntegrityError):
        return IntegrityError(msg)
    if isinstance(exc, pg_errors.UndefinedTable):
        # sqlite says "no such table: x"; several callers match on that phrase.
        m = re.search(r'relation "([^"]+)" does not exist', msg)
        return OperationalError(f"no such table: {m.group(1) if m else '?'}")
    if isinstance(exc, pg_errors.UndefinedColumn):
        m = re.search(r'column "?([^" ]+)"? ', msg)
        return OperationalError(f"no such column: {m.group(1) if m else '?'}")
    if isinstance(exc, pg_errors.DuplicateTable):
        return OperationalError(f"table already exists: {msg}")
    if isinstance(exc, (pg_errors.OperationalError, pg_errors.InternalError)):
        return OperationalError(msg)
    if isinstance(exc, pg_errors.ProgrammingError):
        return ProgrammingError(msg)
    if isinstance(exc, psycopg.Error):
        return DatabaseError(msg)
    return DatabaseError(msg)


# --------------------------------------------------------------------------- #
# Rows
# --------------------------------------------------------------------------- #


class Row(tuple):
    """A ``sqlite3.Row`` stand-in: a tuple that also indexes by column name."""

    # No __slots__: CPython forbids non-empty slots on tuple subclasses, so the
    # two attributes live in the instance dict.

    def __new__(cls, values: Iterable[Any], keys: Sequence[str]):
        obj = super().__new__(cls, values)
        obj._keys = tuple(keys)
        obj._index = None
        return obj

    def keys(self) -> list[str]:
        return list(self._keys)

    def _lookup(self, key: str) -> int:
        idx = self._index
        if idx is None:
            idx = {}
            for i, k in enumerate(self._keys):
                idx.setdefault(k, i)
                idx.setdefault(k.lower(), i)
            self._index = idx
        try:
            return idx[key]
        except KeyError:
            try:
                return idx[key.lower()]
            except KeyError:
                raise IndexError(f"No item with that key: {key!r}") from None

    def __getitem__(self, key):  # type: ignore[override]
        if isinstance(key, str):
            return tuple.__getitem__(self, self._lookup(key))
        return tuple.__getitem__(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except (IndexError, KeyError):
            return default

    def __contains__(self, key) -> bool:  # type: ignore[override]
        if isinstance(key, str):
            try:
                self._lookup(key)
                return True
            except IndexError:
                return False
        return tuple.__contains__(self, key)

    def items(self):
        return [(k, tuple.__getitem__(self, i)) for i, k in enumerate(self._keys)]


def _coerce_out(value: Any) -> Any:
    """Render a PostgreSQL value the way the app saw it coming out of SQLite."""
    if value is None or isinstance(value, (str, int, float, bytes, bool)):
        return value
    if isinstance(value, _dt.datetime):
        if value.tzinfo is not None:
            value = value.astimezone(_dt.timezone.utc).replace(tzinfo=None)
        if value.microsecond:
            return value.strftime("%Y-%m-%d %H:%M:%S.%f")
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, _dt.date):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    if isinstance(value, memoryview):
        return value.tobytes()
    return value


def _coerce_in(params: Any) -> Any:
    """Parameter values the way PostgreSQL's typed binding needs them.

    SQLite stores ``True`` as 1 in an INTEGER column; PostgreSQL rejects a
    boolean bound against an integer column, so booleans go over as ints.
    """
    if params is None:
        return None
    if isinstance(params, dict):
        return {k: _coerce_one(v) for k, v in params.items()}
    return tuple(_coerce_one(v) for v in params)


def _coerce_one(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    return value


# --------------------------------------------------------------------------- #
# SQL translation
# --------------------------------------------------------------------------- #

_PRAGMA_TABLE_INFO_SQL = """
SELECT (c.ordinal_position - 1) AS cid,
       c.column_name AS name,
       upper(c.data_type) AS type,
       CASE WHEN c.is_nullable = 'NO' THEN 1 ELSE 0 END AS notnull,
       c.column_default AS dflt_value,
       COALESCE(pk.pos, 0) AS pk
FROM information_schema.columns c
LEFT JOIN (
    SELECT kcu.column_name, kcu.ordinal_position AS pos
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
      ON tc.constraint_name = kcu.constraint_name
     AND tc.table_schema = kcu.table_schema
    WHERE tc.constraint_type = 'PRIMARY KEY'
      AND tc.table_schema = current_schema()
      AND tc.table_name = '{table}'
) pk ON pk.column_name = c.column_name
WHERE c.table_schema = current_schema()
  AND c.table_name = '{table}'
ORDER BY c.ordinal_position
"""

_SQLITE_MASTER_SUBQUERY = (
    "(SELECT table_name AS name, "
    "CASE table_type WHEN 'BASE TABLE' THEN 'table' ELSE 'view' END AS type, "
    "table_name AS tbl_name, '' AS sql "
    "FROM information_schema.tables WHERE table_schema = current_schema()) AS sqlite_master"
)

_EMPTY_RESULT_SQL = "SELECT 1 WHERE FALSE"

_RE_PRAGMA_TABLE_INFO = re.compile(r"^\s*PRAGMA\s+table_info\s*\(\s*['\"]?(\w+)['\"]?\s*\)\s*;?\s*$", re.I)
_RE_PRAGMA = re.compile(r"^\s*PRAGMA\b", re.I)
_RE_VACUUM = re.compile(r"^\s*VACUUM\b", re.I)
_RE_INSERT_OR_IGNORE = re.compile(r"^(\s*)INSERT\s+OR\s+IGNORE\s+INTO\b", re.I)
_RE_INSERT_OR_REPLACE = re.compile(
    r"^\s*(?:INSERT\s+OR\s+REPLACE|REPLACE)\s+INTO\s+(\w+)\s*\(([^)]*)\)", re.I
)
_RE_CREATE_VIEW_INE = re.compile(r"CREATE\s+VIEW\s+IF\s+NOT\s+EXISTS", re.I)
_RE_DDL = re.compile(r"^\s*(CREATE\s+TABLE|ALTER\s+TABLE)\b", re.I)
_RE_AUTOINC = re.compile(r"\bAUTOINCREMENT\b", re.I)
_RE_IFNULL = re.compile(r"\bIFNULL\s*\(", re.I)
_RE_SQLITE_MASTER = re.compile(r"\bsqlite_master\b", re.I)
_RE_RETURNING = re.compile(r"\bRETURNING\b", re.I)
_RE_INSERT_TABLE = re.compile(r"^\s*INSERT\s+INTO\s+(\w+)", re.I)
_RE_GROUP_CONCAT = re.compile(r"\bGROUP_CONCAT\s*\(", re.I)
# SQLite's ``IS`` / ``IS NOT`` compare any two values null-safely; PostgreSQL's
# ``IS`` only accepts NULL/TRUE/FALSE, and ``IS NOT DISTINCT FROM`` is the
# null-safe equality it does have.
_RE_IS_PARAM = re.compile(r"\bIS\s+NOT\s+\?", re.I)
_RE_IS_EQ_PARAM = re.compile(r"\bIS\s+\?", re.I)
# Explicit transaction starts: psycopg has already opened the transaction on
# the first statement, and PostgreSQL has no IMMEDIATE/EXCLUSIVE modes — the
# UNIQUE constraints and row locks the code relies on hold regardless.
_RE_BEGIN = re.compile(r"^\s*BEGIN(\s+(IMMEDIATE|EXCLUSIVE|DEFERRED|TRANSACTION))*\s*;?\s*$", re.I)
_RE_COMMIT = re.compile(r"^\s*(COMMIT|END)(\s+TRANSACTION)?\s*;?\s*$", re.I)
_RE_ROLLBACK = re.compile(r"^\s*ROLLBACK(\s+TRANSACTION)?\s*;?\s*$", re.I)



_CONSTRAINT_STARTS = ("PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT")
_RE_TYPE_DATETIME = re.compile(r"^DATETIME\b", re.I)
_RE_TYPE_TIMESTAMP = re.compile(r"^TIMESTAMP\b(?!\s*\()", re.I)
_RE_TYPE_REAL = re.compile(r"^REAL\b", re.I)
_RE_TYPE_BLOB = re.compile(r"^BLOB\b", re.I)
_RE_TYPE_INT_PK = re.compile(r"^INTEGER\s+PRIMARY\s+KEY(\s+AUTOINCREMENT)?\b", re.I)
_RE_ADD_COLUMN = re.compile(r"(ADD\s+COLUMN\s+\"?\w+\"?\s+)(.*)$", re.I | re.S)


def _rewrite_type_spec(spec: str) -> str:
    """Map the SQLite type (and its inline PK) at the head of a column spec."""
    spec = _RE_TYPE_INT_PK.sub("INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY", spec)
    spec = _RE_TYPE_DATETIME.sub("TIMESTAMP(0)", spec)
    spec = _RE_TYPE_TIMESTAMP.sub("TIMESTAMP(0)", spec)
    spec = _RE_TYPE_REAL.sub("DOUBLE PRECISION", spec)
    spec = _RE_TYPE_BLOB.sub("BYTEA", spec)
    spec = _RE_AUTOINC.sub("", spec)
    return spec


def _split_top_level(body: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    buf: list[str] = []
    quote: str | None = None
    for ch in body:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    parts.append("".join(buf))
    return parts


def _rewrite_ddl_types(text: str) -> str:
    """Rewrite column *types* in CREATE TABLE / ALTER TABLE ADD COLUMN.

    Types are rewritten by position — the token after the column name — never
    by bare keyword search, because a column may itself be called ``timestamp``
    (``token_ledger_entries.timestamp``) and must keep its name.
    """
    m = _RE_ADD_COLUMN.search(text)
    if m:
        return text[: m.start(2)] + _rewrite_type_spec(m.group(2))
    start = text.find("(")
    if start < 0:
        return text
    depth = 0
    end = -1
    for i in range(start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end < 0:
        return text
    body = text[start + 1 : end]
    out: list[str] = []
    for seg in _split_top_level(body):
        stripped = seg.strip()
        if not stripped:
            out.append(seg)
            continue
        leading = seg[: len(seg) - len(seg.lstrip())]
        trailing = seg[len(seg.rstrip()) :]
        first = stripped.split(None, 1)
        if first[0].upper() in _CONSTRAINT_STARTS or len(first) == 1:
            out.append(seg)
            continue
        name, spec = first[0], first[1]
        out.append(f"{leading}{name} {_rewrite_type_spec(spec)}{trailing}")
    return text[: start + 1] + ",".join(out) + text[end:]


def _rewrite_placeholders(sql: str, escape_percent: bool) -> str:
    """``?`` → ``%s`` outside quotes; optionally ``%`` → ``%%`` outside quotes."""
    out: list[str] = []
    i = 0
    n = len(sql)
    quote: str | None = None
    while i < n:
        ch = sql[i]
        if quote:
            # psycopg's placeholder scanner does not understand SQL quoting:
            # a literal % must be doubled even inside a '...' literal whenever
            # parameters are passed (LIKE '%REDHAT%' with a bound value).
            out.append("%%" if (ch == "%" and escape_percent) else ch)
            if ch == quote:
                # '' inside a '...' literal is an escaped quote, not the end.
                if quote == "'" and i + 1 < n and sql[i + 1] == "'":
                    out.append("'")
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            out.append(ch)
        elif ch == "?":
            out.append("%s")
        elif ch == "%" and escape_percent:
            out.append("%%")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


class _Translated:
    """One SQL string after dialect rewriting, plus what the connection must finish."""

    __slots__ = ("sql", "upsert_table", "upsert_columns", "insert_table", "is_insert")

    def __init__(
        self,
        sql: str,
        *,
        upsert_table: str | None = None,
        upsert_columns: tuple[str, ...] | None = None,
        insert_table: str | None = None,
    ) -> None:
        self.sql = sql
        self.upsert_table = upsert_table
        self.upsert_columns = upsert_columns
        self.insert_table = insert_table
        self.is_insert = insert_table is not None or upsert_table is not None


@functools.lru_cache(maxsize=4096)
def translate(sql: str, has_params: bool) -> _Translated:
    """Rewrite one SQLite statement for PostgreSQL. Pure; cached per text."""
    text = sql.strip()

    m = _RE_PRAGMA_TABLE_INFO.match(text)
    if m:
        return _Translated(_PRAGMA_TABLE_INFO_SQL.format(table=m.group(1)))
    if _RE_PRAGMA.match(text):
        if re.match(r"^\s*PRAGMA\s+database_list", text, re.I):
            return _Translated("SELECT 0 AS seq, 'main' AS name, '' AS file")
        return _Translated(_EMPTY_RESULT_SQL)
    if _RE_VACUUM.match(text):
        # VACUUM cannot run inside a transaction block in PostgreSQL and is
        # autovacuum's job there anyway.
        return _Translated(_EMPTY_RESULT_SQL)
    if _RE_BEGIN.match(text):
        return _Translated(_EMPTY_RESULT_SQL)

    upsert_table: str | None = None
    upsert_columns: tuple[str, ...] | None = None
    insert_table: str | None = None

    m = _RE_INSERT_OR_REPLACE.match(text)
    if m:
        upsert_table = m.group(1)
        upsert_columns = tuple(c.strip().strip('"') for c in m.group(2).split(",") if c.strip())
        text = re.sub(
            r"^(\s*)(?:INSERT\s+OR\s+REPLACE|REPLACE)\s+INTO\b", r"\1INSERT INTO", text, count=1, flags=re.I
        )
    elif _RE_INSERT_OR_IGNORE.match(text):
        text = _RE_INSERT_OR_IGNORE.sub(r"\1INSERT INTO", text, count=1)
        text = _append_conflict_clause(text, "ON CONFLICT DO NOTHING")

    m = _RE_INSERT_TABLE.match(text)
    if m and upsert_table is None:
        insert_table = m.group(1)

    if _RE_DDL.match(text):
        text = _rewrite_ddl_types(text)
    text = _RE_CREATE_VIEW_INE.sub("CREATE OR REPLACE VIEW", text)
    text = _RE_IFNULL.sub("COALESCE(", text)
    text = _RE_IS_PARAM.sub("IS DISTINCT FROM ?", text)
    text = _RE_IS_EQ_PARAM.sub("IS NOT DISTINCT FROM ?", text)
    text = _RE_GROUP_CONCAT.sub("STRING_AGG(", text)
    if _RE_SQLITE_MASTER.search(text):
        text = _RE_SQLITE_MASTER.sub(_SQLITE_MASTER_SUBQUERY, text)

    text = _rewrite_placeholders(text, escape_percent=has_params)
    return _Translated(
        text,
        upsert_table=upsert_table,
        upsert_columns=upsert_columns,
        insert_table=insert_table if upsert_table is None else None,
    )


def _append_conflict_clause(sql: str, clause: str) -> str:
    """Put an ON CONFLICT clause before a RETURNING clause (or at the end)."""
    m = _RE_RETURNING.search(sql)
    body = sql.rstrip().rstrip(";")
    if m:
        return body[: m.start()].rstrip() + " " + clause + " " + body[m.start() :]
    return body + " " + clause


# --------------------------------------------------------------------------- #
# Compatibility functions installed in the database
# --------------------------------------------------------------------------- #

COMPAT_FUNCTIONS_SQL = r"""
CREATE OR REPLACE FUNCTION public.datetime(t text) RETURNS timestamp
LANGUAGE sql STABLE AS $$
    SELECT CASE
        WHEN lower(t) = 'now' THEN (now() AT TIME ZONE 'UTC')::timestamp(0)
        ELSE t::timestamp
    END
$$;

CREATE OR REPLACE FUNCTION public.datetime(t text, modifier text) RETURNS timestamp
LANGUAGE sql STABLE AS $$
    SELECT public.datetime(t) + modifier::interval
$$;

CREATE OR REPLACE FUNCTION public.datetime(t timestamp, modifier text) RETURNS timestamp
LANGUAGE sql IMMUTABLE AS $$
    SELECT t + modifier::interval
$$;

CREATE OR REPLACE FUNCTION public.json_extract(doc text, path text) RETURNS text
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
    parts text[];
BEGIN
    IF doc IS NULL OR path IS NULL THEN
        RETURN NULL;
    END IF;
    parts := string_to_array(regexp_replace(path, '^\$\.?', ''), '.');
    RETURN doc::jsonb #>> parts;
EXCEPTION WHEN others THEN
    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION public.randomblob(n integer) RETURNS bytea
LANGUAGE sql VOLATILE AS $$
    SELECT decode(string_agg(lpad(to_hex((random() * 255)::int), 2, '0'), ''), 'hex')
    FROM generate_series(1, GREATEST(n, 1))
$$;

CREATE OR REPLACE FUNCTION public.hex(b bytea) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
    SELECT upper(encode(b, 'hex'))
$$;
"""

_functions_ready: set[str] = set()
_functions_lock = threading.Lock()


def ensure_compat_functions(pgconn: Any, *, key: str) -> None:
    """Install the SQLite-shaped helper functions once per (process, database)."""
    if key in _functions_ready:
        return
    with _functions_lock:
        if key in _functions_ready:
            return
        # `CREATE OR REPLACE FUNCTION` from two processes at once (gunicorn's
        # workers boot in parallel, the Celery worker alongside) raised
        # "tuple concurrently updated" and killed a web worker on the EC2 first
        # boot (2026-09-24). Serialise on a session advisory lock; the bootstrap
        # connection is autocommit, so lock/unlock are plain statements.
        with pgconn.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(727002)")
            try:
                for attempt in (1, 2):
                    try:
                        cur.execute(COMPAT_FUNCTIONS_SQL)
                        break
                    except Exception as exc:  # the race, if the lock was not enough
                        if attempt == 2 or "concurrently updated" not in str(exc):
                            raise
                        time.sleep(0.2)
            finally:
                cur.execute("SELECT pg_advisory_unlock(727002)")
        pgconn.commit()
        _functions_ready.add(key)


_schemas_ready: set[str] = set()


def ensure_schema(pgconn: Any, schema: str) -> None:
    if schema == "public" or schema in _schemas_ready:
        return
    with _functions_lock:
        if schema in _schemas_ready:
            return
        with pgconn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        pgconn.commit()
        _schemas_ready.add(schema)


# --------------------------------------------------------------------------- #
# Cursor / Connection
# --------------------------------------------------------------------------- #


class Cursor:
    """The slice of ``sqlite3.Cursor`` the app uses, over a psycopg cursor."""

    def __init__(self, conn: "Connection", pgcur: Any, keys: tuple[str, ...]) -> None:
        self._conn = conn
        self._cur = pgcur
        self._keys = keys
        self.lastrowid: int | None = None

    @property
    def description(self):
        return self._cur.description

    @property
    def rowcount(self) -> int:
        rc = self._cur.rowcount
        return -1 if rc is None else rc

    def _wrap(self, raw: Any) -> Row | None:
        if raw is None:
            return None
        return Row((_coerce_out(v) for v in raw), self._keys)

    def fetchone(self) -> Row | None:
        try:
            raw = self._cur.fetchone()
        except Exception as exc:  # no result set (e.g. after DDL)
            if "no result" in str(exc).lower() or "doesn't return rows" in str(exc).lower():
                return None
            raise
        return self._wrap(raw)

    def fetchall(self) -> list[Row]:
        try:
            rows = self._cur.fetchall()
        except Exception as exc:
            if "no result" in str(exc).lower() or "doesn't return rows" in str(exc).lower():
                return []
            raise
        return [self._wrap(r) for r in rows]  # type: ignore[misc]

    def fetchmany(self, size: int | None = None) -> list[Row]:
        rows = self._cur.fetchmany(size) if size is not None else self._cur.fetchmany()
        return [self._wrap(r) for r in rows]  # type: ignore[misc]

    def __iter__(self) -> Iterator[Row]:
        while True:
            row = self.fetchone()
            if row is None:
                return
            yield row

    def execute(self, sql: str, params: Any = ()) -> "Cursor":
        return self._conn.execute(sql, params)

    def close(self) -> None:
        try:
            self._cur.close()
        except Exception:
            pass


_RE_DDL_ANY = re.compile(r"^\s*(CREATE|ALTER|DROP)\s+(TABLE|SCHEMA)\b", re.I)
_catalog_lock = threading.Lock()
#: (schema, table) -> primary-key columns; (schema, table) -> identity column or "".
_unique_cache: dict[tuple[str, str], list[tuple[str, ...]]] = {}
_pk_cache: dict[tuple[str, str], tuple[str, ...]] = {}
_identity_cache: dict[tuple[str, str], str] = {}


def _invalidate_catalog_cache() -> None:
    with _catalog_lock:
        _pk_cache.clear()
        _unique_cache.clear()
        _identity_cache.clear()


class Connection:
    """A sqlite3-shaped handle over one psycopg connection.

    ``release`` is what ``close()`` calls: returning the connection to the pool
    it came from. The wrapper never closes the underlying socket itself; the
    pool owns that.
    """

    def __init__(self, pgconn: Any, *, release, schema: str) -> None:
        self._pg = pgconn
        self._release = release
        self._schema = schema
        self._closed = False
        self.row_factory: Any = None  # accepted and ignored: rows are always Row

    # -- sqlite3.Connection surface ---------------------------------------- #

    @property
    def in_transaction(self) -> bool:
        from psycopg.pq import TransactionStatus

        return self._pg.info.transaction_status in (
            TransactionStatus.INTRANS,
            TransactionStatus.INERROR,
        )

    def execute(self, sql: str, params: Any = ()) -> Cursor:
        if self._closed:
            raise ProgrammingError("Cannot operate on a closed database.")
        # Transaction control issued as SQL text (sqlite3 lets a caller write
        # "COMMIT" / "ROLLBACK" as statements): route it to the connection so
        # psycopg's own transaction state stays consistent.
        if _RE_COMMIT.match(sql):
            self.commit()
            return self._empty_cursor()
        if _RE_ROLLBACK.match(sql):
            self.rollback()
            return self._empty_cursor()
        has_params = bool(params) if not isinstance(params, dict) else bool(params)
        tr = translate(sql, has_params)
        text = tr.sql
        if tr.upsert_table:
            text = self._finish_upsert(tr, has_params)
        elif tr.insert_table and not _RE_RETURNING.search(text):
            # sqlite3 exposes the new rowid as cursor.lastrowid; PostgreSQL has
            # no such thing, so an INSERT into a table with an identity column
            # asks for it back explicitly and the cursor surfaces it the same way.
            id_col = self._identity_column(tr.insert_table)
            if id_col:
                text = text.rstrip().rstrip(";") + f' RETURNING "{id_col}" AS __assure_id'
        if _RE_DDL_ANY.match(text):
            _invalidate_catalog_cache()
        bound = _coerce_in(params) if has_params else None

        from psycopg.pq import TransactionStatus

        in_tx = self._pg.info.transaction_status == TransactionStatus.INTRANS
        cur = self._pg.cursor()
        try:
            if in_tx:
                # Savepoint commands run on their own cursor: a RELEASE on the
                # statement's cursor would replace its result set.
                with self._pg.cursor() as sp:
                    sp.execute("SAVEPOINT assure_stmt")
            cur.execute(text, bound)
            if in_tx:
                with self._pg.cursor() as sp:
                    sp.execute("RELEASE SAVEPOINT assure_stmt")
        except Exception as exc:
            try:
                if in_tx:
                    with self._pg.cursor() as sp:
                        sp.execute("ROLLBACK TO SAVEPOINT assure_stmt")
                else:
                    self._pg.rollback()
            except Exception:
                pass
            cur.close()
            raise _translate_error(exc) from exc
        keys = tuple(d.name for d in cur.description) if cur.description else ()
        wrapped = Cursor(self, cur, keys)
        if tr.is_insert and cur.description and keys == ("__assure_id",):
            # The identity INSERT was given RETURNING; surface it as lastrowid
            # and leave the cursor empty the way sqlite3 leaves an INSERT cursor.
            row = cur.fetchone()
            wrapped.lastrowid = int(row[0]) if row and row[0] is not None else None
            wrapped._keys = ()
        return wrapped

    def _empty_cursor(self) -> Cursor:
        cur = self._pg.cursor()
        cur.execute(_EMPTY_RESULT_SQL)
        return Cursor(self, cur, ())

    def executemany(self, sql: str, seq_of_params: Iterable[Any]) -> Cursor:
        last: Cursor | None = None
        for params in seq_of_params:
            last = self.execute(sql, params)
        return last if last is not None else self.execute(_EMPTY_RESULT_SQL)

    def executescript(self, script: str) -> None:
        for stmt in _split_statements(script):
            self.execute(stmt)
        self.commit()

    def cursor(self) -> "_DeferredCursor":
        return _DeferredCursor(self)

    def commit(self) -> None:
        if self._closed:
            return
        try:
            self._pg.commit()
        except Exception as exc:
            raise _translate_error(exc) from exc

    def rollback(self) -> None:
        if self._closed:
            return
        try:
            self._pg.rollback()
        except Exception:
            pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._pg.rollback()
        except Exception:
            pass
        self._release(self._pg)

    def __del__(self) -> None:
        # Safety net for a handle nobody closed (a standalone ``get_db()`` with
        # no scope around it): when the wrapper is collected the raw connection
        # goes back to the pool instead of sitting "idle in transaction" and
        # holding locks. The gauge stays accurate through the normal path.
        try:
            if not getattr(self, "_closed", True):
                self.close()
        except Exception:
            pass

    # sqlite3 exposes these as attributes; a few callers touch them.
    @property
    def total_changes(self) -> int:
        return 0

    isolation_level = None

    def __enter__(self) -> "Connection":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()

    # -- helpers ------------------------------------------------------------ #

    def _finish_upsert(self, tr: _Translated, has_params: bool) -> str:
        table = tr.upsert_table or ""
        cols = tr.upsert_columns or ()
        pk = self._primary_key(table)
        # SQLite's REPLACE fires on ANY unique violation; PostgreSQL needs one
        # conflict target. The PK is it only when the statement supplies every
        # PK column. `document_locks` inserts a fresh `lock-<uuid>` id and relies
        # on UNIQUE (project_id, version); `prompt_versions` has an identity id and
        # UNIQUE (run_hash) — with the PK as target both raised IntegrityError on
        # the second write (audit 2026-09-23). Then: the unique index whose
        # columns the statement supplies.
        target_key: tuple[str, ...] = ()
        if cols:
            # A supplied non-PK unique key first: the three REPLACE sites in
            # this repo that carry one (document_locks, prompt_versions) mean
            # "replace the row with these business keys", and their PK value is
            # fresh on every call.
            for candidate in self._unique_keys(table):
                if candidate and all(c in cols for c in candidate):
                    target_key = candidate
                    break
        if not target_key and pk and (not cols or all(c in cols for c in pk)):
            target_key = pk
        pk = target_key
        if not pk:
            # Nothing to conflict on: a plain INSERT is the closest SQLite "REPLACE".
            return tr.sql
        updates = [f'"{c}" = EXCLUDED."{c}"' for c in cols if c not in pk]
        target = ", ".join(f'"{c}"' for c in pk)
        if updates:
            clause = f"ON CONFLICT ({target}) DO UPDATE SET " + ", ".join(updates)
        else:
            clause = f"ON CONFLICT ({target}) DO NOTHING"
        return _append_conflict_clause(tr.sql, clause)

    def _identity_column(self, table: str) -> str:
        key = (self._schema, table)
        cached = _identity_cache.get(key)
        if cached is not None:
            return cached
        with self._pg.cursor() as cur:
            cur.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = current_schema() AND table_name = %s
                  AND is_identity = 'YES'
                ORDER BY ordinal_position LIMIT 1
                """,
                (table,),
            )
            row = cur.fetchone()
        col = str(row[0]) if row else ""
        with _catalog_lock:
            _identity_cache[key] = col
        return col

    def _unique_keys(self, table: str) -> list[tuple[str, ...]]:
        """Unique constraints AND unique indexes of ``table`` (pg_index sees both;
        information_schema only reports constraints), shortest first."""
        key = (self._schema, table)
        cached = _unique_cache.get(key)
        if cached is not None:
            return cached
        with self._pg.cursor() as cur:
            cur.execute(
                """
                SELECT i.indexrelid::regclass::text,
                       array_agg(a.attname ORDER BY k.ord)
                FROM pg_index i
                JOIN pg_class c ON c.oid = i.indrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord) ON TRUE
                JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum
                WHERE n.nspname = current_schema() AND c.relname = %s
                  AND i.indisunique AND NOT i.indisprimary AND i.indpred IS NULL
                GROUP BY i.indexrelid
                """,
                (table,),
            )
            keys = sorted((tuple(str(c) for c in row[1]) for row in cur.fetchall()), key=len)
        with _catalog_lock:
            _unique_cache[key] = keys
        return keys

    def _primary_key(self, table: str) -> tuple[str, ...]:
        key = (self._schema, table)
        cached = _pk_cache.get(key)
        if cached is not None:
            return cached
        with self._pg.cursor() as cur:
            cur.execute(
                """
                SELECT kcu.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name
                 AND tc.table_schema = kcu.table_schema
                WHERE tc.constraint_type = 'PRIMARY KEY'
                  AND tc.table_schema = current_schema()
                  AND tc.table_name = %s
                ORDER BY kcu.ordinal_position
                """,
                (table,),
            )
            pk = tuple(r[0] for r in cur.fetchall())
        if not pk:
            # Fall back to a UNIQUE constraint if the table has exactly one.
            with self._pg.cursor() as cur:
                cur.execute(
                    """
                    SELECT tc.constraint_name, kcu.column_name
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                      ON tc.constraint_name = kcu.constraint_name
                     AND tc.table_schema = kcu.table_schema
                    WHERE tc.constraint_type = 'UNIQUE'
                      AND tc.table_schema = current_schema()
                      AND tc.table_name = %s
                    ORDER BY tc.constraint_name, kcu.ordinal_position
                    """,
                    (table,),
                )
                rows = cur.fetchall()
            names = {r[0] for r in rows}
            if len(names) == 1:
                pk = tuple(r[1] for r in rows)
        with _catalog_lock:
            _pk_cache[key] = pk
        return pk


class _DeferredCursor:
    """``conn.cursor()`` support: executes through the connection."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn
        self._last: Cursor | None = None

    def execute(self, sql: str, params: Any = ()) -> "_DeferredCursor":
        self._last = self._conn.execute(sql, params)
        return self

    def __getattr__(self, name: str) -> Any:
        if self._last is None:
            raise ProgrammingError("cursor has not executed a statement")
        return getattr(self._last, name)

    def __iter__(self):
        if self._last is None:
            return iter(())
        return iter(self._last)

    def close(self) -> None:
        if self._last is not None:
            self._last.close()


def _split_statements(script: str) -> list[str]:
    out: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    for ch in script:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
        elif ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


# --------------------------------------------------------------------------- #
# Pool
# --------------------------------------------------------------------------- #

_pools: dict[tuple[str, str], Any] = {}
_pools_lock = threading.Lock()
#: Pools kept open at once. Production has one schema; the test-suite derives
#: a schema per test, and without a cap every one of them would keep its
#: min_size connection open until the server's max_connections ran out.
_MAX_POOLS = int(os.environ.get("PG_MAX_POOLS", "4"))


def _pool_limits() -> tuple[int, int, float]:
    size = int(os.environ.get("PG_POOL_SIZE") or os.environ.get("SQLITE_POOL_SIZE") or "5")
    overflow = int(
        os.environ.get("PG_POOL_MAX_OVERFLOW") or os.environ.get("SQLITE_POOL_MAX_OVERFLOW") or "15"
    )
    timeout = float(os.environ.get("PG_POOL_TIMEOUT") or os.environ.get("SQLITE_POOL_TIMEOUT") or "5")
    return size, overflow, timeout


def _application_name() -> str:
    """The ``application_name`` every connection of this process carries.

    Per process, not per deployment: the test-suite's cleanup terminates its
    own leftover sessions by this name, and two suites (or a suite and a dev
    server) sharing one database must never kill each other's connections.
    """
    explicit = (os.environ.get("ASSURE_PG_APPLICATION_NAME") or "").strip()
    if explicit:
        return explicit[:60]
    return f"assure-{os.getpid()}"[:60]


def _get_pool(url: str, schema: str):
    key = (url, schema)
    pool = _pools.get(key)
    if pool is not None:
        return pool
    with _pools_lock:
        pool = _pools.get(key)
        if pool is not None:
            return pool
        import psycopg
        from psycopg_pool import ConnectionPool

        size, overflow, timeout = _pool_limits()
        # Every connection pins timezone=UTC so CURRENT_TIMESTAMP renders the
        # same instant SQLite's did, and pins search_path to the chosen schema.
        options = f"-c timezone=UTC -c search_path={schema},public"
        app_name = _application_name()
        with psycopg.connect(url, options=options, autocommit=True, application_name=app_name) as bootstrap:
            ensure_schema(bootstrap, schema)
            ensure_compat_functions(bootstrap, key=url)
        pool = ConnectionPool(
            conninfo=url,
            min_size=1,
            max_size=max(1, size + overflow),
            timeout=timeout,
            open=True,
            kwargs={"options": options, "autocommit": False, "application_name": app_name},
            name=f"assure-{schema}",
        )
        while len(_pools) >= _MAX_POOLS:
            oldest_key = next(iter(_pools))
            oldest = _pools.pop(oldest_key)
            try:
                oldest.close(timeout=1.0)
            except Exception:
                pass
        _pools[key] = pool
        return pool


def checkout(db_path: str | None = None) -> Connection:
    """A pooled connection wrapped in the sqlite3-shaped ``Connection``.

    ``db_path`` is the SQLite path the caller resolved; it only matters when
    the schema is derived from it (see :func:`current_schema`).
    """
    url = database_url()
    if url is None:
        raise RuntimeError("DATABASE_URL is not a PostgreSQL DSN")
    schema = current_schema(db_path)
    pool = _get_pool(url, schema)
    pgconn = pool.getconn()

    def _release(raw: Any) -> None:
        try:
            pool.putconn(raw)
        except Exception as exc:
            _log.error("PostgreSQL connection not returned to pool (%s: %s)", exc.__class__.__name__, exc)

    return Connection(pgconn, release=_release, schema=schema)


def is_compat_connection(conn: Any) -> bool:
    return isinstance(conn, Connection)


def close_all_pools() -> None:
    with _pools_lock:
        for pool in _pools.values():
            try:
                pool.close()
            except Exception:
                pass
        _pools.clear()


def drop_derived_schemas() -> int:
    """Test helper: drop every ``db_<hash>`` schema this suite created."""
    url = database_url()
    if url is None:
        return 0
    import psycopg

    close_all_pools()
    app_name = _application_name()
    with psycopg.connect(url, autocommit=True) as conn:
        # A handle a test never closed would sit "idle in transaction" and block
        # DROP SCHEMA forever; end this application's own leftover sessions first.
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = current_database() AND pid <> pg_backend_pid() "
            "AND application_name = %s",
            (app_name,),
        )
        conn.execute("SET lock_timeout = '10s'")
        rows = conn.execute(
            "SELECT schema_name FROM information_schema.schemata WHERE schema_name LIKE 'db\\_%'"
        ).fetchall()
        for (name,) in rows:
            try:
                conn.execute(f'DROP SCHEMA IF EXISTS "{_safe_ident(name)}" CASCADE')
            except Exception as exc:
                _log.warning("could not drop test schema %s: %s", name, exc)
    _schemas_ready.clear()
    return len(rows)


def drop_schema(schema: str) -> None:
    """Test helper: drop one derived schema and everything in it."""
    url = database_url()
    if url is None or schema == "public":
        return
    import psycopg

    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(f'DROP SCHEMA IF EXISTS "{_safe_ident(schema)}" CASCADE')
    _schemas_ready.discard(schema)
