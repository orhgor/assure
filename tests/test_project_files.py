"""Per-project source.md + last compiled JDF AST (manifest.json)."""

from __future__ import annotations

import json
from pathlib import Path

from prompt_matrix.db.project_files import (
    MANIFEST_VERSION,
    fetch_project_files,
    save_last_compiled,
    save_project_source,
)
from prompt_matrix.i18n import CATALOGS, LOCALES

ROOT = Path(__file__).resolve().parents[1]


def test_project_files_i18n_and_scripts() -> None:
    for locale in LOCALES:
        cat = CATALOGS[locale]
        assert cat["projects.files.restored"].strip()
    html = (ROOT / "prompt_matrix" / "templates" / "index.html").read_text(encoding="utf-8")
    assert "project_files.js" in html
    js = (ROOT / "prompt_matrix" / "static" / "project_files.js").read_text(encoding="utf-8")
    assert "AssureProjectFileManager" in js
    assert "assure_active_project" in js
    assert "lastCompiledOutput" in js
    canvas = (ROOT / "prompt_matrix" / "static" / "jdf_canvas.js").read_text(encoding="utf-8")
    assert "files.hydrate" in canvas
    assert "this.loadProject();" in canvas


def test_project_files_roundtrip(tmp_path, monkeypatch) -> None:
    import sqlite3

    from prompt_matrix.db.connection import init_db
    from prompt_matrix.history import get_db

    db_path = tmp_path / "files.sqlite"
    monkeypatch.setattr("prompt_matrix.history.DB_PATH", db_path)

    def _getter():
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr("prompt_matrix.history.get_db", _getter)
    monkeypatch.setattr("prompt_matrix.db.connection.get_db", _getter)
    conn = _getter()
    init_db(conn)

    empty = fetch_project_files("default")
    assert empty["source_md"] == ""
    assert empty["manifest"]["lastCompiledOutput"] == []
    assert empty["manifest"]["manifestVersion"] == MANIFEST_VERSION
    assert empty["path"] == "/projects/default/"

    save_project_source("default", "# Q3 investor update")
    nodes = [
        {
            "type": "section",
            "id": "sec-1",
            "title": "Revenue",
            "children": [{"type": "paragraph", "id": "p-1", "content": "ARR $12M"}],
        }
    ]
    save_last_compiled(
        "default",
        {
            "document_id": "doc-default",
            "body": nodes,
            "truth_ledger": {"revenue": 12_000_000},
            "meta": {"title": "Q3"},
        },
    )
    loaded = fetch_project_files("default")
    assert loaded["source_md"] == "# Q3 investor update"
    assert loaded["manifest"]["lastCompiledOutput"][0]["id"] == "sec-1"
    assert loaded["manifest"]["truth_ledger"]["revenue"] == 12_000_000
    conn.close()


def test_project_files_routes(tmp_path, monkeypatch) -> None:
    import sqlite3

    from prompt_matrix.db.connection import init_db
    from prompt_matrix.web import create_app

    db_path = tmp_path / "files-routes.sqlite"
    monkeypatch.setattr("prompt_matrix.history.DB_PATH", db_path)

    def _getter():
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr("prompt_matrix.history.get_db", _getter)
    monkeypatch.setattr("prompt_matrix.db.connection.get_db", _getter)
    conn = _getter()
    init_db(conn)
    app = create_app(require_auth=False)
    client = app.test_client()
    put = client.put(
        "/api/projects/default/files",
        json={"source_md": "Draft text", "lastCompiledOutput": [{"type": "section", "id": "s"}]},
    )
    assert put.status_code == 200
    got = client.get("/api/projects/default/files")
    body = got.get_json()
    assert body["source_md"] == "Draft text"
    assert body["manifest"]["lastCompiledOutput"][0]["id"] == "s"
    conn.close()
