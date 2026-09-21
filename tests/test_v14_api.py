"""v1.4 API: project templates and SQLite prompts."""

from __future__ import annotations

import pytest

from prompt_matrix.web import create_app


@pytest.fixture
def client():
    app = create_app(require_auth=False)
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_project_templates_list(client):
    res = client.get("/api/project-templates")
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    ids = {t["id"] for t in data["templates"]}
    assert "compliance-memo" in ids
    assert "research-dossier" in ids
    assert "contract-review" in ids
    assert "blank" in ids
    assert len(data["templates"]) == 4
    names = {t["id"]: t.get("name") for t in data["templates"]}
    assert names["research-dossier"] == "Research Dossier"


def test_prompts_crud(client):
    res = client.get("/api/prompts")
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert data["count"] >= 1

    create = client.post(
        "/api/prompts",
        json={"name": "Test prompt", "content": "Hello world", "class": "research"},
    )
    assert create.status_code == 201
    pid = create.get_json()["prompt"]["id"]

    update = client.put(f"/api/prompts/{pid}", json={"content": "Updated content"})
    assert update.status_code == 200
    assert update.get_json()["prompt"]["content"] == "Updated content"

    delete = client.delete(f"/api/prompts/{pid}")
    assert delete.status_code == 200


def test_create_project_with_template(client):
    res = client.post(
        "/api/projects",
        json={
            "title": "Template Test",
            "template_id": "compliance-memo",
            "prompt": "Draft memo body.",
        },
    )
    assert res.status_code == 201
    body = res.get_json()
    assert body["ok"] is True
    pid = body["id"]

    jdf = client.get(f"/api/projects/{pid}/jdf")
    assert jdf.status_code == 200
    doc = jdf.get_json().get("document") or {}
    assert len(doc.get("body") or []) >= 1

    files = client.get(f"/api/projects/{pid}/files")
    assert files.status_code == 200
    assert "Draft memo" in (files.get_json().get("source_md") or "")
