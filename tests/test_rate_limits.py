"""Rate limit and daily compile quota tests."""

from __future__ import annotations

import sqlite3

import pytest


def _reset_db_path(monkeypatch, db_path) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()


@pytest.fixture()
def limit_client(tmp_path, monkeypatch):
    db_path = tmp_path / "history.sqlite"
    _reset_db_path(monkeypatch, db_path)
    monkeypatch.setenv("ASSURE_COMPILE_DAILY_LIMIT", "2")
    from prompt_matrix.db.connection import init_db
    from prompt_matrix.web import create_app

    init_db()
    app = create_app(require_auth=False)
    app.config["TESTING"] = True
    return app.test_client(), str(db_path)


def test_daily_compile_limit_blocks_after_cap(limit_client, monkeypatch):
    client, _db = limit_client
    from prompt_matrix.db.jdf_repository import ensure_project

    # ``daily_compile_limits.project_id`` is a FK to ``projects(id)``, so the
    # stream's counter needs the project to exist.
    ensure_project("limit-proj", "Limit")

    def fake_stream(*_a, **_k):
        yield 'data: {"event":"done"}\n\n'

    monkeypatch.setattr(
        "prompt_matrix.routers.inquire_stream.run_inquire_pipeline",
        fake_stream,
    )

    payload = {"user_intent": "Summarize revenue trends for Q3."}
    assert client.post("/api/projects/limit-proj/inquire/stream", json=payload).status_code == 200
    assert client.post("/api/projects/limit-proj/inquire/stream", json=payload).status_code == 200
    res = client.post("/api/projects/limit-proj/inquire/stream", json=payload)
    assert res.status_code == 429


def test_daily_compile_limit_counter(limit_client):
    _client, db_path = limit_client
    from prompt_matrix.db.jdf_repository import ensure_project
    from prompt_matrix.rate_limits import increment_daily_compile_limit

    ensure_project("counter-proj", "Counter")
    increment_daily_compile_limit("counter-proj")
    increment_daily_compile_limit("counter-proj")
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT count FROM daily_compile_limits WHERE project_id = 'counter-proj'"
    ).fetchone()
    conn.close()
    assert row is not None
    assert int(row[0]) == 2
