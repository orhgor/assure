"""v1.2 features: PDF export/import, disk autosave, image nodes, node revisions."""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PDF_FIXTURE = ROOT / "tests" / "fixtures" / "grounding-sample.pdf"


def _reset_db_path(monkeypatch, db_path: Path) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()


@pytest.fixture()
def v12_client(tmp_path, monkeypatch):
    db_path = tmp_path / "history.sqlite"
    jdf_dir = tmp_path / "jdf_disk"
    jdf_dir.mkdir()
    monkeypatch.setenv("ASSURE_JDF_DIR", str(jdf_dir))
    _reset_db_path(monkeypatch, db_path)
    from prompt_matrix.db.connection import init_db
    from prompt_matrix.web import create_app

    init_db()
    app = create_app(require_auth=False)
    return app.test_client(), jdf_dir


def _sample_tree(*, with_image: bool = False) -> dict:
    from prompt_matrix.models.jdf import empty_annotations

    ann = empty_annotations()
    children: list[dict] = [
        {
            "type": "paragraph",
            "id": "p-v12-1",
            "content": "Assure v1.2 export test paragraph.",
            "entities_referenced": [],
            "provenance": [],
            "meta": {},
            "annotations": ann,
        }
    ]
    if with_image:
        children.append(
            {
                "type": "image",
                "id": "img-v12-1",
                "src": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
                "alt": "1x1 test pixel",
                "caption": "Test figure",
                "meta": {},
                "annotations": ann,
            }
        )
    return {
        "document_id": "doc-v12-test",
        "meta": {"title": "v12-test"},
        "truth_ledger": {},
        "body": [
            {
                "type": "section",
                "id": "sec-v12-1",
                "title": "Section",
                "children": children,
                "meta": {},
                "annotations": ann,
            }
        ],
    }


def test_export_pdf(v12_client) -> None:
    client, _jdf_dir = v12_client
    tree = _sample_tree()
    put = client.put("/api/projects/v12-pdf/jdf", json={"document": tree, "mutation_type": "TEST"})
    assert put.status_code == 200

    res = client.get("/api/projects/v12-pdf/export?format=pdf")
    assert res.status_code == 200
    assert res.mimetype == "application/pdf"
    body = res.data
    assert body[:4] == b"%PDF"
    assert len(body) > 100


def test_import_pdf(v12_client) -> None:
    client, _jdf_dir = v12_client
    assert PDF_FIXTURE.is_file()
    data = {
        "file": (BytesIO(PDF_FIXTURE.read_bytes()), "grounding-sample.pdf"),
    }
    res = client.post(
        "/api/projects/v12-import/import-pdf", data=data, content_type="multipart/form-data"
    )
    assert res.status_code == 200
    payload = res.get_json()
    assert payload.get("ok") is True
    doc = payload.get("document") or {}
    assert doc.get("body")
    first_section = doc["body"][0]
    assert first_section.get("type") == "section"
    assert first_section.get("children")


def test_jdf_autosave_disk(v12_client) -> None:
    client, jdf_dir = v12_client
    tree = _sample_tree()
    res = client.put(
        "/api/projects/v12-disk/jdf", json={"document": tree, "mutation_type": "DISK_TEST"}
    )
    assert res.status_code == 200
    payload = res.get_json()
    disk_path = payload.get("disk_path")
    assert disk_path
    path = Path(disk_path)
    assert path.is_file()
    assert path.parent == jdf_dir / "v12-disk"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["document_id"] == "doc-v12-test"


def test_image_node_roundtrip(v12_client) -> None:
    client, _jdf_dir = v12_client
    tree = _sample_tree(with_image=True)
    res = client.put(
        "/api/projects/v12-img/jdf", json={"document": tree, "mutation_type": "IMAGE_TEST"}
    )
    assert res.status_code == 200
    got = client.get("/api/projects/v12-img/jdf")
    assert got.status_code == 200
    doc = got.get_json().get("document") or {}
    section = doc["body"][0]
    types = [c.get("type") for c in section.get("children") or []]
    assert "image" in types

    from prompt_matrix.exporters.text_ast import jdf_to_html, jdf_to_markdown

    html = jdf_to_html(doc)
    md = jdf_to_markdown(doc)
    assert "img-v12-1" in html or "data:image" in html
    assert "Test figure" in md or "1x1 test pixel" in md


def test_node_revision_history_and_restore(v12_client) -> None:
    client, _jdf_dir = v12_client
    tree = _sample_tree()
    client.put("/api/projects/v12-rev/jdf", json={"document": tree, "mutation_type": "INITIAL"})

    updated = _sample_tree()
    updated["body"][0]["children"][0]["content"] = "Revised paragraph for node history."
    res = client.put(
        "/api/projects/v12-rev/jdf",
        json={
            "document": updated,
            "mutation_type": "NODE_UPDATE",
            "target_node_id": "p-v12-1",
        },
    )
    assert res.status_code == 200

    hist = client.get("/api/projects/v12-rev/nodes/p-v12-1/history")
    assert hist.status_code == 200
    revisions = hist.get_json().get("revisions") or []
    assert len(revisions) >= 1
    revision_id = revisions[0]["revision_id"]

    restore_tree = _sample_tree()
    restore_tree["body"][0]["children"][0]["content"] = "Restored from revision snapshot."
    client.put(
        "/api/projects/v12-rev/jdf",
        json={"document": restore_tree, "mutation_type": "BEFORE_RESTORE"},
    )

    restore = client.post(
        "/api/projects/v12-rev/nodes/p-v12-1/restore",
        json={"revision_id": revision_id},
    )
    assert restore.status_code == 200
    payload = restore.get_json()
    assert payload.get("ok") is True
    doc = payload.get("document") or {}
    content = doc["body"][0]["children"][0]["content"]
    assert "Revised paragraph" in content or "Assure v1.2" in content
