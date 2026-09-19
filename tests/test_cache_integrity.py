"""Pipeline-cache writes: the foreign key, the sentinel, and the count.

`pipeline_cache.project_id` references `projects(id)`. Every write to it is
best-effort, which used to mean every failure was silent — including the one that
matters, a write carrying a project id no `projects` row owns. The row is simply
absent, so the next read is a miss and a miss reads as a cold start: the cache the
pipeline was told it had is gone, and nothing says so.

Measured on the pre-migration staging database (2026-09-18): 24 `pipeline_cache`
rows carried a `project_id` with no `projects` row. On the migrated database those
writes are now *rejected* instead of stored, which is correct — and invisible until
there is a number to read.

Two shapes are needed, because the answer differs between them and only one of them
is the deployed one: a fresh `init_db` in this tree declares no constraint on
`pipeline_cache` (the fresh-database declarations land separately), while the
migrated box does. `_migrated_db` rebuilds the table to the shape staging runs, so
the refusal under test is the refusal the product hits.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from prompt_matrix.db.connection import init_db
from prompt_matrix.history import get_db
from prompt_matrix.lib.logger import cache_drop_count
from prompt_matrix.services import entailment_cache
from prompt_matrix.services.omp_memory import save_ast_cache

# The table as staging runs it, quoted from its `sqlite_master`. Rebuilt rather
# than declared here because `init_db` in this tree creates the unconstrained
# version; the point of these tests is the constrained one.
STAGING_PIPELINE_CACHE_DDL = """
CREATE TABLE "pipeline_cache" (
    cache_key TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
)
"""


def _migrated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A database whose pipeline_cache carries the foreign key staging declares."""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "cache.sqlite"))
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    init_db()
    db = get_db()
    db.execute("DROP TABLE IF EXISTS pipeline_cache")
    db.execute(STAGING_PIPELINE_CACHE_DDL)
    db.commit()


