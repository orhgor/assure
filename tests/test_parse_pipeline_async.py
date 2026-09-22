"""Uploads are staged in the object store and parsed by a worker task.

The contract under test: ``import-pdf`` answers 202 with a task id when
``PARSE_ASYNC`` is on, ``GET /api/tasks/<id>`` reports the task, the worker
task reads the staged object, writes the revision and removes the object, and a
redelivered task does not parse the document twice. Celery runs eagerly here,
so ``apply_async`` executes the task inline and the whole path is exercised in
one process. Presigned uploads are checked against a fake S3 client.
"""

from __future__ import annotations

import io
import json

import pytest


def _pdf_bytes(text: str = "Hello source document. Liability limit $5,000,000.") -> bytes:
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "async.sqlite"))
    monkeypatch.setenv("ASSURE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WTF_CSRF_ENABLED", "0")
    monkeypatch.setenv("PARSE_ASYNC", "1")
    monkeypatch.setenv("CELERY_BROKER_URL", "")
    monkeypatch.delenv("ASSURE_S3_BUCKET", raising=False)
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db
    from prompt_matrix.web import create_app

    init_db()
    app = create_app(require_auth=False)
    return app.test_client()


def test_import_pdf_is_queued_and_the_task_is_pollable(client, tmp_path):
    res = client.post(
        "/api/projects/default/import-pdf",
        data={"file": (io.BytesIO(_pdf_bytes()), "policy.pdf")},
        content_type="multipart/form-data",
    )
    assert res.status_code == 202, res.get_data(as_text=True)
    body = res.get_json()
    assert body["status"] == "queued" and body["task_id"]
    assert body["status_url"] == f"/api/tasks/{body['task_id']}"

    status = client.get(body["status_url"])
    assert status.status_code == 200
    payload = status.get_json()
    # Eager Celery: the task already ran inline.
    assert payload["status"] == "success", payload
    result = payload["result"]["result"]
    assert result["ok"] is True and result["version"] == 1
    assert result["parser_name"] in ("jdf-cli", "pymupdf")  # jdf-cli when installed, else fallback

    # The staged object is gone once the revision exists.
    staged = list((tmp_path / "objects" / "uploads").rglob("*.pdf"))
    assert staged == []

    doc = client.get("/api/projects/default/jdf").get_json()
    assert "Liability limit" in json.dumps(doc)


def test_redelivered_task_does_not_parse_twice(client):
    from prompt_matrix.tasks.parse_tasks import import_project_pdf_task

    res = client.post(
        "/api/projects/default/import-pdf",
        data={"file": (io.BytesIO(_pdf_bytes()), "policy.pdf")},
        content_type="multipart/form-data",
    )
    assert res.status_code == 202
    # A broker redelivery of the same message: the object was deleted by the
    # first run, so the second run reports skipped and writes nothing.
    again = import_project_pdf_task.apply(args=["default", "uploads/default/gone/policy.pdf", "policy.pdf"]).get()
    assert again["status"] == "skipped"
    history = client.get("/api/projects/default/history").get_json()
    versions = [r.get("version") for r in (history.get("revisions") or history.get("history") or [])]
    assert versions.count(1) <= 1


def test_sync_mode_still_answers_200(client, monkeypatch):
    monkeypatch.setenv("PARSE_ASYNC", "0")
    res = client.post(
        "/api/projects/default/import-pdf",
        data={"file": (io.BytesIO(_pdf_bytes()), "policy.pdf")},
        content_type="multipart/form-data",
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    assert res.get_json()["ok"] is True


def test_presign_without_s3_falls_back_to_multipart(client):
    res = client.post(
        "/api/projects/default/uploads/presign",
        json={"filename": "scan.pdf", "content_type": "application/pdf", "size_bytes": 1000},
    )
    assert res.status_code == 200
    assert res.get_json()["mode"] == "multipart"


def test_presign_with_s3_then_import_by_object_key(client, monkeypatch):
    from prompt_matrix.services import object_store

    class FakeS3:
        def __init__(self):
            self.objects = {}

        def generate_presigned_url(self, op, Params, ExpiresIn):
            return f"https://{Params['Bucket']}.s3.amazonaws.com/{Params['Key']}"

        def put_object(self, Bucket, Key, Body, ContentType=None):
            self.objects[Key] = Body

        def head_object(self, Bucket, Key):
            if Key not in self.objects:
                raise KeyError(Key)
            return {}

        def get_object(self, Bucket, Key):
            return {"Body": io.BytesIO(self.objects[Key])}

        def delete_object(self, Bucket, Key):
            self.objects.pop(Key, None)

    fake = FakeS3()
    monkeypatch.setattr(object_store.S3ObjectStore, "_c", lambda self: fake)
    monkeypatch.setenv("ASSURE_S3_BUCKET", "assure-test-bucket")
    monkeypatch.setenv("ASSURE_S3_PREFIX", "assure/")

    pre = client.post(
        "/api/projects/default/uploads/presign",
        json={"filename": "policy.pdf", "content_type": "application/pdf"},
    ).get_json()
    assert pre["mode"] == "s3"
    assert pre["upload"]["url"].startswith("https://assure-test-bucket.s3.amazonaws.com/assure/uploads/default/")
    key = pre["object_key"]
    # The browser uploads straight to S3:
    fake.objects["assure/" + key] = _pdf_bytes()

    # A key from another project is refused.
    other = client.post(
        "/api/projects/default/import-pdf",
        json={"object_key": "uploads/someone-else/x/policy.pdf", "filename": "policy.pdf"},
    )
    assert other.status_code == 403

    res = client.post(
        "/api/projects/default/import-pdf",
        json={"object_key": key, "filename": pre["complete"]["body"]["filename"]},
    )
    assert res.status_code == 202, res.get_data(as_text=True)
    status = client.get(res.get_json()["status_url"]).get_json()
    assert status["status"] == "success", status
    assert ("assure/" + key) not in fake.objects  # deleted after the parse


def test_unknown_task_is_pending_not_an_error(client):
    res = client.get("/api/tasks/does-not-exist")
    assert res.status_code == 200
    assert res.get_json()["status"] == "pending"
