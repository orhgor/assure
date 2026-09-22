"""Celery application for offloading LLM compile / audit / grounding work."""

from __future__ import annotations

import os

from celery import Celery


def celery_broker_disabled() -> bool:
    raw = os.environ.get("CELERY_BROKER_URL")
    if raw is not None and not str(raw).strip():
        return True
    return os.environ.get("CELERY_DISABLED", "").strip().lower() in ("1", "true", "yes")


def _redis_url() -> str:
    return (os.environ.get("REDIS_URL") or "").strip()


def _default_broker() -> str:
    """Broker precedence: CELERY_BROKER_URL, then REDIS_URL, then SQS.

    Redis is the default because it is also the result backend, the
    rate-limit store and the debounce lock — one shared service locally and
    on AWS (ElastiCache). ``sqs://`` stays available for deployments that
    prefer it; results then go to PostgreSQL (see ``_default_backend``).
    """
    explicit = os.environ.get("CELERY_BROKER_URL")
    if explicit is not None and explicit.strip():
        return explicit.strip()
    if _redis_url():
        return _redis_url()
    return "sqs://"


_broker = "memory://" if celery_broker_disabled() else _default_broker()


def broker_supports_control() -> bool:
    """Remote control (revoke, inspect) needs broadcast: Redis / AMQP, not SQS."""
    return _broker.startswith(("redis://", "rediss://", "amqp://", "pyamqp://", "memory://"))


try:
    from .db.pg_compat import sqlalchemy_url as _pg_sqlalchemy_url
except ImportError:
    from db.pg_compat import sqlalchemy_url as _pg_sqlalchemy_url


def _default_backend() -> str:
    """Results: Redis when it is the broker, else the app's PostgreSQL."""
    if _broker.startswith(("redis://", "rediss://")):
        return _broker
    pg_url = _pg_sqlalchemy_url()
    if pg_url:
        return f"db+{pg_url}"
    # Eager / disabled mode only (no broker, no database): results in memory.
    return "cache+memory://"


_result_backend = (os.environ.get("CELERY_RESULT_BACKEND") or _default_backend()).strip()

celery_app = Celery(
    "assure",
    broker=_broker,
    backend=_result_backend,
    include=[
        "prompt_matrix.tasks.llm_tasks",
        "prompt_matrix.tasks.substrate_tasks",
        "prompt_matrix.tasks.compile_tasks",
        "prompt_matrix.tasks.parse_tasks",
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
    # Late acks + one prefetch: a worker that dies mid-parse hands the message
    # back to the queue instead of losing it. Tasks are written to tolerate a
    # redelivery (idempotent object keys, INSERT ... ON CONFLICT).
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    result_expires=int(os.environ.get("CELERY_RESULT_EXPIRES", "86400")),
    task_routes={
        "assure.process_substrate_upload": {"queue": "parse"},
        "assure.import_project_pdf": {"queue": "parse"},
        "assure.safe_compile_and_verify": {"queue": "default"},
    },
)

_eager = os.environ.get("CELERY_TASK_ALWAYS_EAGER", "").lower() in ("1", "true", "yes")
if celery_broker_disabled():
    _eager = True
if _eager:
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    celery_app.conf.task_store_eager_result = True


# The worker resolves the same AWS identity as the web tier: .env, then the
# credentials saved from the Sources panel, then the machine role. Done per
# worker process so a setting saved after boot is picked up on the next
# process spawn (prefork), and never before the database exists.
try:
    from celery.signals import worker_process_init

    @worker_process_init.connect  # type: ignore[misc]
    def _apply_aws_integration(**_kwargs):
        try:
            from prompt_matrix.services.aws_integration import apply_to_environment

            apply_to_environment()
        except Exception:  # pragma: no cover - observability only
            import logging

            logging.getLogger("assure").exception("aws integration: worker could not apply saved settings")
except ImportError:  # pragma: no cover
    pass
