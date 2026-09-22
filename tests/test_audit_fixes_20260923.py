"""Regression tests for the 2026-09-23 backend audit fixes."""

from __future__ import annotations

import pytest
from z3 import unknown

from prompt_matrix.db.connection import init_db
from prompt_matrix.db.ingest_jobs_repository import advance, create_job, get_job, list_jobs, mark_stale
from prompt_matrix.db.jdf_repository import ensure_project
from prompt_matrix.history import get_db
from prompt_matrix.ledger.truth_engine import TruthLedgerEngine, Z3Timeout


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "audit.sqlite"))
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    init_db()
    yield


# 1. Z3 `unknown` (solver timeout) is never a PASS ------------------------------
def test_z3_unknown_raises_timeout(monkeypatch):
    engine = TruthLedgerEngine()
    engine.lock_metric("limit", 5.0)
    monkeypatch.setattr(engine._solver, "check", lambda *a, **k: unknown)
    monkeypatch.setattr(engine._solver, "reason_unknown", lambda: "timeout", raising=False)
    with pytest.raises(Z3Timeout):
        engine.verify_metric("limit", 7.0)


def test_verification_reports_timeout_not_pass(monkeypatch):
    from prompt_matrix.services import full_context_scan, verification

    def boom(*_a, **_k):
        raise Z3Timeout("solver timeout")

    monkeypatch.setattr(full_context_scan, "_scan_z3_document", boom)
    out = verification._run_z3({"document_id": "d", "body": [{"id": "p1", "type": "paragraph", "content": "x=1"}]})
    assert out["z3_status"] == "TIMEOUT"


def test_verify_locks_timeout_is_timeout(monkeypatch):
    from prompt_matrix.routers import draft as draft_mod

    class _Truth:
        def lock_metric(self, *a, **k):
            return None

        def validate_entities(self, metrics):
            raise Z3Timeout("solver timeout")

        def verify_metric(self, *a, **k):
            raise Z3Timeout("solver timeout")

    monkeypatch.setattr(draft_mod, "TruthLedgerEngine", lambda *a, **k: _Truth())
    result = draft_mod.verify_locks(
        [{"canonical_key": "policy liability limit", "value": 5000000, "metric": "policy liability limit", "confidence": 0.9}],
        "Policy liability limit: 5000000.",
    )
    assert result["status"] == "TIMEOUT"
    assert result.get("skip_reason")


# 3. INSERT OR REPLACE conflicts on the unique key the statement supplies -------
def test_replace_by_unique_index_document_locks(db):
    from prompt_matrix.db.document_lock_repository import create_document_lock, fetch_lock_for_version

    ensure_project("p-lock", "Lock")
    tree = {"document_id": "p-lock", "body": []}
    first = create_document_lock("p-lock", tree, locked_by="a", version=1)
    second = create_document_lock("p-lock", tree, locked_by="b", version=1)
    assert first["id"] != second["id"]
    row = fetch_lock_for_version("p-lock", 1)
    assert row["locked_by"] == "b"
    count = get_db().execute("SELECT COUNT(*) FROM document_locks WHERE project_id = ?", ("p-lock",)).fetchone()[0]
    assert count == 1


# 4/15/16. Ingest jobs: terminal guard, retry reset, stale healing --------------
def test_terminal_job_ignores_late_stage_writes(db):
    ensure_project("p-j", "Jobs")
    job = create_job("p-j", kind="import_pdf", filename="a.pdf", object_key="uploads/p-j/x/a.pdf", size_bytes=10)
    advance(job, "parsing")
    advance(job, "done", revision_id="rev-1")
    advance(job, "fetching")
    advance(job, "skipped", error="staged object not found")
    row = get_job(job)
    assert row["status"] == "done"
    assert row["revision_id"] == "rev-1"


def test_retry_resets_the_failure_clock(db):
    ensure_project("p-r", "Retry")
    job = create_job("p-r", kind="import_pdf", filename="a.pdf", object_key="uploads/p-r/x/a.pdf", size_bytes=10)
    advance(job, "parsing")
    advance(job, "failed", error="boom")
    assert get_job(job)["finished_at"]
    advance(job, "queued", error=None)
    row = get_job(job)
    assert row["status"] == "queued"
    assert row["finished_at"] is None
    assert row["error"] is None


def test_stale_active_job_is_failed_on_listing(db):
    ensure_project("p-s", "Stale")
    job = create_job("p-s", kind="import_pdf", filename="a.pdf", object_key="uploads/p-s/x/a.pdf", size_bytes=10)
    advance(job, "parsing")
    db = get_db()  # one handle: get_db() outside a request may hand out a fresh connection per call
    db.execute("UPDATE ingest_jobs SET updated_at = datetime('now', '-2 hours') WHERE job_id = ?", (job,))
    db.commit()
    assert mark_stale() >= 1
    row = get_job(job)
    assert row["status"] == "failed"
    assert "worker lost" in (row["error"] or "")
    listed = list_jobs("p-s")
    jobs = listed["jobs"] if isinstance(listed, dict) else listed
    assert any(j["job_id"] == job and j["status"] == "failed" for j in jobs)


# 8. S3 exists(): only a 404 is "gone" -----------------------------------------
def test_s3_exists_reraises_non_404(monkeypatch):
    from botocore.exceptions import ClientError

    from prompt_matrix.services.object_store import S3ObjectStore

    store = S3ObjectStore.__new__(S3ObjectStore)
    store.bucket = "b"
    store.prefix = ""

    class _Client:
        def __init__(self, code, status):
            self.code, self.status = code, status

        def head_object(self, **_k):
            raise ClientError({"Error": {"Code": self.code}, "ResponseMetadata": {"HTTPStatusCode": self.status}}, "HeadObject")

    monkeypatch.setattr(store, "_c", lambda: _Client("404", 404))
    monkeypatch.setattr(store, "_k", lambda k: k)
    assert store.exists("x") is False
    monkeypatch.setattr(store, "_c", lambda: _Client("AccessDenied", 403))
    with pytest.raises(ClientError):
        store.exists("x")


# 19. presign rejects a non-numeric size ---------------------------------------
def test_presign_bad_size_is_400(db, monkeypatch):
    monkeypatch.setenv("ASSURE_REQUIRE_LOGIN", "false")
    monkeypatch.setenv("WTF_CSRF_ENABLED", "0")
    from prompt_matrix.web import create_app

    app = create_app(require_auth=False)
    ensure_project("p-pre", "Presign")
    client = app.test_client()
    resp = client.post(
        "/api/projects/p-pre/uploads/presign",
        json={"filename": "a.pdf", "content_type": "application/pdf", "size_bytes": "abc"},
    )
    assert resp.status_code == 400
