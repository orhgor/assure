"""Auth ownership and CSRF guards."""

from __future__ import annotations

from pathlib import Path

import pytest

from prompt_matrix.db.jdf_repository import ensure_project


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **env: str):
    for key, val in env.items():
        monkeypatch.setenv(key, val)
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "sec.sqlite"))
    monkeypatch.setenv("WTF_CSRF_ENABLED", env.get("WTF_CSRF_ENABLED", "0"))
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db
    from prompt_matrix.web import create_app

    init_db()
    return create_app(require_auth=False)


def test_cross_user_project_access(tmp_path, monkeypatch) -> None:
    app = _client(tmp_path, monkeypatch, ASSURE_ENFORCE_OWNERSHIP="1", WTF_CSRF_ENABLED="0")
    ensure_project("alice-doc", "Alice", owner_id="user_alice")
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["clerk_user_id"] = "user_bob"
    denied = client.get("/api/projects/alice-doc/jdf")
    assert denied.status_code == 403
    write = client.put(
        "/api/projects/alice-doc/jdf",
        json={"document": {"document_id": "x", "meta": {}, "truth_ledger": {}, "body": []}},
    )
    assert write.status_code == 403
    with client.session_transaction() as sess:
        sess["clerk_user_id"] = "user_alice"
    ok = client.get("/api/projects/alice-doc/jdf")
    assert ok.status_code == 200


def test_csrf_protection(tmp_path, monkeypatch) -> None:
    pytest.importorskip("flask_wtf")
    import re

    app = _client(
        tmp_path,
        monkeypatch,
        WTF_CSRF_ENABLED="1",
        ASSURE_ENFORCE_OWNERSHIP="0",
        ASSURE_REQUIRE_LOGIN="false",
    )
    client = app.test_client()
    res = client.post("/api/projects", json={"title": "CSRF Probe"})
    assert res.status_code in {400, 403}
    home = client.get("/app")
    html = home.get_data(as_text=True)
    match = re.search(r'name="csrf-token" content="([^"]+)"', html)
    assert match, "CSRF meta token missing from HTML"
    ok = client.post(
        "/api/projects",
        json={"title": "CSRF Probe"},
        headers={"X-CSRFToken": match.group(1)},
    )
    assert ok.status_code in {200, 201}
