"""S3-ready OMP storage interface: params, env config, local fallback.

The S3 backend is a stub by design (Phase 3, show): it logs and falls back
to local. These tests verify the interface and config exist — never that
S3 writes work.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def omp_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "omp_s3.db"))
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db

    init_db()
    # Clear any inherited S3 env so each test controls the config.
    for var in ("ASSURE_S3_BACKEND", "ASSURE_S3_BUCKET", "ASSURE_S3_PREFIX"):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch, tmp_path


def _artifact(project_id: str = "p1"):
    from prompt_matrix.models.omp import OMPArtifact

    return OMPArtifact(
        artifact_id="omp-test-0001",
        project_id=project_id,
        artifact_type="parse",
        payload={"text": "hello"},
    )


def test_local_default_returns_artifact_id(omp_env):
    """Default behavior: local storage, artifact_id returned."""
    from prompt_matrix.services.omp import store_omp_artifact

    assert store_omp_artifact("p1", _artifact()) == "omp-test-0001"


def test_local_writes_disk_json(omp_env):
    """The local backend persists the artifact JSON on the instance disk."""
    from prompt_matrix.services.omp import store_omp_artifact

    store_omp_artifact("p1", _artifact())
    matches = list((omp_env[1] / "omp_artifacts").rglob("omp-test-0001.json"))
    assert matches, "local backend must write the artifact JSON to disk"


def test_s3_without_bucket_explicit_raises(omp_env):
    """An explicit s3 decision with no bucket is a caller error."""
    from prompt_matrix.services.omp import store_omp_artifact

    with pytest.raises(ValueError, match="s3_bucket required"):
        store_omp_artifact("p1", _artifact(), storage_backend="s3")


def test_s3_stub_logs_and_falls_back_to_local(omp_env, caplog):
    """backend="s3" + bucket: logs the would-be key, stores locally.

    This is the placeholder contract — the artifact lands on local disk, the
    return is the local artifact_id, and the log says S3 is not implemented.
    No S3 write happens.
    """
    import logging

    from prompt_matrix.services.omp import store_omp_artifact

    with caplog.at_level("INFO", logger="prompt_matrix.services.omp"):
        returned = store_omp_artifact(
            "p1",
            _artifact(),
            storage_backend="s3",
            s3_bucket="client-insurance-bucket",
            s3_prefix="assure/artifacts/",
        )
    assert returned == "omp-test-0001"  # local fallback, not an S3 URI
    assert any("not yet implemented" in r.getMessage() for r in caplog.records)
    # The URI shape the client phase will write:
    # s3://bucket/prefix/{project}/{id}.json
    assert any(
        "s3://client-insurance-bucket/assure/artifacts/p1/omp-test-0001.json"
        in r.getMessage()
        for r in caplog.records
    )
    # Local persistence actually happened (show fallback).
    matches = list((omp_env[1] / "omp_artifacts").rglob("omp-test-0001.json"))
    assert matches
def test_env_backend_selects_s3_path(omp_env, caplog):
    """ASSURE_S3_BACKEND=s3 + ASSURE_S3_BUCKET → the S3 branch runs without
    any caller params: switching backends is config, not code."""
    import logging

    from prompt_matrix.services.omp import store_omp_artifact

    monkeypatch, _tmp = omp_env
    monkeypatch.setenv("ASSURE_S3_BACKEND", "s3")
    monkeypatch.setenv("ASSURE_S3_BUCKET", "client-bucket")
    with caplog.at_level("INFO", logger="prompt_matrix.services.omp"):
        assert store_omp_artifact("p2", _artifact("p2")) == "omp-test-0001"
    assert any("not yet implemented" in r.getMessage() for r in caplog.records)


def test_env_s3_without_bucket_falls_back_not_raises(omp_env, caplog):
    """An env misconfig must not fail an ingest: warn + local."""
    import logging

    from prompt_matrix.services.omp import store_omp_artifact

    monkeypatch, _tmp = omp_env
    monkeypatch.setenv("ASSURE_S3_BACKEND", "s3")
    with caplog.at_level("WARNING", logger="prompt_matrix.services.omp"):
        assert store_omp_artifact("p3", _artifact("p3")) == "omp-test-0001"
    assert any("falling back to local" in r.getMessage() for r in caplog.records)


def test_vault_row_accepts_s3_uri_format(tmp_path, monkeypatch):
    """The vault row's omp_artifact_id is a text field: it stores the local
    artifact_id today and an s3://bucket/key URI in the future — same field,
    different value format, no schema change."""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "omp_s3_vault.db"))
    import prompt_matrix.history as history_mod
    from prompt_matrix.db.connection import init_db
    from prompt_matrix.db.substrate_repository import (
        list_substrate_for_project,
        upsert_substrate_entry,
    )

    history_mod.DB_PATH = history_mod._resolve_db_path()
    init_db()
    from prompt_matrix.db.jdf_repository import ensure_project
    from prompt_matrix.db.substrate_repository import (
        list_substrate_for_project,
        upsert_substrate_entry,
    )

    ensure_project("s3proj")  # vault rows FK to projects
    s3_uri = (
        "s3://client-insurance-bucket/assure/artifacts/project-123/omp-abc123.json"
    )
    upsert_substrate_entry(
        "s3proj",
        filename="policy.pdf",
        page_count=1,
        extracted_text="A source with enough text for the vault.",
        omp_artifact_id=s3_uri,
    )
    row = list_substrate_for_project("s3proj")[0]
    assert row["omp_artifact_id"] == s3_uri