"""AWS/S3 integration: .env first, then credentials saved from the Sources
panel (encrypted), then the machine role — and a real bucket probe, never a
guess. boto3 is replaced by a fake that records what it was asked, so no
network is touched here; the contract under test is resolution and reporting.
"""

from __future__ import annotations

import os

import pytest


class _FakeS3:
    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls = []

    def head_bucket(self, Bucket):
        self.calls.append(("head_bucket", Bucket))
        if isinstance(self.behaviour, Exception):
            raise self.behaviour
        return {}


class _FakeSTS:
    def get_caller_identity(self):
        return {"Account": "123456789012", "Arn": "arn:aws:iam::123456789012:user/assure-local"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "aws.sqlite"))
    monkeypatch.setenv("ASSURE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WTF_CSRF_ENABLED", "0")
    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION",
                "ASSURE_S3_BUCKET", "ASSURE_S3_PREFIX", "ENCRYPTION_KEY"):
        monkeypatch.delenv(key, raising=False)
    for key in list(os.environ):
        if key.startswith("_ASSURE_AWS_FROM_DB_"):
            monkeypatch.delenv(key, raising=False)
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db

    init_db()
    yield monkeypatch
    # save() exports the entered credentials into os.environ on purpose (that is
    # how boto3 sees them); monkeypatch.delenv records nothing for a variable
    # that was absent, so the export would outlive this test and send every
    # later upload to real S3 with fake keys. Undo it explicitly.
    from prompt_matrix.services import aws_integration

    try:
        aws_integration.clear()
    except Exception:
        pass
    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION",
                "ASSURE_S3_BUCKET", "ASSURE_S3_PREFIX"):
        os.environ.pop(key, None)
    for key in list(os.environ):
        if key.startswith("_ASSURE_AWS_FROM_DB_"):
            os.environ.pop(key, None)


def _fake_boto3(monkeypatch, behaviour=None):
    import boto3

    fake_s3 = _FakeS3(behaviour)

    def client(service, **kwargs):
        return fake_s3 if service == "s3" else _FakeSTS()

    monkeypatch.setattr(boto3, "client", client)
    return fake_s3


def test_status_without_bucket_is_local_and_says_why(env):
    from prompt_matrix.services import aws_integration

    s = aws_integration.status()
    assert s["configured"] is False and s["storage"] == "local"
    assert s["credential_source"] == "role"
    assert "No S3 bucket" in s["error"]


def test_save_stores_secret_encrypted_and_applies_to_environment(env):
    from prompt_matrix.history import borrowed_connection
    from prompt_matrix.services import aws_integration

    _fake_boto3(env)
    result = aws_integration.save(
        access_key_id="AKIAEXAMPLE12345678",
        secret_access_key="s3cr3t/verysecret",
        region="eu-central-1",
        bucket="assure-test-objects",
        prefix="assure/",
    )
    assert result["configured"] and result["reachable"] is True
    assert result["credential_source"] == "database"
    assert result["identity"]["arn"].endswith("user/assure-local")
    assert result["access_key_id_hint"] == "AKIA…5678"
    # The secret never sits in clear in the database.
    with borrowed_connection() as conn:
        row = conn.execute("SELECT value_json, secret_enc FROM integration_settings WHERE name='aws'").fetchone()
    assert "s3cr3t" not in (row[0] or "") and "s3cr3t" not in (row[1] or "")
    assert aws_integration.load_saved()["secret_access_key"] == "s3cr3t/verysecret"
    # Applied to the process so boto3 clients (S3, SQS, Textract) all see it.
    assert os.environ["AWS_ACCESS_KEY_ID"] == "AKIAEXAMPLE12345678"
    assert os.environ["ASSURE_S3_BUCKET"] == "assure-test-objects"
    from prompt_matrix.services.object_store import get_object_store

    assert get_object_store().backend == "s3"


def test_env_credentials_win_over_saved_ones(env):
    from prompt_matrix.services import aws_integration

    _fake_boto3(env)
    aws_integration.save(access_key_id="AKIASAVED0000000000", secret_access_key="saved", region="eu-central-1", bucket="saved-bucket")
    aws_integration.clear()
    env.setenv("AWS_ACCESS_KEY_ID", "AKIAENV000000000000")
    env.setenv("AWS_SECRET_ACCESS_KEY", "env-secret")
    env.setenv("ASSURE_S3_BUCKET", "env-bucket")
    aws_integration.save(access_key_id="AKIASAVED0000000000", secret_access_key="saved", region="eu-central-1", bucket="saved-bucket")
    s = aws_integration.status()
    assert s["credential_source"] == "env"
    assert s["bucket"] == "env-bucket"
    assert os.environ["AWS_ACCESS_KEY_ID"] == "AKIAENV000000000000"


def test_resave_without_secret_keeps_the_stored_secret(env):
    from prompt_matrix.services import aws_integration

    _fake_boto3(env)
    aws_integration.save(access_key_id="AKIAEXAMPLE12345678", secret_access_key="keep-me", region="eu-central-1", bucket="b1")
    aws_integration.save(access_key_id="AKIAEXAMPLE12345678", secret_access_key="", region="eu-central-1", bucket="b2")
    saved = aws_integration.load_saved()
    assert saved["secret_access_key"] == "keep-me" and saved["bucket"] == "b2"


