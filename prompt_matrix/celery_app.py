"""Celery application for offloading LLM compile / audit / grounding work."""

from __future__ import annotations

import os

from celery import Celery


def celery_broker_disabled() -> bool:
    raw = os.environ.get("CELERY_BROKER_URL")
    if raw is not None and not str(raw).strip():
        return True
    return os.environ.get("CELERY_DISABLED", "").strip().lower() in ("1", "true", "yes")


_broker = (
    "memory://"
    if celery_broker_disabled()
    else (os.environ.get("CELERY_BROKER_URL") or "sqs://").strip()
)
try:
    from .history import _resolve_db_path
except ImportError:
    from history import _resolve_db_path
_data_dir = _resolve_db_path().parent
_default_backend = f"db+sqlite:///{_data_dir / 'celery-results.sqlite'}"
_result_backend = (os.environ.get("CELERY_RESULT_BACKEND") or _default_backend).strip()

celery_app = Celery(
    "assure",
    broker=_broker,
    backend=_result_backend,
    include=[
        "prompt_matrix.tasks.llm_tasks",
        "prompt_matrix.tasks.substrate_tasks",
        "prompt_matrix.tasks.compile_tasks",
        "prompt_matrix.tasks.redhat",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=int(os.environ.get("CELERY_TASK_TIME_LIMIT", "900")),
    worker_prefetch_multiplier=1,
    broker_transport_options={
        "region": os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        "visibility_timeout": int(os.environ.get("CELERY_SQS_VISIBILITY_TIMEOUT", "3600")),
        "polling_interval": float(os.environ.get("CELERY_SQS_POLLING_INTERVAL", "1")),
        "queue_name_prefix": os.environ.get("CELERY_SQS_QUEUE_PREFIX", "assure-"),
    },
    task_routes={
        "assure.process_substrate_upload": {"queue": "sqlite_writes"},
    },
)

_eager = os.environ.get("CELERY_TASK_ALWAYS_EAGER", "").lower() in ("1", "true", "yes")
if celery_broker_disabled():
    _eager = True
if _eager:
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    celery_app.conf.task_store_eager_result = True
