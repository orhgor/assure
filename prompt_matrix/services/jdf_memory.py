"""Store JDF documents + chunks: PostgreSQL holds the document and the chunk
index, OMP (when configured) holds a second, keyword-ranked copy of the chunks.

The chunk index was OMP-only until 2026-09-23. The local and staging containers
run without ``OMP_SERVER`` (``omp_client.omp_configured()`` is False there), so
every ingest wrote 0 chunks and every search returned 0 hits with ``ok: true`` —
a user who uploaded a 16-page PDF from the SOURCES panel and searched a phrase
from it got nothing. ``jdf_cli_chunks`` is the durable index every ingest writes
first; OMP is a ranking layer on top of it, never the only copy.
"""
import hashlib
import json
import logging
import re
import sqlite3

from .omp_memory import safe_omp_remember

try:
    from ..omp_client import (
        DEFAULT_NAMESPACE,
        omp_configured,
        omp_delete_memory,
        omp_list_memories,
        omp_recall,
        sanitize_omp_tag,
    )
except ImportError:
    from omp_client import (
        DEFAULT_NAMESPACE,
        omp_configured,
        omp_delete_memory,
        omp_list_memories,
        omp_recall,
        sanitize_omp_tag,
    )

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

# The durable chunk index. One row per (tenant, doc, chunk_idx) of the current
# generation: remember_jdf_document replaces the document's rows in the same
# unit of work that upserts its jdf_cli_documents row, so the two tables never
# disagree about which generation is current. ``text`` is what search matches
# and returns; ``meta_json`` carries the chunk's scalar metadata (page, source
# filename, substrate_file_id) the way the OMP payload's ``meta`` does.
_JDF_CLI_CHUNKS_TABLE = "jdf_cli_chunks"
_JDF_CLI_CHUNKS_DDL = f"""
CREATE TABLE IF NOT EXISTS {_JDF_CLI_CHUNKS_TABLE} (
    tenant_id TEXT NOT NULL,
    doc_id TEXT NOT NULL,
    doc_hash TEXT NOT NULL,
    chunk_idx INTEGER NOT NULL,
    text TEXT NOT NULL,
    meta_json TEXT NOT NULL DEFAULT '{{}}',
    created_at TIMESTAMP DEFAULT (datetime('now')),
    PRIMARY KEY (tenant_id, doc_id, chunk_idx)
)
"""


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
    db.execute(_JDF_CLI_CHUNKS_DDL)
    db.commit()


def _persist_jdf_chunks(doc_id: str, doc_hash: str, chunks: list[dict], tenant_id: str) -> int:
    """Replace this document's rows in the durable chunk index. Returns rows written.

    Called right after ``_persist_jdf_document`` so the index and the document row
    move to the new generation together. Empty chunks are skipped, as they are for
    OMP: a chunk with no text is nothing to search. ``meta`` keeps only scalar
    values — the same filter the OMP payload applies — so a chunk's page number,
    source filename and vault id travel with the text and nothing else does.
    """
    _ensure_jdf_cli_documents_table()
    db = get_db()
    db.execute(
        f"DELETE FROM {_JDF_CLI_CHUNKS_TABLE} WHERE tenant_id = ? AND doc_id = ?",
        (tenant_id, doc_id),
    )
    written = 0
    for idx, chunk in enumerate(chunks):
        text = (chunk.get("text") or chunk.get("content") or "").strip()
        if not text:
            continue
        meta = {
            k: v
            for k, v in chunk.items()
            if k not in ("text", "content") and isinstance(v, (str, int, float, bool))
        }
        db.execute(
            f"""
            INSERT INTO {_JDF_CLI_CHUNKS_TABLE} (tenant_id, doc_id, doc_hash, chunk_idx, text, meta_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(tenant_id, doc_id, chunk_idx) DO UPDATE SET
                doc_hash = excluded.doc_hash,
                text = excluded.text,
                meta_json = excluded.meta_json,
                created_at = datetime('now')
            """,
            (tenant_id, doc_id, doc_hash, idx, text[:8000], json.dumps(meta, ensure_ascii=False)),
        )
        written += 1
    db.commit()
    return written


