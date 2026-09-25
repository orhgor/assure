"""Celery application for offloading LLM compile / audit / grounding work."""

from __future__ import annotations

import os

from celery import Celery, Task


class ScopedTask(Task):
    """Every task body runs inside one ``history.db_scope()``.

    A worker has no Flask request, so outside a scope every ``get_db()`` in the
    repositories checks a NEW pooled connection out and nothing returns it (the
    defect ``history.db_scope`` documents). Measured 2026-09-25 on the compose
    worker after the Parsure hook joined the ingest pipeline: one
    ``import_project_pdf`` task made ~25 ``get_db()`` calls (stage writes,
    revision, OMP, report, five audit events), exceeded pool_size 5 + overflow
    15 and died with ``PoolTimeout: couldn't get a connection after 5.00 sec``
    at the ``persisting`` stage. With the scope the task holds one connection,
    nested calls join it, and it is released when the task returns or raises.
    Threads a task spawns (entailment prefetch) have their own thread-local and
    are unaffected.
    """

    def __call__(self, *args, **kwargs):
        try:
            from prompt_matrix.history import db_scope
        except ImportError:  # flat layout (prompt_matrix/ on sys.path)
            from history import db_scope
        with db_scope():
            return super().__call__(*args, **kwargs)


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
    task_cls=ScopedTask,
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
    # Recycle prefork children: each new child runs ``worker_process_init`` and
    # so re-reads the AWS credentials saved from the Sources panel. Without a
    # limit a child lives as long as the worker and a setting saved after boot
    # never reaches it (audit 2026-09-23). 200 tasks is hours of parse work at
    # the measured ~1 s/text-layer document, so the fork cost is negligible.
    worker_max_tasks_per_child=int(os.environ.get("CELERY_WORKER_MAX_TASKS_PER_CHILD", "200")),
    broker_transport_options={
        "region": os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        # ≥ task_time_limit (900 s) + margin: a crashed worker's document used to
        # wait up to 1 h for redelivery (audit 2026-09-24).
        "visibility_timeout": int(os.environ.get("CELERY_SQS_VISIBILITY_TIMEOUT", "1000")),
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
# credentials saved from the Sources panel, then the machine role.
#
# Three hooks, because the identity is read at three different moments:
#
# ``celeryd_init``       the worker's *parent* process, after the config is
#                        built and before the consumer opens the broker
#                        connection. This is the process the SQS transport's
#                        boto3 client is created in, so it is the only place
#                        that can give that client the saved keys. Before
#                        2026-09-23 only ``worker_process_init`` was connected
#                        and the parent never applied them.
# ``beat_init``          same, for a scheduler process.
# ``worker_process_init`` every prefork child, so a setting saved after boot is
#                        picked up on the next spawn (``worker_max_tasks_per_child``
#                        above makes that spawn happen).
#
# Nothing runs on plain import: the web process calls ``apply_to_environment``
# itself from ``create_app`` after migrations, and a worker booted before the
# database exists gets the ``except`` below, not a crash.
def _apply_aws_integration(**_kwargs):
    try:
        try:
            from .services.aws_integration import apply_to_environment
        except ImportError:
            from services.aws_integration import apply_to_environment

        apply_to_environment()
    except Exception:  # pragma: no cover - observability only
        import logging

        logging.getLogger("assure").exception("aws integration: worker could not apply saved settings")


try:
    from celery.signals import beat_init, celeryd_init, worker_process_init

    celeryd_init.connect(_apply_aws_integration, weak=False)
    beat_init.connect(_apply_aws_integration, weak=False)
    worker_process_init.connect(_apply_aws_integration, weak=False)
except ImportError:  # pragma: no cover
    pass
