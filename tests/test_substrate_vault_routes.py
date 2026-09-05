"""Substrate Vault UI: list/delete/include routes and provenance stamping."""

from __future__ import annotations

import io
from unittest.mock import patch

import pytest
from pypdf import PdfWriter

from prompt_matrix.models.jdf import attach_substrate_provenance_to_tree, build_document_from_draft


def _single_page_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


@pytest.fixture()
def vault_client(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()

    from prompt_matrix.db.connection import init_db
    from prompt_matrix.web import create_app

    init_db()
    app = create_app(require_auth=False)
    return app.test_client()


def _upload(client, filename="brief.pdf", text="Net income grew 12%."):
    with patch("prompt_matrix.routers.substrate.TextractClient") as mock_cls:
        instance = mock_cls.return_value
        instance._get_page_count.return_value = 1
        instance.extract_text.return_value = {
            "text": text,
            "tables": [],
            "forms": [],
            "page_count": 1,
            "filename": filename,
        }
        data = {"file": (io.BytesIO(_single_page_pdf()), filename)}
        res = client.post(
            "/api/projects/default/substrate/upload",
            data=data,
            content_type="multipart/form-data",
        )
    return res


def test_upload_returns_size_bytes(vault_client):
    res = _upload(vault_client)
    assert res.status_code == 200
    payload = res.get_json()
    assert payload["ok"] is True
    assert payload["size_bytes"] > 0


def test_list_is_empty_for_unknown_project(vault_client):
    res = vault_client.get("/api/projects/no-such-project/substrate")
    assert res.status_code == 200
    assert res.get_json() == {"ok": True, "files": []}


def test_list_returns_uploaded_file(vault_client):
    upload_res = _upload(vault_client)
    file_id = upload_res.get_json()["id"]

    res = vault_client.get("/api/projects/default/substrate")
    assert res.status_code == 200
    payload = res.get_json()
    assert payload["ok"] is True
    assert len(payload["files"]) == 1
    entry = payload["files"][0]
    assert entry["id"] == file_id
    assert entry["filename"] == "brief.pdf"
    assert entry["included"] is True
    assert entry["file_size_bytes"] > 0
    assert entry["claims_count"] == 0


def test_patch_toggles_included(vault_client):
    upload_res = _upload(vault_client)
    file_id = upload_res.get_json()["id"]

    res = vault_client.patch("/api/projects/default/substrate/" + file_id, json={"included": False})
    assert res.status_code == 200
    assert res.get_json()["included"] is False

    listed = vault_client.get("/api/projects/default/substrate").get_json()
    assert listed["files"][0]["included"] is False


def test_patch_unknown_file_returns_404(vault_client):
    res = vault_client.patch(
        "/api/projects/default/substrate/does-not-exist", json={"included": False}
    )
    assert res.status_code == 404


def test_patch_missing_included_field_returns_400(vault_client):
    upload_res = _upload(vault_client)
    file_id = upload_res.get_json()["id"]
    res = vault_client.patch("/api/projects/default/substrate/" + file_id, json={})
    assert res.status_code == 400


def test_delete_removes_file(vault_client):
    upload_res = _upload(vault_client)
    file_id = upload_res.get_json()["id"]

    res = vault_client.delete("/api/projects/default/substrate/" + file_id)
    assert res.status_code == 200
    assert res.get_json()["ok"] is True

    listed = vault_client.get("/api/projects/default/substrate").get_json()
    assert listed["files"] == []


def test_delete_unknown_file_returns_404(vault_client):
    res = vault_client.delete("/api/projects/default/substrate/does-not-exist")
    assert res.status_code == 404


def test_attach_substrate_provenance_to_tree_matches_value_in_source_text():
    tree = build_document_from_draft("p1", "Revenue is $10M this quarter.").model_dump(mode="json")
    locks = [{"canonical_key": "Revenue", "value": 10, "unit": "M"}]
    substrate_rows = [
        {
            "id": "sub-1",
            "filename": "cim.pdf",
            "extracted_text": "The board confirmed revenue of 10 for the quarter under review.",
        }
    ]

    updated = attach_substrate_provenance_to_tree(tree, locks, substrate_rows)
    para = updated["body"][0]["children"][0]
    assert para["provenance"], "expected a provenance entry to be attached"
    assert para["provenance"][0]["source_name"] == "cim.pdf"
    assert para["provenance"][0]["source_id"] == "sub-1"


def test_attach_substrate_provenance_to_tree_no_match_when_value_absent():
    tree = build_document_from_draft("p1", "Revenue is $10M this quarter.").model_dump(mode="json")
    locks = [{"canonical_key": "Revenue", "value": 10, "unit": "M"}]
    substrate_rows = [
        {
            "id": "sub-1",
            "filename": "cim.pdf",
            "extracted_text": "No relevant numbers in here at all.",
        }
    ]

    updated = attach_substrate_provenance_to_tree(tree, locks, substrate_rows)
    para = updated["body"][0]["children"][0]
    assert not para.get("provenance")


def test_attach_substrate_provenance_to_tree_handles_empty_inputs():
    tree = build_document_from_draft("p1", "Revenue is $10M this quarter.").model_dump(mode="json")
    assert attach_substrate_provenance_to_tree(tree, [], []) == tree
    assert attach_substrate_provenance_to_tree(tree, [{"value": 10}], []) == tree