def _unconstrained_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A fresh `init_db` in this tree: pipeline_cache carries no constraint.

    Worth testing separately, because that shape is where the sentinel fallback did
    its real damage — with nothing to refuse the write, the literal was stored as
    the row's project_id.
    """
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "fresh.sqlite"))
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    init_db()


def _add_project(project_id: str) -> None:
    db = get_db()
    cols = {r[1] for r in db.execute("PRAGMA table_info(projects)").fetchall()}
    names = ["id", "title", "current_version"]
    values: list = [project_id, "cache-integrity", 1]
    if "owner_id" in cols:
        names.append("owner_id")
        values.append(None)
    db.execute(
        f"INSERT OR IGNORE INTO projects ({', '.join(names)}) VALUES ({', '.join('?' for _ in names)})",
        values,
    )
    db.commit()


def _rows(project_id: str) -> list[tuple]:
    return (
        get_db()
        .execute(
            "SELECT cache_key, project_id, kind FROM pipeline_cache WHERE project_id = ?",
            (project_id,),
        )
        .fetchall()
    )


def _verdict() -> dict:
    return {"verdict": "yes", "model": "stub/model", "checked_at": "2026-09-19T00:00:00Z"}


# --- the refusal ---------------------------------------------------------------


def test_a_write_the_foreign_key_refuses_is_counted_and_writes_nothing(tmp_path, monkeypatch):
    """A rejected write leaves no row and is not silent: it is a number at /api/health."""
    _migrated_db(tmp_path, monkeypatch)
    before = cache_drop_count()
    entailment_cache.store_verdict("entailment:orphan", "no-such-project", _verdict())
    assert _rows("no-such-project") == []
    assert cache_drop_count() == before + 1


def test_a_write_with_a_real_project_is_not_counted(tmp_path, monkeypatch):
    """The count is about refusals, so a write that lands must not move it."""
    _migrated_db(tmp_path, monkeypatch)
    _add_project("integrity-real")
    before = cache_drop_count()
    entailment_cache.store_verdict("entailment:real", "integrity-real", _verdict())
    save_ast_cache("ast:integrity-real:1", "integrity-real", {"compiled": {"node_count": 1}})
    assert [r[2] for r in _rows("integrity-real")] == ["entailment", "ast"]
    assert cache_drop_count() == before


def test_a_refused_write_leaves_the_connection_usable(tmp_path, monkeypatch):
    """The rejected statement must be rolled back, not left holding the write lock.

    Swallowing the rejection left the failed statement uncommitted on a shared
    connection, and the next write failed `database is locked` — the same cascade
    the audit-row counter fixed for audit_log. So the write after a refusal is part
    of the contract, not a separate concern.
    """
    _migrated_db(tmp_path, monkeypatch)
    _add_project("integrity-after-refusal")
    entailment_cache.store_verdict("entailment:refused", "no-such-project", _verdict())
    save_ast_cache("ast:integrity-after-refusal:1", "integrity-after-refusal", {"compiled": {}})
    assert len(_rows("integrity-after-refusal")) == 1


def test_a_falsy_project_id_is_not_stored_under_a_sentinel(tmp_path, monkeypatch):
    """`project_id or "entailment"` is gone.

    The literal named no project. On a database without the constraint it stored a
    row claiming one; on the deployed shape the write is refused, and either way the
    id the caller passed was not the id recorded — so the id is now the caller's.
    (The relational tier's twin of this, ``project_id or CACHE_KIND``, is covered on
    postaudit/cache-integrity-relational — that module does not exist on this
    lineage.)
    """
    _migrated_db(tmp_path, monkeypatch)
    before = cache_drop_count()
    entailment_cache.store_verdict("entailment:falsy", "", _verdict())
    stored = (
        get_db()
        .execute("SELECT project_id FROM pipeline_cache WHERE project_id IN ('entailment', '')")
        .fetchall()
    )
    assert stored == []
    assert cache_drop_count() == before + 1

    # The shape without the constraint: nothing refuses the write, so this is the
    # one where the literal used to become the row's project_id.
    _unconstrained_db(tmp_path, monkeypatch)
    entailment_cache.store_verdict("entailment:falsy-fresh", "", _verdict())
    fresh = (
        get_db()
        .execute("SELECT project_id FROM pipeline_cache WHERE project_id IN ('entailment', '')")
        .fetchall()
    )
    assert fresh == [], "the sentinel must not become a stored project id"


def test_a_failure_that_is_not_a_foreign_key_propagates(tmp_path, monkeypatch):
    """Only a rejected write is swallowed. A bug in the cache is not."""
    _migrated_db(tmp_path, monkeypatch)

    def _boom(*_args, **_kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(entailment_cache, "save_pipeline_cache", _boom)
    with pytest.raises(sqlite3.OperationalError):
        entailment_cache.store_verdict("entailment:boom", "any-project", _verdict())


def test_health_reports_the_refusals_it_has_seen(tmp_path, monkeypatch):
    """/api/health carries the count, the way it carries audit_drops.

    A number on the health route is the whole point: the refused row is absent, so
    without it the loss is only in the service log.
    """
    _migrated_db(tmp_path, monkeypatch)
    from prompt_matrix.web import create_app

    client = create_app(require_auth=False).test_client()
    before = client.get("/api/health").get_json()["cache_drops"]
    entailment_cache.store_verdict("entailment:health", "no-such-project", _verdict())
    after = client.get("/api/health").get_json()
    assert after["cache_drops"] == before + 1


# --- the two paths the cache exists for ----------------------------------------


SOURCE = (
    "ACME POLICY 2026\n"
    "The policy period runs from 1 January 2026 to 31 December 2026.\n"
    "The deductible is 2500 dollars for each covered claim.\n"
    "The coverage limit is 1000000 dollars per occurrence.\n"
)
DRAFT = (
    "## Policy Summary\n"
    "The policy period runs from 1 January 2026 to 31 December 2026.\n"
    "The deductible is 2500 dollars for each covered claim.\n"
    "The coverage limit is 1000000 dollars per occurrence.\n"
)


def test_a_compile_hits_the_cache_twice_and_the_second_run_is_the_hit(tmp_path, monkeypatch):
    """The compile keeps its cache across the fix, and the fix reports its refusals."""
    _migrated_db(tmp_path, monkeypatch)
    _add_project("integrity-compile")

    from prompt_matrix.db.substrate_repository import save_substrate_entry
    from prompt_matrix.routers import draft as draft_module

    source_id = save_substrate_entry(
        "integrity-compile", filename="policy.md", page_count=1, extracted_text=SOURCE
    )["id"]

    drafts: list[str] = []

    def _stub_stream_model(gov, messages, *, target_ai=None, cancel_check=None):
        drafts.append(target_ai or "")
        if False:
            yield ""
        yield (DRAFT, 120, 90, "stub/draft-model")

    monkeypatch.setattr(draft_module, "_stream_model", _stub_stream_model)

    def _run() -> list[dict]:
        frames = []
        for item in draft_module.run_draft_pipeline(
            "integrity-compile",
            intent="Write a memo summarising the policy terms.",
            substrate_file_ids=[source_id],
        ):
            if not isinstance(item, str):
                continue
            for line in item.splitlines():
                if not line.startswith("data: "):
                    continue
                try:
                    parsed = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    frames.append(parsed)
        return frames

    first = _run()
    assert drafts, "the first run must reach the model — nothing was cached yet"
    assert any(f.get("type") == "compiled" for f in first), "the first run must render a document"
    assert len(_rows("integrity-compile")) == 1

    drafts.clear()
    drops_before = cache_drop_count()
    second = _run()
    assert drafts == [], "the second run must be served from the cache, not a model call"
    assert any(f.get("omp_cached") is True for f in second), [f.get("stage") for f in second]
    assert cache_drop_count() == drops_before
