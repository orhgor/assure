"""Shared pytest configuration for unit and integration tests."""

from __future__ import annotations

import os

import pytest

# Blank before dotenv (override=False). Live Resend must never run under pytest.
os.environ["RESEND_API_KEY"] = ""


def _postgres_under_test() -> bool:
    url = (os.environ.get("DATABASE_URL") or "").strip().lower()
    return url.startswith(("postgres://", "postgresql://"))


#: The suite runs on PostgreSQL — the same engine as every server deployment.
#: ``docker compose -f docker-compose.dev.yml up -d`` provides this default;
#: CI points DATABASE_URL at its own service container.
_DEFAULT_TEST_DATABASE_URL = "postgresql://assure:assure@localhost:5432/assure"


def pytest_configure(config):
    os.environ["RESEND_API_KEY"] = ""
    os.environ["SQLITE_USE_POOL"] = "0"
    os.environ.setdefault("BRAVE_API_KEY", "")
    os.environ.setdefault("DATABASE_URL", _DEFAULT_TEST_DATABASE_URL)
    # No broker under test: Celery runs eagerly and the parse path is inline
    # unless a test sets PARSE_ASYNC=1 to exercise the 202 + task_id contract.
    os.environ.setdefault("CELERY_BROKER_URL", "")
    os.environ.setdefault("PARSE_ASYNC", "0")
    # No live model calls from the suite: since 2026-09-26 an unset backend
    # resolves to the local Ollama and the intake critique's grounded check
    # would reach it from every run_after_parse (the full suite went from
    # 6 min to >13 min on this laptop). Tests that exercise the model paths
    # inject a completion and set these themselves.
    os.environ.setdefault("PARSURE_LLM_EXTRACTION", "0")
    os.environ.setdefault("PARSURE_REDHAT_LLM", "0")
    # Redis is optional under test: without REDIS_URL the process-local
    # fallbacks run (memory rate limits, eager Celery).
    os.environ.pop("REDIS_URL", None) if os.environ.get("ASSURE_TEST_NO_REDIS") else None
    if _postgres_under_test():
        # The suite isolates tests by pointing each at its own temp SQLite
        # path. On PostgreSQL that path selects a schema instead, so every test
        # still gets a private database (db/pg_compat.current_schema).
        os.environ["ASSURE_PG_SCHEMA_FROM_DB_PATH"] = "1"
    config.addinivalue_line(
        "markers",
        "playwright: browser-driven Assure workbench simulation (requires pytest-playwright)",
    )
    config.addinivalue_line(
        "markers",
        "quality_check: unified pre-promotion quality gate (API, backend, UI, design, visual)",
    )
    if os.environ.get("CI"):
        try:
            import z3

            z3.set_param("parallel.enable", False)
        except Exception:
            pass


def _install_sqlite3_shim() -> None:
    """Route the suite's legacy ``sqlite3.connect(path)`` calls to PostgreSQL.

    Older tests inspect rows by opening the SQLite file the test pointed
    ``DATABASE_PATH`` at. There is no file any more: the same path selects a
    PostgreSQL schema (db/pg_compat.current_schema), so ``sqlite3.connect`` is
    replaced for the session with a function that hands back the compat
    connection for that schema. The returned handle has the same ``execute`` /
    ``fetchone`` / ``fetchall`` / ``commit`` / ``close`` surface, so the tests'
    assertions run unchanged against the real backend.
    """
    import sqlite3

    from prompt_matrix.db import pg_compat

    real_connect = sqlite3.connect

    def _connect(database, *args, **kwargs):
        if database == ":memory:":
            return real_connect(database, *args, **kwargs)
        return pg_compat.checkout(str(database))

    sqlite3.connect = _connect  # type: ignore[assignment]


def pytest_sessionstart(session):
    if _postgres_under_test():
        _install_sqlite3_shim()


@pytest.fixture(autouse=True)
def _block_live_resend(monkeypatch):
    """Strip Resend credentials so pytest never sends live mail."""
    monkeypatch.setenv("RESEND_API_KEY", "")
    monkeypatch.setenv("SQLITE_USE_POOL", "0")


def pytest_sessionfinish(session, exitstatus):
    """Drop the per-test PostgreSQL schemas the run created."""
    if not _postgres_under_test():
        return
    try:
        from prompt_matrix.db.pg_compat import drop_derived_schemas

        dropped = drop_derived_schemas()
        if dropped:
            print(f"\n[pg] dropped {dropped} per-test schema(s)")
    except Exception as exc:  # cleanup only; never fail the run for it
        print(f"\n[pg] schema cleanup skipped: {exc}")