def _durable_chunk_candidates(query: str, tenant_id: str) -> list[dict]:
    """This tenant's chunks from the durable index, in the OMP payload shape.

    Only rows of the current generation are read (the join on doc_hash is what
    ``_current_generation`` does for OMP rows), newest document first, chunks in
    document order. The query's tokens prefilter in SQL as substrings so a large
    tenant is not read whole; ``_matching_chunks`` then applies the whole-token
    contract on the result, the same as for OMP rows.
    """
    tokens = _query_tokens(query)
    if not tokens:
        return []
    where = " OR ".join("LOWER(c.text) LIKE ?" for _ in tokens)
    params: list = [tenant_id, *[f"%{token}%" for token in tokens]]
    try:
        rows = get_db().execute(
            f"""
            SELECT c.doc_id, c.doc_hash, c.chunk_idx, c.text, c.meta_json
            FROM {_JDF_CLI_CHUNKS_TABLE} c
            JOIN {_JDF_CLI_DOCS_TABLE} d
              ON d.tenant_id = c.tenant_id AND d.doc_id = c.doc_id AND d.doc_hash = c.doc_hash
            WHERE c.tenant_id = ? AND ({where})
            ORDER BY d.created_at DESC, c.chunk_idx ASC
            """,
            tuple(params),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    out: list[dict] = []
    for row in rows:
        try:
            meta = json.loads(row[4] or "{}")
        except (TypeError, ValueError):
            meta = {}
        out.append(
            {
                "kind": "jdf_chunk",
                "tenant": tenant_id,
                "doc_id": str(row[0]),
                "doc_hash": str(row[1]),
                "chunk_idx": int(row[2]),
                "text": str(row[3] or ""),
                "meta": meta if isinstance(meta, dict) else {},
            }
        )
    return out


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


# jdf-cli stamps meta.title from the input file name and pdf_to_jdf() converts a
# temp copy, so identical bytes hashed differently on every ingest: the box
# accumulated 20 generations of one document, each with its own doc_hash. Only
# producer-supplied metadata is dropped; pages (the content) are always hashed.
_VOLATILE_JDF_META_KEYS = frozenset(
    {
        "title",
        "filename",
        "file",
        "path",
        "source",
        "created",
        "created_at",
        "createdat",
        "date",
        "timestamp",
    }
)


def _stable_content(jdf_dict: dict) -> str:
    """Canonical JSON of a JDF, ignoring volatile producer metadata.

    ``repr(sorted(jdf_dict.items()))`` only ordered the top level, so nested key
    order leaked into the hash as well; json.dumps(sort_keys=True) is canonical
    all the way down.
    """
    meta = jdf_dict.get("meta")
    stable_meta = (
        {k: v for k, v in meta.items() if str(k).lower() not in _VOLATILE_JDF_META_KEYS}
        if isinstance(meta, dict)
        else {}
    )
    body = {k: v for k, v in jdf_dict.items() if k != "meta"}
    return json.dumps(
        {"meta": stable_meta, **body},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def _doc_hash(jdf_dict: dict) -> str:
    return hashlib.sha256(_stable_content(jdf_dict).encode("utf-8")).hexdigest()[:16]


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
# Widening again is not a fix: a project that had *just* ingested one chunk
# still got 0 hits, because duplicate generations of that document plus the
# blobs take every slot (reproduced against omp-server locally: 20 duplicate
# rows + 40 blobs + the fresh chunk -> fresh chunk outside the window, while
# "POLICY SAMPLE" — no competition — returned it). The window is therefore only
# a fallback; _tenant_chunk_candidates also recalls each of the tenant's own
# document keys, which ranking cannot bury. OMP caps `limit` at 100
# (SearchMemoriesSchema), so 100 is also the widest window available.
_RECALL_LIMIT = 100

# Prune pass: page through the app's namespace looking for earlier generations.
_PRUNE_PAGE = 200
_PRUNE_MAX_PAGES = 25

_QUERY_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _search_raw(query: str, limit: int = _RECALL_LIMIT) -> list[dict]:
    raw = omp_recall(query, limit=limit)
    if not isinstance(raw, dict):
        return []
    memories = raw.get("memories") or raw.get("results") or []
    return memories if isinstance(memories, list) else []


def _doc_key(tenant_id: str, doc_id: str) -> str:
    """The memory key — and therefore a tag — every chunk of (tenant, doc) is written under."""
    return f"jdf:{tenant_id}:{doc_id}"


def _doc_key_selector(tenant_id: str, doc_id: str) -> str:
    """Query that phrase-matches this document's own chunk rows.

    _sanitize_tags appends the key to the row's tags, and OMP's search turns a
    whitespace-free query into one FTS phrase, so this selects the document's
    rows through the tag column instead of ranking them against the rest of the
    store. sanitize_omp_tag() applies the same 50-char truncation the tag got,
    so the phrase stays a token prefix of the stored tag for long project ids.
    """
    return sanitize_omp_tag(_doc_key(tenant_id, doc_id))


def _tenant_doc_ids(tenant_id: str) -> list[str]:
    """doc_ids with a durable row for this tenant (every ingest writes one)."""
    try:
        rows = get_db().execute(
            f"SELECT doc_id FROM {_JDF_CLI_DOCS_TABLE} WHERE tenant_id = ?"
            " ORDER BY created_at DESC",
            (tenant_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [str(row[0]) for row in rows if row[0]]


def _query_tokens(query: str) -> list[str]:
    """Distinct keyword tokens of a query, in order (dedupe keeps ranking stable)."""
    seen: dict[str, None] = {}
    for token in _QUERY_TOKEN_RE.findall(str(query or "").lower()):
        seen.setdefault(token, None)
    return list(seen)


def _matching_chunks(query: str, chunks: list[dict]) -> list[dict]:
    """Drop chunks that share no keyword token with the query. OMP's order is kept.

    OMP's keyword mode ORs the query's tokens, so "any token present" is the
    contract kept here — the ranking stays the one OMP produced, which the app
    already relied on; only the tenant scoping, the stale-generation filter and
    the dedupe are new.
    """
    tokens = _query_tokens(query)
    if not tokens:
        return []
    out: list[dict] = []
    for chunk in chunks:
        present = set(_QUERY_TOKEN_RE.findall(str(chunk.get("text") or "").lower()))
        if any(token in present for token in tokens):
            out.append(chunk)
    return out


def _dedupe_chunks(chunks: list[dict]) -> list[dict]:
    """One entry per (doc_id, chunk_idx, generation), first occurrence wins."""
    seen: set[tuple] = set()
    out: list[dict] = []
    for chunk in chunks:
        key = (chunk.get("doc_id"), chunk.get("chunk_idx"), chunk.get("doc_hash"))
        if key in seen:
            continue
        seen.add(key)
        out.append(chunk)
    return out


def _current_generation(chunks: list[dict], tenant_id: str) -> list[dict]:
    """Drop earlier generations of the same doc_id, then collapse duplicates.

    Every ingest writes the current hash into jdf_cli_documents, so a row whose
    doc_hash differs belongs to an ingest OMP still holds but the app has
    replaced — the same filter get_doc_chunks applies. A doc with no durable row
    (ingested before that row existed) keeps the doc_id-only behaviour; the
    dedupe stays because a re-ingest of byte-identical content writes the same
    chunk under the same hash until the prune pass has cleared the old row.
    """
    expected: dict[str, str | None] = {}
    out: list[dict] = []
    for chunk in chunks:
        doc_id = str(chunk.get("doc_id") or "")
        if doc_id not in expected:
            expected[doc_id] = _durable_doc_hash(doc_id, tenant_id)
        wanted = expected[doc_id]
        if wanted and chunk.get("doc_hash") != wanted:
            continue
        out.append(chunk)
    return _dedupe_chunks(out)


def _tenant_chunk_candidates(query: str, tenant_id: str) -> list[dict]:
    """Every jdf_chunk of this tenant the store can be asked for directly.

    One recall for the user's query (kept for docs with no durable row) plus one
    per ingested document key — see _RECALL_LIMIT for why the query recall alone
    is not enough. Rows are accepted on their own payload (kind/tenant), never
    on the query's ranking.

    The durable index (``jdf_cli_chunks``) is appended after the OMP rows: OMP's
    keyword ranking is kept where it exists, and a deployment with no OMP — the
    local and staging containers — still answers from PostgreSQL. A chunk present
    in both is one hit: ``_dedupe_chunks`` keys on (doc_id, chunk_idx, doc_hash).
    """
    probes = [query] + [
        _doc_key_selector(tenant_id, doc_id) for doc_id in _tenant_doc_ids(tenant_id)
    ]
    out: list[dict] = []
    for probe in probes:
        for memory in _search_raw(probe):
            parsed = _parse_chunk_content(memory)
            if not parsed or parsed.get("kind") != "jdf_chunk":
                continue
            if parsed.get("tenant") != tenant_id:
                continue
            out.append(parsed)
    out.extend(_durable_chunk_candidates(query, tenant_id))
    return out


def _prune_doc_generation(tenant_id: str, doc_id: str) -> int:
    """Delete earlier generations of (tenant, doc) from OMP. Best-effort.

    OMP addresses rows by id, never by key — the app's key survives only as a
    tag — so the prior generation is found by listing the app's namespace and
    matching each row's own payload (exact doc_id + tenant). A tag prefix would
    have to survive sanitize_omp_tag's 50-char truncation, which can cut another
    document's tag into the same prefix. Returns the number of rows removed.
    """
    removed = 0
    try:
        offset = 0
        for _ in range(_PRUNE_MAX_PAGES):
            page = omp_list_memories(
                limit=_PRUNE_PAGE, offset=offset, namespace=DEFAULT_NAMESPACE
            )
            rows = page.get("memories") if isinstance(page, dict) else None
            if not rows:
                break
            for memory in rows:
                parsed = _parse_chunk_content(memory)
                if not parsed or parsed.get("kind") != "jdf_chunk":
                    continue
                if parsed.get("doc_id") != doc_id or parsed.get("tenant") != tenant_id:
                    continue
                if omp_delete_memory(str(memory.get("id") or "")):
                    removed += 1
            if len(rows) < _PRUNE_PAGE:
                break
            offset += _PRUNE_PAGE
    except Exception as exc:  # best-effort: the doc_hash filter still hides stale rows
        log.warning("[jdf] prune skipped doc=%s tenant=%s: %s", doc_id, tenant_id, exc)
    if removed:
        log.info("[jdf] pruned %d earlier chunks doc=%s tenant=%s", removed, doc_id, tenant_id)
    return removed


def remember_jdf_document(doc_id, jdf_dict: dict, chunks: list[dict], tenant_id=DEFAULT_TENANT):
    doc_hash = _doc_hash(jdf_dict)
    # FIX 2: persist the full JDF durably before touching OMP.
    _persist_jdf_document(doc_id, doc_hash, jdf_dict, tenant_id)
    # The durable chunk index is written next, in the same PostgreSQL: search
    # reads it whether or not OMP is configured (module docstring).
    durable = _persist_jdf_chunks(doc_id, doc_hash, chunks, tenant_id)
    # Before the writes: the prune matches rows by payload identity, so running
    # it afterwards would delete the generation just written.
    pruned = _prune_doc_generation(tenant_id, doc_id) if omp_configured() else 0
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
        "[jdf] stored doc=%s tenant=%s chunks=%d durable=%d omp=%d",
        doc_id, tenant_id, len(chunks), durable, stored,
    )
    if not omp_configured():
        # No OMP in this deployment: the durable index is the index, and the
        # counts report it. Nothing is unavailable — search reads PostgreSQL.
        return {
            "doc_id": doc_id,
            "chunks_total": len(chunks),
            "chunks_stored": durable,
            "chunks_failed": 0,
            "chunks_pruned": 0,
            "partial": False,
            "doc_hash": doc_hash,
            "index": "postgres",
        }
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
        "chunks_pruned": pruned,
        "partial": failed > 0,
        "doc_hash": doc_hash,
        "index": "postgres+omp",
    }


def forget_jdf_document(doc_id: str, tenant_id=DEFAULT_TENANT) -> dict:
    """Remove (tenant, doc) from the index: durable rows, document row, OMP rows.

    The vault delete route calls this so a source the user removed stops
    answering searches. There is no dock-side delete route; this is the one
    removal path, and the OMP part reuses the prune pass because OMP rows are
    addressed by id, not by the key the app wrote. Best-effort on the OMP side
    (down or unconfigured means nothing to remove there); the PostgreSQL side is
    the one that must succeed, and it raises if it cannot.
    """
    _ensure_jdf_cli_documents_table()
    db = get_db()
    chunks_removed = db.execute(
        f"DELETE FROM {_JDF_CLI_CHUNKS_TABLE} WHERE tenant_id = ? AND doc_id = ?",
        (tenant_id, doc_id),
    ).rowcount
    docs_removed = db.execute(
        f"DELETE FROM {_JDF_CLI_DOCS_TABLE} WHERE tenant_id = ? AND doc_id = ?",
        (tenant_id, doc_id),
    ).rowcount
    db.commit()
    omp_removed = _prune_doc_generation(tenant_id, doc_id) if omp_configured() else 0
    log.info(
        "[jdf] forgot doc=%s tenant=%s chunks=%d docs=%d omp=%d",
        doc_id, tenant_id, chunks_removed, docs_removed, omp_removed,
    )
    return {
        "doc_id": doc_id,
        "chunks_removed": int(chunks_removed or 0),
        "documents_removed": int(docs_removed or 0),
        "omp_removed": omp_removed,
    }


def search_jdf_chunks(query: str, tenant_id=DEFAULT_TENANT, limit: int = 20):
    """Keyword search over this tenant's ingested chunks.

    Retrieval is tenant-scoped (see _tenant_chunk_candidates), so a hit does not
    depend on OMP's global window; rows of a replaced generation and duplicate
    rows are dropped, and the surviving chunks keep OMP's keyword order.
    """
    chunks = _current_generation(_tenant_chunk_candidates(query, tenant_id), tenant_id)
    return _matching_chunks(query, chunks)[:limit]


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
    q = _doc_key_selector(tenant_id, doc_id)
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
    return _dedupe_chunks(out)