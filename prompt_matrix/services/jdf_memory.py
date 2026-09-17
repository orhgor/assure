"""Store JDF documents + chunks. SQLite full doc, OMP chunk index."""
import hashlib
import json
import logging
import sqlite3

from .omp_memory import safe_omp_remember

try:
    from ..omp_client import omp_recall
except ImportError:
    from omp_client import omp_recall

try:
    from ..db.connection import init_db
    from ..history import get_db
except ImportError:
    from db.connection import init_db
    from history import get_db

log = logging.getLogger(__name__)

DEFAULT_TENANT = "default"


class OmpUnavailable(RuntimeError):
    """OMP responded but refused every chunk write (or is down)."""


# -- jdf-cli format store (distinct from jdf_documents which holds
# -- PyMuPDF-JDF revisions via save_jdf_revision)
_JDF_CLI_DOCS_TABLE = "jdf_cli_documents"
# (tenant_id, doc_id), not doc_id alone: a second tenant reusing a filename
# overwrote the first tenant's row (audit 2026-09-17 §B).
_JDF_CLI_DOCS_PK = ("tenant_id", "doc_id")
_JDF_CLI_DOCS_COLUMNS = ("tenant_id", "doc_id", "doc_hash", "jdf_json", "created_at")
_JDF_CLI_DOCS_COLUMNS_SQL = """
    tenant_id TEXT NOT NULL,
    doc_id TEXT NOT NULL,
    doc_hash TEXT NOT NULL,
    jdf_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT (datetime('now')),
    PRIMARY KEY (tenant_id, doc_id)
"""
_JDF_CLI_DOCS_DDL = (
    f"CREATE TABLE IF NOT EXISTS {_JDF_CLI_DOCS_TABLE} ({_JDF_CLI_DOCS_COLUMNS_SQL})"
)


def _table_pk_columns(db, table: str = _JDF_CLI_DOCS_TABLE) -> list[str] | None:
    """PK column names in key order, or None when the handle cannot introspect.

    PRAGMA table_info rows are (cid, name, type, notnull, dflt_value, pk), pk
    being the 1-based position within the primary key (0 = not part of it) —
    the same PRAGMA the schema migrations use via _column_exists.
    """
    cursor = db.execute(f"PRAGMA table_info({table})")
    fetchall = getattr(cursor, "fetchall", None)
    if fetchall is None:
        return None
    rows = fetchall()
    keyed = [r for r in rows if int(r[5] or 0) > 0]
    return [r[1] for r in sorted(keyed, key=lambda r: int(r[5]))]


def _migrate_jdf_cli_docs_to_composite_pk(db) -> None:
    """Rebuild jdf_cli_documents with PRIMARY KEY (tenant_id, doc_id).

    CREATE TABLE IF NOT EXISTS never alters an existing table, so an old table
    is identified by its PK and replaced here. Empty tables are dropped;
    populated ones are swapped, copying every row. The INSERT opens the
    transaction, so the DROP/RENAME that follow commit or roll back with the
    copy. Idempotent: the scratch table is dropped up front, so a run
    interrupted after CREATE leaves nothing that the next run trips over.
    """
    table = _JDF_CLI_DOCS_TABLE
    scratch = f"{table}_new"
    count = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    if not count:
        db.execute(f"DROP TABLE {table}")
        db.commit()
        return
    columns = ", ".join(_JDF_CLI_DOCS_COLUMNS)
    db.execute(f"DROP TABLE IF EXISTS {scratch}")
    db.execute(f"CREATE TABLE {scratch} ({_JDF_CLI_DOCS_COLUMNS_SQL})")
    db.execute(f"INSERT INTO {scratch} ({columns}) SELECT {columns} FROM {table}")
    db.execute(f"DROP TABLE {table}")
    db.execute(f"ALTER TABLE {scratch} RENAME TO {table}")
    db.commit()


def _ensure_jdf_cli_documents_table() -> None:
    init_db()
    db = get_db()
    pk = _table_pk_columns(db)
    if pk and tuple(pk) != _JDF_CLI_DOCS_PK:
        log.info("[jdf] %s PK %s -> %s", _JDF_CLI_DOCS_TABLE, pk, list(_JDF_CLI_DOCS_PK))
        _migrate_jdf_cli_docs_to_composite_pk(db)
    db.execute(_JDF_CLI_DOCS_DDL)
    db.commit()


def _persist_jdf_document(doc_id: str, doc_hash: str, jdf_dict: dict, tenant_id: str) -> None:
    """Durably store the full JDF JSON (FIX 2). The minimal jdf_cli_documents table is
    used (not save_jdf_revision / jdf_documents) because jdf-cli output ({$jdf,meta,pages})
    does not match the app JDF schema that parse_document() requires."""
    _ensure_jdf_cli_documents_table()
    db = get_db()
    db.execute(
        """
        INSERT INTO jdf_cli_documents (tenant_id, doc_id, doc_hash, jdf_json)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(tenant_id, doc_id) DO UPDATE SET
            doc_hash = excluded.doc_hash,
            jdf_json = excluded.jdf_json,
            created_at = datetime('now')
        """,
        (tenant_id, doc_id, doc_hash, json.dumps(jdf_dict, ensure_ascii=False)),
    )
    db.commit()


def _doc_hash(jdf_dict: dict) -> str:
    return hashlib.sha256(repr(sorted(jdf_dict.items())).encode()).hexdigest()[:16]


