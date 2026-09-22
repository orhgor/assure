"""AWS integration: where the app's S3/SQS/Textract credentials come from.

Three sources, in this order, and the resolved one is reported to the UI so an
operator can see *why* uploads are going where they go:

1. **Environment** — ``AWS_ACCESS_KEY_ID`` / ``AWS_SECRET_ACCESS_KEY``,
   ``AWS_DEFAULT_REGION``,
   ``ASSURE_S3_BUCKET`` / ``ASSURE_S3_PREFIX`` from ``.env``.
2. **Database** — values entered on the Sources panel (``PUT
   /api/integrations/aws``), stored in ``integration_settings`` with the secret
   Fernet-encrypted. These are applied to the process environment at startup
   (web and worker) so every boto3 client, including Celery's SQS transport,
   sees the same identity.
3. **Machine role** — nothing configured: boto3's default chain, i.e. the ECS
   task role or the EC2 instance profile. This is the production shape.

``status()`` performs a real ``head_bucket`` against the configured bucket.
There is no mock path: without a reachable bucket the answer says so, and the
object store stays local-disk (which the UI shows as a warning).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

INTEGRATION_NAME = "aws"
_ENV_KEYS = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION")


# --------------------------------------------------------------------------- #
# Encryption: ENCRYPTION_KEY when set, else a key derived from the app secret
# --------------------------------------------------------------------------- #


def _cipher():
    from cryptography.fernet import Fernet

    raw = (os.environ.get("ENCRYPTION_KEY") or "").strip().strip('"').strip("'")
    if raw:
        return Fernet(raw.encode("utf-8"))
    # Local development without ENCRYPTION_KEY: derive a stable Fernet key from
    # the session secret so entered credentials are still not stored in clear.
    # Servers set both (create_app refuses to start without PEM_SECRET_KEY).
    secret = os.environ.get("PEM_SECRET_KEY") or os.environ.get("FLASK_SECRET_KEY") or "assure-local-dev"
    digest = hashlib.sha256(("assure-aws-integration:" + secret).encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _encrypt(text: str) -> str:
    return _cipher().encrypt(text.encode("utf-8")).decode("utf-8")


def _decrypt(blob: str) -> str:
    return _cipher().decrypt(blob.encode("utf-8")).decode("utf-8")


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #


def _db():
    try:
        from ..db.connection import init_db
        from ..history import get_db
    except ImportError:
        from db.connection import init_db
        from history import get_db
    init_db()
    return get_db()


def load_saved() -> dict[str, Any] | None:
    """The database row, secret decrypted; ``None`` when nothing was saved."""
    db = _db()
    row = db.execute(
        "SELECT value_json, secret_enc, updated_at, updated_by FROM integration_settings WHERE name = ?",
        (INTEGRATION_NAME,),
    ).fetchone()
    if not row:
        return None
    try:
        value = json.loads(row[0] or "{}")
    except (TypeError, ValueError):
        value = {}
    secret = None
    if row[1]:
        try:
            secret = _decrypt(row[1])
        except Exception:
            log.error("aws integration: stored secret cannot be decrypted (ENCRYPTION_KEY changed?)")
            value["secret_unreadable"] = True
    return {
        "access_key_id": value.get("access_key_id") or "",
        "secret_access_key": secret or "",
        "region": value.get("region") or "",
        "bucket": value.get("bucket") or "",
        "prefix": value.get("prefix") or "",
        "secret_unreadable": bool(value.get("secret_unreadable")),
        "updated_at": row[2],
        "updated_by": row[3],
    }


def save(
    *,
    access_key_id: str,
    secret_access_key: str | None,
    region: str,
    bucket: str,
    prefix: str = "assure/",
    updated_by: str | None = None,
) -> dict[str, Any]:
    """Persist the integration. An empty secret keeps the previously stored one
    (the form never echoes the secret back, so re-saving the bucket alone must
    not wipe it) — but only while the access key id it belongs to is still the
    one being saved. Clearing the key id means "use the machine role", and a
    secret kept beside an empty or different key id is a pair that can never
    sign a request; before 2026-09-23 it was kept, so ``apply_to_environment``
    kept exporting the old identity after the operator had removed it."""
    db = _db()
    existing = load_saved() or {}
    key_id = access_key_id.strip()
    secret = (secret_access_key or "").strip()
    if not secret and key_id and key_id == (existing.get("access_key_id") or ""):
        secret = existing.get("secret_access_key") or ""
    value = {
        "access_key_id": key_id,
        "region": region.strip(),
        "bucket": bucket.strip(),
        "prefix": (prefix or "").strip(),
    }
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    db.execute(
        """
        INSERT INTO integration_settings (name, value_json, secret_enc, updated_by, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            value_json = excluded.value_json,
            secret_enc = excluded.secret_enc,
            updated_by = excluded.updated_by,
            updated_at = excluded.updated_at
        """,
        (INTEGRATION_NAME, json.dumps(value), _encrypt(secret) if secret else None, updated_by, now, now),
    )
    db.commit()
    apply_to_environment()
    return status()


def clear() -> None:
    db = _db()
    db.execute("DELETE FROM integration_settings WHERE name = ?", (INTEGRATION_NAME,))
    db.commit()
    _unapply_from_db()


def _unapply_from_db() -> None:
    """Remove every variable ``_set_from_db`` exported, and its marker.

    Driven by the ``_ASSURE_AWS_FROM_DB_*`` markers rather than a fixed key
    list, so a value exported by an earlier version of this module is still
    undone; ``.env`` values carry no marker and are left alone.
    """
    for marker in [k for k in os.environ if k.startswith("_ASSURE_AWS_FROM_DB_")]:
        os.environ.pop(marker[len("_ASSURE_AWS_FROM_DB_"):], None)
        os.environ.pop(marker, None)


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #


def _env_has_keys() -> bool:
    return bool((os.environ.get("AWS_ACCESS_KEY_ID") or "").strip() and (os.environ.get("AWS_SECRET_ACCESS_KEY") or "").strip())


def apply_to_environment() -> str:
    """Export database-saved credentials into the process environment.

    Only fills variables the environment does not already set — ``.env`` wins —
    and marks what it set so it can be undone exactly. Returns the credential
    source that is now effective: ``env`` / ``database`` / ``role``. Called from
    ``web.create_app``, from the Celery worker at boot, and after every save.

    Every variable this function exported on an earlier call is removed first,
    so the environment mirrors the database row as it is *now*. Before
    2026-09-23 the function only ever added: after the operator emptied the
    access key id on the Sources panel, ``save()`` wrote a key-less row, the
    role branch was taken, and ``AWS_ACCESS_KEY_ID`` / ``AWS_SECRET_ACCESS_KEY``
    exported from the previous row stayed in ``os.environ`` — boto3 kept signing
    with credentials the UI said were gone. ``AWS_SESSION_TOKEN`` is never set
    or touched here: saved credentials are long-lived keys, and a session token
    in the environment belongs to the role chain, not to this integration.
    """
    _unapply_from_db()
    if _env_has_keys():
        if not (os.environ.get("ASSURE_S3_BUCKET") or "").strip():
            saved = _safe_load_saved()
            if saved and saved.get("bucket"):
                _set_from_db("ASSURE_S3_BUCKET", saved["bucket"])
                _set_from_db("ASSURE_S3_PREFIX", saved.get("prefix") or "assure/")
        return "env"
    saved = _safe_load_saved()
    if saved and saved.get("access_key_id") and saved.get("secret_access_key"):
        _set_from_db("AWS_ACCESS_KEY_ID", saved["access_key_id"])
        _set_from_db("AWS_SECRET_ACCESS_KEY", saved["secret_access_key"])
        if saved.get("region"):
            _set_from_db("AWS_DEFAULT_REGION", saved["region"])
        if saved.get("bucket"):
            _set_from_db("ASSURE_S3_BUCKET", saved["bucket"])
            _set_from_db("ASSURE_S3_PREFIX", saved.get("prefix") or "assure/")
        return "database"
    if saved and saved.get("bucket") and not (os.environ.get("ASSURE_S3_BUCKET") or "").strip():
        # Bucket entered on the screen, credentials expected from the machine role.
        _set_from_db("ASSURE_S3_BUCKET", saved["bucket"])
        _set_from_db("ASSURE_S3_PREFIX", saved.get("prefix") or "assure/")
        if saved.get("region"):
            _set_from_db("AWS_DEFAULT_REGION", saved["region"])
    return "role"


def _set_from_db(key: str, value: str) -> None:
    if (os.environ.get(key) or "").strip() and not os.environ.get(f"_ASSURE_AWS_FROM_DB_{key}"):
        return  # .env / real environment wins
    os.environ[key] = value
    os.environ[f"_ASSURE_AWS_FROM_DB_{key}"] = "1"


def _safe_load_saved() -> dict[str, Any] | None:
    try:
        return load_saved()
    except Exception:
        # No database yet (first boot before migrations) or unreadable row: the
        # caller falls back to the role; the status endpoint reports it.
        log.debug("aws integration: saved settings unavailable", exc_info=True)
        return None


def credential_source() -> str:
    if _env_has_keys():
        return "database" if os.environ.get("_ASSURE_AWS_FROM_DB_AWS_ACCESS_KEY_ID") else "env"
    return "role"


def status(*, probe: bool = True) -> dict[str, Any]:
    """What the app will do with an upload right now, checked for real.

    ``reachable`` comes from ``s3:HeadBucket`` with the effective identity;
    ``identity`` from ``sts:GetCallerIdentity`` when reachable. Never guesses.
    """
    apply_to_environment()
    bucket = (os.environ.get("ASSURE_S3_BUCKET") or "").strip()
    region = (os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION") or "").strip()
    saved = _safe_load_saved() or {}
    out: dict[str, Any] = {
        "configured": bool(bucket),
        "bucket": bucket,
        "prefix": (os.environ.get("ASSURE_S3_PREFIX") or "assure/").strip(),
        "region": region,
        "credential_source": credential_source(),
        "access_key_id_hint": _hint(os.environ.get("AWS_ACCESS_KEY_ID") or saved.get("access_key_id") or ""),
        "saved_in_database": bool(saved),
        "saved_at": saved.get("updated_at"),
        "secret_unreadable": bool(saved.get("secret_unreadable")),
        "storage": "s3" if bucket else "local",
        "reachable": None,
        "identity": None,
        "error": None,
    }
    if not bucket:
        out["error"] = "No S3 bucket configured: uploads and artifacts stay on this instance's disk."
        return out
    if not probe:
        return out
    try:
        import boto3
        from botocore.config import Config

        cfg = Config(connect_timeout=4, read_timeout=8, retries={"max_attempts": 1})
        s3 = boto3.client("s3", region_name=region or None, config=cfg)
        s3.head_bucket(Bucket=bucket)
        out["reachable"] = True
        try:
            ident = boto3.client("sts", region_name=region or None, config=cfg).get_caller_identity()
            out["identity"] = {"account": ident.get("Account"), "arn": ident.get("Arn")}
        except Exception as exc:  # HeadBucket succeeded; identity is informational
            out["identity"] = {"error": exc.__class__.__name__}
    except Exception as exc:
        out["reachable"] = False
        out["error"] = _short_error(exc)
    return out


def _hint(access_key_id: str) -> str:
    key = (access_key_id or "").strip()
    if len(key) < 8:
        return ""
    return key[:4] + "…" + key[-4:]


def _short_error(exc: Exception) -> str:
    message = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
    lowered = message.lower()
    if "unable to locate credentials" in lowered or "nocredentials" in exc.__class__.__name__.lower():
        return "No AWS credentials: set AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY in .env, enter them here, or run with an IAM role."
    if "403" in message or "forbidden" in lowered or "accessdenied" in message.replace(" ", "").lower():
        return "Access denied to the bucket: the identity lacks s3:ListBucket/GetObject/PutObject on it."
    if "404" in message or "nosuchbucket" in message.replace(" ", "").lower():
        return "Bucket not found in this region."
    if "invalidaccesskeyid" in message.replace(" ", "").lower() or "signaturedoesnotmatch" in message.replace(" ", "").lower():
        return "The access key id or secret is wrong."
    return f"{exc.__class__.__name__}: {message[:200]}"
