"""JSON/YAML config import tests."""

from __future__ import annotations

import json

import pytest

from prompt_matrix.services.config_import import config_to_jdf_section, parse_config_bytes
from prompt_matrix.web import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "import.db"))
    monkeypatch.setenv("WTF_CSRF_ENABLED", "0")
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db

    init_db()
    app = create_app(require_auth=False)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_parse_json_config():
    raw = b'{"deductible_pct": 2, "nested": {"limit": 100}}'
    parsed = parse_config_bytes(raw, "config.json")
    assert parsed["deductible_pct"] == 2
    assert parsed["nested"]["limit"] == 100


def test_parse_yaml_config():
    pytest.importorskip("yaml")
    raw = b"deductible_pct: 5\nliability: 2M"
    parsed = parse_config_bytes(raw, "config.yaml")
    assert parsed["deductible_pct"] == 5


def test_config_to_jdf_valid_tree():
    section = config_to_jdf_section({"a": 1, "b": {"c": 2}})
    assert section["type"] == "section"
    assert len(section["children"]) >= 2


def test_import_config_endpoint(client):
    create = client.post("/api/projects", json={"title": "Import Config"})
    pid = create.get_json()["id"]
    res = client.post(
        f"/api/projects/{pid}/import-config",
        json={"config": {"rating_engine": {"deductible_pct": 2}}, "title": "Rating Config"},
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    assert body.get("section_id")

    jdf = client.get(f"/api/projects/{pid}/jdf")
    doc = jdf.get_json()["document"]
    assert len(doc.get("body") or []) >= 1
    titles = json.dumps(doc)
    assert "deductible_pct" in titles or "Rating Config" in titles