def _parse_chunk_content(memory) -> dict | None:
    """OMP stores chunk payloads as JSON-string `content`; return the dict or None.

    Some writers wrap content in a cache marker (``omp_memory.CACHE_MARKER``,
    e.g. ``"PEM_CACHE_V1\\n{...}"``), so a bare ``json.loads`` is not the only
    shape to accept: on failure, retry from the first ``{``. Content that
    yields no dict either way is skipped, never fabricated.
    """
    if not isinstance(memory, dict):
        return None
    content = memory.get("content")
    if not content:
        return None
    if isinstance(content, dict):
        return content
    if not isinstance(content, str):
        return None
    for candidate in (content, content[content.find("{") :] if "{" in content else ""):
        try:
            parsed = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


# Keyword recall ranks over the whole memory store, so OMP's default window of
# 10 is filled by the ~6 KB AST/PEM cache blobs — they carry the same domain
# words as the chunks (e.g. the ledger's liability_limit*). Measured on the box
# 2026-09-17 for "liability limit": 0 of the 10 slots were chunks, so search
# returned 0 hits with ok:true; at 50 the tenant's 22 chunks were in the window.
_RECALL_LIMIT = 50


def _search_raw(query: str) -> list[dict]:
    raw = omp_recall(query, limit=_RECALL_LIMIT)
    if not isinstance(raw, dict):
        return []
    memories = raw.get("memories") or raw.get("results") or []
    return memories if isinstance(memories, list) else []


def remember_jdf_document(doc_id, jdf_dict: dict, chunks: list[dict], tenant_id=DEFAULT_TENANT):
    doc_hash = _doc_hash(jdf_dict)
    # FIX 2: persist the full JDF durably before touching OMP.
    _persist_jdf_document(doc_id, doc_hash, jdf_dict, tenant_id)
    stored = 0
    attempted = 0
    for idx, chunk in enumerate(chunks):
        text = (chunk.get("text") or chunk.get("content") or "").strip()
        if not text:
            continue
        attempted += 1
        payload = {
            "kind": "jdf_chunk",
            "tenant": tenant_id,
            "doc_id": doc_id,
            "doc_hash": doc_hash,
            "chunk_idx": idx,
            "text": text[:8000],
            "meta": {
                k: v
                for k, v in chunk.items()
                if k not in ("text", "content") and isinstance(v, (str, int, float, bool))
            },
        }
        key = f"jdf:{tenant_id}:{doc_id}:{idx}"
        if safe_omp_remember(key, payload):
            stored += 1
    log.info(
        "[jdf] stored doc=%s tenant=%s chunks=%d stored=%d",
        doc_id, tenant_id, len(chunks), stored,
    )
    if len(chunks) > 0 and stored == 0:  # FIX 4
        raise OmpUnavailable(f"0/{len(chunks)} chunks written to OMP")
    # FIX 2: a half-indexed document must not look healthy. Empty chunks are
    # skipped by design (not failures), so the shortfall is measured against
    # the chunks that were actually offered to OMP.
    failed = attempted - stored
    if failed:
        log.warning(
            "[jdf] partial index doc=%s tenant=%s stored=%d/%d chunks_failed=%d",
            doc_id, tenant_id, stored, attempted, failed,
        )
    return {
        "doc_id": doc_id,
        "chunks_total": len(chunks),
        "chunks_stored": stored,
        "chunks_failed": failed,
        "partial": failed > 0,
        "doc_hash": doc_hash,
    }


def search_jdf_chunks(query: str, tenant_id=DEFAULT_TENANT, limit: int = 20):
    out = []
    for memory in _search_raw(query):
        parsed = _parse_chunk_content(memory)
        if not parsed:
            continue
        if parsed.get("kind") != "jdf_chunk":
            continue
        if parsed.get("tenant") != tenant_id:
            continue
        out.append(parsed)
    return out[:limit]


def _durable_doc_hash(doc_id: str, tenant_id: str) -> str | None:
    """Latest doc_hash for (tenant_id, doc_id), or None when unknown.

    jdf_cli_documents is upserted per ingest, so its row holds the current hash.
    Re-ingesting a filename appends new OMP rows keyed
    jdf:{tenant}:{doc_id}:{idx} without removing the previous generation, so a
    recall returns both. No table or no row (never ingested through this path)
    means no hash to filter on, and callers keep the doc_id-only behaviour.
    """
    try:
        row = get_db().execute(
            f"SELECT doc_hash FROM {_JDF_CLI_DOCS_TABLE} WHERE tenant_id = ? AND doc_id = ?",
            (tenant_id, doc_id),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    return (row[0] or None) if row else None


def get_doc_chunks(doc_id: str, tenant_id=DEFAULT_TENANT):
    q = f"jdf:{tenant_id}:{doc_id}"
    # FIX 1: filter on content, not just identity — stale chunks from an
    # earlier ingest of the same filename must not come back with fresh ones.
    expected_hash = _durable_doc_hash(doc_id, tenant_id)
    out = []
    for memory in _search_raw(q):
        parsed = _parse_chunk_content(memory)
        if not parsed:
            continue
        if parsed.get("doc_id") != doc_id:
            continue
        if expected_hash and parsed.get("doc_hash") != expected_hash:
            continue
        out.append(parsed)
    return out