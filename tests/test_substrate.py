from __future__ import annotations

import sqlite3

import pytest


def _reset_db_path(monkeypatch, db_path) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()


@pytest.fixture()
def substrate_client(tmp_path, monkeypatch):
    db_path = tmp_path / "history.sqlite"
    _reset_db_path(monkeypatch, db_path)
    from prompt_matrix.db.connection import init_db
    from prompt_matrix.web import create_app

    init_db()
    app = create_app(require_auth=False)
    return app.test_client(), str(db_path)

