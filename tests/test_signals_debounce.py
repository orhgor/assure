"""Red-Hat draft debounce: a burst of edits is audited once, on its *last* edit.

Two paths exist — a Redis lock shared by every web replica, and a per-process
timer used when ``REDIS_URL`` is unset. Both must hand the newest payload to
the enqueue; before 2026-09-23 the Redis path armed one timer closed over the
first edit and returned on the held lock for every later one, so the audit ran
on stale content and the final edit of a burst was never audited.
"""

from __future__ import annotations

import time

import pytest


class _FakeRedis:
    """The four commands the debounce uses, on a dict; ``ex`` is ignored (a
    timed test must not depend on the TTL)."""

    def __init__(self):
        self.store: dict[str, str] = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    def getdel(self, key):
        return self.store.pop(key, None)

    def get(self, key):
        return self.store.get(key)

    def delete(self, key):
        self.store.pop(key, None)


def _wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


@pytest.fixture
def fast_debounce(monkeypatch):
    from prompt_matrix import signals

    monkeypatch.setattr(signals, "_DEBOUNCE_S", 0.05)
    enqueued: list[tuple] = []
    monkeypatch.setattr(
        signals,
        "_enqueue_multipass",
        lambda project_id, current_jdf, previous_jdf, run_id: enqueued.append(
            (project_id, current_jdf, previous_jdf, run_id)
        ),
    )
    return enqueued


@pytest.fixture
def fake_redis(monkeypatch):
    from prompt_matrix.services import redis_client

    fake = _FakeRedis()
    monkeypatch.setenv("REDIS_URL", "redis://fake-for-test/0")
    monkeypatch.setattr(redis_client, "get_redis", lambda: fake)
    return fake


def _edit(project_id: str, version: int) -> dict:
    return {"body": [{"type": "paragraph", "content": f"edit {version}"}], "v": version}


def test_redis_debounce_audits_the_last_edit_once(fast_debounce, fake_redis):
    from prompt_matrix import signals

    first, second = _edit("p1", 1), _edit("p1", 2)
    signals._on_draft_changed("test", project_id="p1", current_jdf=first, previous_jdf=None)
    signals._on_draft_changed("test", project_id="p1", current_jdf=second, previous_jdf=first)

    assert _wait_for(lambda: len(fast_debounce) >= 1), "the debounced audit never fired"
    time.sleep(0.15)  # a second timer, if one had been armed, would have fired by now
    assert len(fast_debounce) == 1, fast_debounce
    project_id, current_jdf, previous_jdf, _run_id = fast_debounce[0]
    assert project_id == "p1"
    assert current_jdf == second, "the audit ran on the first edit, not the last"
    assert previous_jdf == first
    # The window is closed and the payload consumed: the next edit starts fresh.
    assert not any(k.startswith("assure:lock:redhat-debounce:") for k in fake_redis.store)
    assert signals._payload_key("p1") not in fake_redis.store


def test_redis_debounce_second_replica_hands_its_payload_to_the_lock_holder(fast_debounce, fake_redis):
    """Only the lock holder arms a timer, but a caller that loses the lock still
    gets its edit audited — by the holder's timer, which reads the shared key."""
    from prompt_matrix import signals

    signals._on_draft_changed("test", project_id="p2", current_jdf=_edit("p2", 1))
    # A second caller inside the window (same Redis, another replica in production).
    assert signals._redis_debounce("p2", {"project_id": "p2", "current_jdf": _edit("p2", 2)}) is True

    assert _wait_for(lambda: len(fast_debounce) >= 1)
    time.sleep(0.15)
    assert [call[1] for call in fast_debounce] == [_edit("p2", 2)]


def test_local_debounce_audits_the_last_edit_once(fast_debounce, monkeypatch):
    """Without REDIS_URL the per-process timer is re-armed with the newest payload
    (this path was already correct; the test keeps it so)."""
    monkeypatch.delenv("REDIS_URL", raising=False)
    from prompt_matrix import signals

    first, second = _edit("p3", 1), _edit("p3", 2)
    signals._on_draft_changed("test", project_id="p3", current_jdf=first)
    signals._on_draft_changed("test", project_id="p3", current_jdf=second)

    assert _wait_for(lambda: len(fast_debounce) >= 1)
    time.sleep(0.15)
    assert [call[1] for call in fast_debounce] == [second]
    assert "p3" not in signals._debounce_timers
