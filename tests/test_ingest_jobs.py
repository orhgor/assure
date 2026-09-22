"""Ingest jobs: the durable, watchable record of a queued document.

Contract: an upload creates a job row before the worker runs; the worker
advances it through the stages and records the parser, pages, OCR confidence,
Z3 verdict, revision and artifact; the list/detail/report routes expose that;
``GET /api/tasks/<id>`` joins it; a failed job can be retried only while its
staged object still exists.
"""

from __future__ import annotations

import io

import pytest


def _pdf_bytes(text: str = "Coverage limit $5,000,000 per occurrence.") -> bytes:
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "jobs.sqlite"))
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
    return create_app(require_auth=False).test_client()


def _upload(client, project="default", name="policy.pdf"):
    return client.post(
        f"/api/projects/{project}/import-pdf",
        data={"file": (io.BytesIO(_pdf_bytes()), name)},
        content_type="multipart/form-data",
    )


def test_upload_creates_a_job_that_reaches_done_with_facts(client):
    res = _upload(client)
    assert res.status_code == 202
    body = res.get_json()
    assert body["job_id"] and body["job_url"].endswith(body["job_id"])

    detail = client.get(body["job_url"]).get_json()
    job = detail["job"]
    assert job["status"] == "done", job
    stages = [h["stage"] for h in job["stage_history"]]
    assert stages[0] == "queued" and stages[-1] == "done"
    assert {"fetching", "parsing", "verifying", "persisting"} <= set(stages)
    assert job["parser_name"] in ("jdf-cli", "pymupdf")
    assert job["page_count"] == 1
    assert job["revision_version"] == 1 and job["revision_id"]
    assert job["z3_status"] in ("PASS", "VIOLATION", "TIMEOUT", "ERROR")
    assert job["redhat_status"] in ("complete", "skipped")
    assert job["omp_artifact_id"]
    assert job["duration_ms"] is not None and job["finished_at"]
    assert job["task_id"] == body["task_id"]


def test_list_and_stats(client):
    _upload(client, name="a.pdf")
    _upload(client, name="b.pdf")
    listing = client.get("/api/projects/default/ingest-jobs").get_json()
    assert listing["ok"] and len(listing["jobs"]) == 2
    assert listing["stats"]["done"] == 2 and listing["stats"]["active"] == 0
    assert listing["active"] is False
    assert sum(listing["stats"]["z3"].values()) == 2
    only_done = client.get("/api/projects/default/ingest-jobs?status=done").get_json()
    assert len(only_done["jobs"]) == 2
    active = client.get("/api/projects/default/ingest-jobs?status=active").get_json()
    assert active["jobs"] == []


def test_report_joins_the_revision_verification(client):
    body = _upload(client).get_json()
    report = client.get(body["job_url"] + "/report").get_json()
    assert report["ok"]
    v = report["verification"]
    assert v["revision_id"] == report["job"]["revision_id"]
    assert v["version"] == 1 and v["node_count"] >= 1
    assert isinstance(v["z3"], dict) and v["z3"]["z3_status"] == report["job"]["z3_status"]
    assert isinstance(v["z3"].get("violations"), list)
    assert report["omp"]["artifact_id"] == report["job"]["omp_artifact_id"]


def test_task_status_joins_the_job(client):
    body = _upload(client).get_json()
    status = client.get(body["status_url"]).get_json()
    assert status["job"]["job_id"] == body["job_id"]
    assert status["status"] == "success"


def test_job_belongs_to_its_project(client):
    body = _upload(client).get_json()
    assert client.get(f"/api/projects/other/ingest-jobs/{body['job_id']}").status_code == 404


