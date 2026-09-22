"""OMP artifact storage: the row is the record, the JSON is mirrored to the
object store — S3 when configured, the local objects/ directory otherwise."""

from __future__ import annotations

import json

import pytest


class _FakeS3:
    """Enough of boto3's S3 client for put/get/head/delete/presign."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.objects[(Bucket, Key)] = Body

    def get_object(self, Bucket, Key):
        import io

        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def head_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise KeyError(Key)
        return {}

    def delete_object(self, Bucket, Key):
        self.objects.pop((Bucket, Key), None)

    def generate_presigned_url(self, op, Params, ExpiresIn):
        return f"https://{Params['Bucket']}.s3.amazonaws.com/{Params['Key']}?X-Amz-Expires={ExpiresIn}"


@pytest.fixture
def omp_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "omp_s3.db"))
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db

    init_db()
    for var in ("ASSURE_S3_BACKEND", "ASSURE_S3_BUCKET", "ASSURE_S3_PREFIX"):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch, tmp_path


@pytest.fixture
def fake_s3(monkeypatch):
    from prompt_matrix.services import object_store

    fake = _FakeS3()
    monkeypatch.setattr(object_store.S3ObjectStore, "_c", lambda self: fake)
    return fake


def _artifact(project_id: str = "p1"):
    from prompt_matrix.models.omp import OMPArtifact

    return OMPArtifact(
        artifact_id="omp-test-0001",
        project_id=project_id,
        artifact_type="parse",
        payload={"text": "hello"},
    )


def test_local_default_returns_artifact_id(omp_env):
    from prompt_matrix.services.omp import store_omp_artifact

    assert store_omp_artifact("p1", _artifact()) == "omp-test-0001"


def test_local_mirrors_json_under_objects_dir(omp_env):
    """Without a bucket the JSON lands in <data dir>/objects/omp/<project>/<id>.json."""
    from prompt_matrix.services.omp import store_omp_artifact

    store_omp_artifact("p1", _artifact())
    matches = list((omp_env[1] / "objects" / "omp").rglob("omp-test-0001.json"))
    assert matches, "local backend must mirror the artifact JSON"
    assert json.loads(matches[0].read_text())["payload"] == {"text": "hello"}


def test_s3_without_bucket_explicit_raises(omp_env):
    from prompt_matrix.services.omp import store_omp_artifact

    with pytest.raises(ValueError, match="s3_bucket required"):
        store_omp_artifact("p1", _artifact(), storage_backend="s3")


def test_s3_backend_writes_the_object(omp_env, fake_s3):
    """backend="s3" + bucket: a real put_object at s3://bucket/prefix/omp/<project>/<id>.json."""
    from prompt_matrix.services.omp import load_omp_artifact, store_omp_artifact

    returned = store_omp_artifact(
        "p1",
        _artifact(),
        storage_backend="s3",
        s3_bucket="client-insurance-bucket",
        s3_prefix="assure/artifacts/",
    )
    assert returned == "omp-test-0001"
    key = ("client-insurance-bucket", "assure/artifacts/omp/p1/omp-test-0001.json")
    assert key in fake_s3.objects
    assert json.loads(fake_s3.objects[key])["artifact_id"] == "omp-test-0001"
    # The row is still the record the app reads.
    assert load_omp_artifact("omp-test-0001") is not None


def test_env_bucket_selects_s3_without_caller_params(omp_env, fake_s3):
    """ASSURE_S3_BUCKET alone switches the mirror to S3: config, not code."""
    from prompt_matrix.services.omp import store_omp_artifact

    monkeypatch, _tmp = omp_env
    monkeypatch.setenv("ASSURE_S3_BUCKET", "client-bucket")
    monkeypatch.setenv("ASSURE_S3_PREFIX", "assure/")
    assert store_omp_artifact("p2", _artifact("p2")) == "omp-test-0001"
    assert ("client-bucket", "assure/omp/p2/omp-test-0001.json") in fake_s3.objects


def test_env_s3_without_bucket_falls_back_not_raises(omp_env, caplog):
    from prompt_matrix.services.omp import store_omp_artifact

    monkeypatch, _tmp = omp_env
    monkeypatch.setenv("ASSURE_S3_BACKEND", "s3")
    with caplog.at_level("WARNING", logger="prompt_matrix.services.omp"):
        assert store_omp_artifact("p3", _artifact("p3")) == "omp-test-0001"
    assert any("falling back to local" in r.getMessage() for r in caplog.records)


def test_s3_mirror_failure_does_not_fail_the_store(omp_env, fake_s3, monkeypatch, caplog):
    """Mirroring is best-effort: the row is written even if S3 is down."""
    from prompt_matrix.services import object_store
    from prompt_matrix.services.omp import load_omp_artifact, store_omp_artifact

    def boom(self, key, data, content_type="application/octet-stream"):
        raise RuntimeError("s3 down")

    monkeypatch.setattr(object_store.S3ObjectStore, "put_bytes", boom)
    monkeypatch.setenv("ASSURE_S3_BUCKET", "client-bucket")
    with caplog.at_level("ERROR", logger="prompt_matrix.services.omp"):
        assert store_omp_artifact("p4", _artifact("p4")) == "omp-test-0001"
    assert load_omp_artifact("omp-test-0001") is not None
    assert any("mirror failed" in r.getMessage() for r in caplog.records)