def test_emptying_the_key_id_unapplies_the_saved_credentials(env):
    """Removing the key on the Sources panel must remove it from the process too.

    Before 2026-09-23 ``save()`` kept the old secret beside the empty key id and
    ``apply_to_environment`` only ever added variables, so boto3 went on signing
    with ``AKIAEXAMPLE12345678`` after the UI reported the machine role.
    """
    from prompt_matrix.services import aws_integration

    _fake_boto3(env)
    aws_integration.save(access_key_id="AKIAEXAMPLE12345678", secret_access_key="old-secret", region="eu-central-1", bucket="b1")
    assert os.environ["AWS_ACCESS_KEY_ID"] == "AKIAEXAMPLE12345678"
    assert os.environ["AWS_SECRET_ACCESS_KEY"] == "old-secret"

    result = aws_integration.save(access_key_id="", secret_access_key="", region="eu-central-1", bucket="b1")

    saved = aws_integration.load_saved()
    assert saved["access_key_id"] == "" and saved["secret_access_key"] == ""
    assert "AWS_ACCESS_KEY_ID" not in os.environ
    assert "AWS_SECRET_ACCESS_KEY" not in os.environ
    assert "AWS_SESSION_TOKEN" not in os.environ
    assert result["credential_source"] == "role"
    assert aws_integration.credential_source() == "role"
    # The bucket entered on the screen still applies (role branch).
    assert os.environ["ASSURE_S3_BUCKET"] == "b1"
    assert os.environ["AWS_DEFAULT_REGION"] == "eu-central-1"


def test_a_new_key_id_without_a_secret_does_not_inherit_the_old_secret(env):
    """A secret belongs to one key id; carrying it to another makes a pair that
    can never sign."""
    from prompt_matrix.services import aws_integration

    _fake_boto3(env)
    aws_integration.save(access_key_id="AKIAEXAMPLE12345678", secret_access_key="old-secret", region="eu-central-1", bucket="b1")
    aws_integration.save(access_key_id="AKIAOTHERKEY00000000", secret_access_key="", region="eu-central-1", bucket="b1")
    saved = aws_integration.load_saved()
    assert saved["access_key_id"] == "AKIAOTHERKEY00000000" and saved["secret_access_key"] == ""
    assert "AWS_ACCESS_KEY_ID" not in os.environ and "AWS_SECRET_ACCESS_KEY" not in os.environ
    assert aws_integration.credential_source() == "role"


def test_apply_to_environment_reflects_the_row_as_it_is_now(env):
    """Re-applying after the row changed replaces what the previous apply set."""
    from prompt_matrix.services import aws_integration

    _fake_boto3(env)
    aws_integration.save(access_key_id="AKIAEXAMPLE12345678", secret_access_key="s", region="eu-central-1", bucket="first")
    aws_integration.save(access_key_id="AKIAEXAMPLE12345678", secret_access_key="", region="us-east-1", bucket="second")
    assert aws_integration.apply_to_environment() == "database"
    assert os.environ["ASSURE_S3_BUCKET"] == "second"
    assert os.environ["AWS_DEFAULT_REGION"] == "us-east-1"
    assert os.environ["AWS_SECRET_ACCESS_KEY"] == "s"  # same key id: the stored secret is kept


def test_unreachable_bucket_is_reported_with_a_plain_reason(env):
    from botocore.exceptions import ClientError

    from prompt_matrix.services import aws_integration

    denied = ClientError({"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadBucket")
    _fake_boto3(env, behaviour=denied)
    result = aws_integration.save(access_key_id="AKIAEXAMPLE12345678", secret_access_key="x", region="eu-central-1", bucket="locked")
    assert result["reachable"] is False
    assert "Access denied" in result["error"]


def test_routes_validate_save_and_report(env):
    from prompt_matrix.web import create_app

    fake = _fake_boto3(env)
    client = create_app(require_auth=False).test_client()

    before = client.get("/api/integrations/aws").get_json()
    assert before["ok"] and before["configured"] is False

    bad = client.put("/api/integrations/aws", json={"region": "eu-central-1"})
    assert bad.status_code == 400 and "bucket" in bad.get_json()["error"]
    bad_key = client.put("/api/integrations/aws", json={"bucket": "b", "region": "r", "access_key_id": "nope", "secret_access_key": "s"})
    assert bad_key.status_code == 400

    ok = client.put(
        "/api/integrations/aws",
        json={"bucket": "assure-objects", "region": "eu-central-1", "access_key_id": "AKIAEXAMPLE12345678", "secret_access_key": "s"},
    )
    assert ok.status_code == 200, ok.get_data(as_text=True)
    body = ok.get_json()
    assert body["reachable"] is True and body["storage"] == "s3"
    assert ("head_bucket", "assure-objects") in fake.calls
    assert "secret_access_key" not in body

    after = client.get("/api/integrations/aws").get_json()
    assert after["configured"] and after["credential_source"] == "database"

    gone = client.delete("/api/integrations/aws").get_json()
    assert gone["configured"] is False