def test_retry_refuses_done_jobs_and_expired_objects(client, tmp_path):
    body = _upload(client).get_json()
    res = client.post(body["job_url"] + "/retry")
    assert res.status_code == 409

    from prompt_matrix.db.ingest_jobs_repository import advance, create_job
    from prompt_matrix.db.jdf_repository import ensure_project

    ensure_project("default")
    job_id = create_job("default", kind="import_pdf", filename="lost.pdf", object_key="uploads/default/x/lost.pdf")
    advance(job_id, "failed", error="boom")
    res = client.post(f"/api/projects/default/ingest-jobs/{job_id}/retry")
    assert res.status_code == 409
    assert "upload it again" in res.get_json()["error"]


def test_retry_requeues_when_the_object_is_still_staged(client, tmp_path):
    from prompt_matrix.db.ingest_jobs_repository import advance, create_job, get_job
    from prompt_matrix.db.jdf_repository import ensure_project
    from prompt_matrix.services.object_store import get_object_store, upload_key

    ensure_project("default")
    key = upload_key("default", "again.pdf")
    get_object_store().put_bytes(key, _pdf_bytes(), content_type="application/pdf")
    job_id = create_job("default", kind="import_pdf", filename="again.pdf", object_key=key)
    advance(job_id, "failed", error="worker died")
    res = client.post(f"/api/projects/default/ingest-jobs/{job_id}/retry")
    assert res.status_code == 202, res.get_data(as_text=True)
    job = get_job(job_id)
    assert job["status"] == "done" and job["error"] in (None, "")
    stages = [h["stage"] for h in job["stage_history"]]
    assert stages.count("queued") == 2  # created, then re-queued


def test_failed_parse_records_the_error(client, monkeypatch):

    def boom(*a, **k):
        raise RuntimeError("tree invalid")

    monkeypatch.setattr("prompt_matrix.models.jdf.parse_document", boom)
    body = _upload(client).get_json()
    job = client.get(body["job_url"]).get_json()["job"]
    assert job["status"] == "failed"
    assert "tree invalid" in (job["error"] or "")
    status = client.get(body["status_url"]).get_json()
    assert status["status"] == "failure"


def test_vault_upload_job_records_the_verification_verdict(client, monkeypatch):
    """A ``substrate_upload`` job ends with a Z3 verdict, not a blank.

    ``ingest_substrate_file`` ran verification but never returned it, and the
    worker read ``entry["verification"]`` for the job columns — so every vault
    upload's job showed ``z3_status None`` (observed: CP00101012-1.pdf, jdf-cli,
    16 pages). The function now records ``verifying``/``persisting`` with the
    verdict itself and returns it for the terminal stage. The source is also
    indexed for search from the worker path (same function as the sync route).
    """
    monkeypatch.setenv("SUBSTRATE_ASYNC_UPLOAD", "1")
    monkeypatch.delenv("OMP_SERVER", raising=False)
    text = (
        "The wind and hail deductible is twenty five thousand dollars.\n\n"
        "Coverage territory is Suffolk County."
    )
    res = client.post(
        "/api/projects/default/substrate/upload",
        data={"file": (io.BytesIO(text.encode("utf-8")), "terms.md")},
        content_type="multipart/form-data",
    )
    assert res.status_code == 202, res.get_data(as_text=True)
    body = res.get_json()

    job = client.get(body["job_url"]).get_json()["job"]
    assert job["kind"] == "substrate_upload"
    assert job["status"] == "done", job
    stages = [h["stage"] for h in job["stage_history"]]
    assert {"fetching", "parsing", "verifying", "persisting", "done"} <= set(stages)
    assert stages.index("verifying") < stages.index("persisting") < stages.index("done")
    assert job["z3_status"] in ("PASS", "VIOLATION", "TIMEOUT", "ERROR")
    assert job["z3_violation_count"] is not None
    assert job["redhat_status"] in ("complete", "skipped")
    assert job["substrate_file_id"]
    assert job["page_count"] == 1

    found = client.post(
        "/api/projects/default/jdf/search", json={"query": "hail deductible"}
    ).get_json()
    assert found["count"] == 1
    assert found["results"][0]["meta"]["substrate_file_id"] == job["substrate_file_id"]
