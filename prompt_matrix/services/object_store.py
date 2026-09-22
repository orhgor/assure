"""Object storage for the bytes that must not live on one instance's disk.

Two things cross the web/worker boundary as files: an uploaded document
waiting to be parsed, and the OMP artifact JSON mirrored beside its database
row. On a single box those were paths under the data directory; with web
replicas and workers on different hosts a path is meaningless, so both go
through this interface.

``ASSURE_S3_BUCKET`` set → :class:`S3ObjectStore` (boto3, standard credential
chain: task role on ECS, instance profile on EC2, env/profile locally).
Unset → :class:`LocalObjectStore` under ``<data dir>/objects/``, the same
key layout, so single-host deployments and the test-suite need no bucket.

Keys are relative (``uploads/<project>/<uuid>/<name>``,
``omp/<project>/<artifact>.json``); ``ASSURE_S3_PREFIX`` is prepended on S3.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_segment(value: str, fallback: str = "file") -> str:
    cleaned = _SAFE_SEGMENT.sub("_", (value or "").strip()).strip("._")
    return cleaned[:120] or fallback


def upload_key(project_id: str, filename: str) -> str:
    """A fresh staging key for one upload: ``uploads/<project>/<uuid>/<name>``."""
    return f"uploads/{_safe_segment(project_id, 'project')}/{uuid.uuid4().hex}/{_safe_segment(filename)}"


def omp_key(project_id: str, artifact_id: str) -> str:
    return f"omp/{_safe_segment(project_id, 'project')}/{_safe_segment(artifact_id, 'artifact')}.json"


def key_belongs_to_project(key: str, project_id: str) -> bool:
    """Whether an upload key names this project — the guard before a route
    lets a client hand it an object key instead of bytes."""
    return bool(key) and key.startswith(f"uploads/{_safe_segment(project_id, 'project')}/") and ".." not in key


class ObjectStore:
    """The interface; both backends implement exactly this."""

    backend = "abstract"

    def put_bytes(self, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> str:
        raise NotImplementedError

    def get_bytes(self, key: str) -> bytes:
        raise NotImplementedError

    def exists(self, key: str) -> bool:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError

    def uri(self, key: str) -> str:
        raise NotImplementedError

    def presign_put(self, key: str, *, content_type: str, expires_seconds: int = 900) -> dict[str, Any] | None:
        """A browser-direct upload target, or ``None`` when the backend has none."""
        return None


class LocalObjectStore(ObjectStore):
    backend = "local"

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        if ".." in key or key.startswith("/"):
            raise ValueError(f"unsafe object key: {key!r}")
        return self.root / key

    def put_bytes(self, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return self.uri(key)

    def get_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink()
        except FileNotFoundError:
            pass

    def uri(self, key: str) -> str:
        return f"file://{self._path(key)}"


class S3ObjectStore(ObjectStore):
    backend = "s3"

    def __init__(self, bucket: str, prefix: str = "", *, client: Any | None = None, region: str | None = None) -> None:
        self.bucket = bucket
        self.prefix = prefix.strip("/") + "/" if prefix.strip("/") else ""
        self._client = client
        self._region = region or os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION")

    def _c(self) -> Any:
        if self._client is None:
            import boto3

            self._client = boto3.client("s3", region_name=self._region)
        return self._client

    def _k(self, key: str) -> str:
        return self.prefix + key

    def put_bytes(self, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> str:
        self._c().put_object(Bucket=self.bucket, Key=self._k(key), Body=data, ContentType=content_type)
        return self.uri(key)

    def get_bytes(self, key: str) -> bytes:
        return self._c().get_object(Bucket=self.bucket, Key=self._k(key))["Body"].read()

    def exists(self, key: str) -> bool:
        try:
            self._c().head_object(Bucket=self.bucket, Key=self._k(key))
            return True
        except Exception as exc:
            # Only "no such object" is False. Throttling, AccessDenied or DNS
            # used to read as "gone", and the worker then marked the job
            # skipped — a conclusion the error does not support (audit
            # 2026-09-23). Anything else propagates to the caller's retry path.
            code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", "") or "")
            status = getattr(exc, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode")
            if code in ("404", "NoSuchKey", "NotFound") or status == 404:
                return False
            raise

    def delete(self, key: str) -> None:
        try:
            self._c().delete_object(Bucket=self.bucket, Key=self._k(key))
        except Exception as exc:  # best-effort: a lifecycle rule is the backstop
            log.warning("object delete failed for %s: %s", key, exc)

    def uri(self, key: str) -> str:
        return f"s3://{self.bucket}/{self._k(key)}"

    def presign_put(self, key: str, *, content_type: str, expires_seconds: int = 900) -> dict[str, Any] | None:
        url = self._c().generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": self._k(key), "ContentType": content_type},
            ExpiresIn=expires_seconds,
        )
        return {"method": "PUT", "url": url, "headers": {"Content-Type": content_type}}


def _data_dir() -> Path:
    try:
        from ..history import _resolve_db_path
    except ImportError:
        from history import _resolve_db_path
    return _resolve_db_path().parent


def get_object_store() -> ObjectStore:
    """The configured store. Resolved per call: env-driven, cheap, test-friendly."""
    bucket = (os.environ.get("ASSURE_S3_BUCKET") or "").strip()
    if bucket:
        return S3ObjectStore(bucket, os.environ.get("ASSURE_S3_PREFIX") or "")
    return LocalObjectStore(_data_dir() / "objects")
