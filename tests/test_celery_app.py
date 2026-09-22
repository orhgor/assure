"""Celery worker boot: the saved AWS identity reaches every process that signs
a request, and prefork children are recycled so a later save reaches them too.

Before 2026-09-23 ``apply_to_environment`` ran only in ``worker_process_init``:
the parent process — where the SQS transport creates its boto3 client — never
called it, and without ``worker_max_tasks_per_child`` a child lived forever on
the credentials it saw at its own birth.
"""

from __future__ import annotations


def test_celeryd_init_applies_saved_aws_credentials(monkeypatch):
    from celery.signals import celeryd_init

    from prompt_matrix import celery_app as mod

    calls: list[str] = []
    monkeypatch.setattr(
        "prompt_matrix.services.aws_integration.apply_to_environment",
        lambda: calls.append("applied") or "role",
    )
    # The handler is registered on the parent-process signal ...
    assert celeryd_init.has_listeners()
    # ... and, when Celery fires it, resolves the identity.
    celeryd_init.send(sender="w1@test", instance=None, conf=mod.celery_app.conf, options={})
    assert calls == ["applied"]


def test_every_process_kind_has_the_handler():
    from celery.signals import beat_init, celeryd_init, worker_process_init

    from prompt_matrix import celery_app as mod

    for sig in (celeryd_init, beat_init, worker_process_init):
        receivers = [ref() if callable(ref) and not hasattr(ref, "__code__") else ref for _, ref in sig.receivers]
        assert mod._apply_aws_integration in receivers, sig.name


def test_handler_survives_a_missing_database(monkeypatch):
    """A worker booted before migrations must log, not crash."""
    from prompt_matrix import celery_app as mod

    def boom():
        raise RuntimeError("relation integration_settings does not exist")

    monkeypatch.setattr("prompt_matrix.services.aws_integration.apply_to_environment", boom)
    mod._apply_aws_integration(sender="w1@test")


def test_prefork_children_are_recycled():
    from prompt_matrix.celery_app import celery_app

    assert celery_app.conf.worker_max_tasks_per_child == 200
