"""The compile lock's lease: a bounded hold, a waiter that outlasts it, recovery.

Black box, through the CLI, because that is how the lock is used: a wrapper
process around a whole compile phase. Every test runs on its own lock path
(``--lock``) so nothing here touches the real ``/tmp/assure-compile.lock``.

DETECTOR, NOT GUARD
-------------------
``test_waiter_outlasts_a_legitimate_long_hold`` and
``test_holder_that_outlives_its_lease_is_superseded`` are detectors: they fail
against the pre-fix wrapper (``withlock_retry.py``, which retried for
``RETRY_SECONDS`` and then printed ``NOT RUN``). Point this file at any copy of
it whose lock path is rebound through the env, and the starvation is visible:

    ASSURE_COMPILE_LOCK_TOOL=<path to the pre-fix wrapper> ASSURE_COMPILE_LOCK_LEGACY=1 \
    pytest tests/test_compile_lock_lease.py -k "long_hold or superseded"

The rebound copy is the pre-fix file with one mechanical change — its hardcoded
lock path reads ``ASSURE_COMPILE_LOCK`` — so the borrowed code can run on a test
path. The algorithm (retry to a deadline, then ``NOT RUN``) is untouched; only
the constant moved.

``test_crashed_holder_is_recovered_and_logged`` passes either way on the
recovery itself (the OS drops an flock when the holder dies) — it is a guard for
that, and a detector for the *observability* (log line + counter), which the
pre-fix wrapper has none of. ``test_legacy_holder_is_never_taken_over`` and
``test_busy_line_keeps_its_shape`` are guards: behaviour that must not regress.
"""

from __future__ import annotations

import fcntl
import json
import os
import pathlib
import re
import signal
import subprocess
import sys
import time

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
TOOL = os.environ.get("ASSURE_COMPILE_LOCK_TOOL") or str(REPO / "scripts" / "compile_lock.py")
LEGACY = os.environ.get("ASSURE_COMPILE_LOCK_LEGACY") == "1"

_MARK = "import sys; open(sys.argv[1], 'w').write('ran')"
_SLEEP_AND_MARK = "import sys, time; time.sleep(float(sys.argv[1])); open(sys.argv[2], 'w').write('ran')"

pytestmark = pytest.mark.skipif(
    not os.path.exists(TOOL), reason="no compile-lock tool at %s" % TOOL)


@pytest.fixture
def lock_path(tmp_path):
    """A lock path nothing else uses: the shared lock is never touched, even in
    legacy mode, where the wrapper's hardcoded path is rebound through the env."""
    yield str(tmp_path / "compile.lock")


def _spawn(lock, holder, phase, lease, max_wait, cmd, poll=0.3):
    """Start the tool around ``cmd``; returns the Popen."""
    if LEGACY:
        env = dict(os.environ, ASSURE_COMPILE_LOCK=lock,
                   RETRY_SECONDS=str(max_wait), PROBE_LABEL=phase)
        argv = [sys.executable, TOOL, *cmd]
    else:
        env = dict(os.environ, ASSURE_COMPILE_LOCK=lock)
        argv = [
            sys.executable, TOOL,
            "--lock", lock, "--holder", holder, "--phase", phase,
            "--lease", str(lease), "--max-wait", str(max_wait), "--poll", str(poll),
            "--", *cmd,
        ]
    return subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def _marker(path) -> str:
    return [sys.executable, "-c", _MARK, str(path)]


def _hold(marker, seconds) -> str:
    return [sys.executable, "-c", _SLEEP_AND_MARK, str(seconds), str(marker)]


def _wait_for_lease(lock, timeout=20.0) -> None:
    """The holder is holding: a lease state, or (legacy) its breadcrumb."""
    evidence = lock if LEGACY else lock + ".lease"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if os.path.getsize(evidence) > 0:
                return
        except OSError:
            pass
        time.sleep(0.05)
    raise AssertionError("holder never wrote %s" % evidence)


def _takeovers(lock) -> int:
    try:
        with open(lock + ".takeovers") as fh:
            return int((fh.read() or "0").strip() or "0")
    except (OSError, ValueError):
        return 0


def _kill(proc) -> None:
    if proc is not None and proc.poll() is None:
        try:
            proc.kill()
        except OSError:
            pass
        proc.wait(timeout=10)


# --------------------------------------------------------------------------- #
# detector 1: the bg_84 starvation
# --------------------------------------------------------------------------- #

def test_waiter_outlasts_a_legitimate_long_hold(tmp_path, lock_path):
    """A 12 s legitimate hold must not make a 3 s waiter window return NOT RUN.

    12 s is longer than the pre-fix wrapper's whole retry window plus its 5 s
    poll (3 + 5 = 8), which is the bg_84 shape: the holder is still working when
    the waiter's window runs out.
    """
    held, after = tmp_path / "held.marker", tmp_path / "after.marker"
    holder = _spawn(lock_path, "holder", "before", 2.0, 60.0, _hold(held, 12.0))
    waiter = None
    try:
        time.sleep(1.0)  # mid-hold, heartbeating
        waiter = _spawn(lock_path, "waiter", "after", 2.0, 3.0, _marker(after), poll=0.3)
        holder_rc = holder.wait(timeout=60)
        waiter_out, _ = waiter.communicate(timeout=90)
    finally:
        _kill(holder)
        _kill(waiter)
    assert holder_rc == 0
    assert held.exists(), "the holder's own command never ran"
    assert waiter.returncode == 0, (
        "the waiter yielded to a legitimate long hold instead of waiting it out "
        "(rc=%s):\n%s" % (waiter.returncode, waiter_out))
    assert after.exists(), "the waiter acquired but never ran its command"
    assert after.stat().st_mtime >= held.stat().st_mtime, "the waiter ran before the holder finished"


# --------------------------------------------------------------------------- #
# detector 2: a holder that outlives its lease
# --------------------------------------------------------------------------- #

def test_holder_that_outlives_its_lease_is_superseded(tmp_path, lock_path):
    """A holder that stops heartbeating is superseded, logged, and stood down."""
    held, after = tmp_path / "held.marker", tmp_path / "after.marker"
    holder = _spawn(lock_path, "hungsmith", "stuck", 1.0, 30.0, _hold(held, 60.0))
    waiter = None
    try:
        _wait_for_lease(lock_path)
        time.sleep(0.5)
        os.kill(holder.pid, signal.SIGSTOP)  # alive, holds the flock, heartbeats nothing
        waiter = _spawn(lock_path, "waiter", "next", 1.0, 60.0, _marker(after), poll=0.3)
        waiter_out, _ = waiter.communicate(timeout=90)
        assert waiter.returncode == 0, "the waiter did not recover the expired lease:\n%s" % waiter_out
        assert "LOCK-TAKEOVER reason=lease-expired" in waiter_out, waiter_out
        assert after.exists(), "the waiter took the lease but never ran its command"
        assert _takeovers(lock_path) == 1, "the takeover was not counted"
        with open(lock_path + ".recoveries") as fh:
            assert "LOCK-TAKEOVER reason=lease-expired" in fh.read()

        os.kill(holder.pid, signal.SIGCONT)
        holder_out, _ = holder.communicate(timeout=30)
        assert holder.returncode == 3, "the superseded holder did not stand down:\n%s" % holder_out
        assert "LOCK-LEASE-LOST" in holder_out, holder_out
    finally:
        _kill(waiter)
        try:
            os.kill(holder.pid, signal.SIGCONT)
        except OSError:
            pass
        _kill(holder)


# --------------------------------------------------------------------------- #
# crash recovery (guard on recovery, detector on observability)
# --------------------------------------------------------------------------- #

def test_crashed_holder_is_recovered_and_logged(tmp_path, lock_path):
    after = tmp_path / "after.marker"
    holder = _spawn(lock_path, "crasher", "boom", 2.0, 30.0, _hold(tmp_path / "held.marker", 60.0))
    waiter = None
    try:
        _wait_for_lease(lock_path)
        time.sleep(0.3)
        os.kill(holder.pid, signal.SIGKILL)
        holder.wait(timeout=10)
        waiter = _spawn(lock_path, "waiter", "next", 2.0, 30.0, _marker(after), poll=0.3)
        waiter_out, _ = waiter.communicate(timeout=60)
    finally:
        _kill(waiter)
        _kill(holder)
    assert waiter.returncode == 0, waiter_out
    assert after.exists()
    assert "LOCK-STALE-RECOVERED reason=holder-crash" in waiter_out, waiter_out
    assert _takeovers(lock_path) == 1, "the recovery was not counted"


# --------------------------------------------------------------------------- #
# guards
# --------------------------------------------------------------------------- #

def test_legacy_holder_is_never_taken_over(tmp_path, lock_path):
    """An old wrapper's hold has no lease: it must be waited for, not broken."""
    if LEGACY:
        pytest.skip("the legacy wrapper has no notion of a legacy holder")
    after = tmp_path / "after.marker"
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with open(lock_path, "w") as fh:
        fh.write("holder=OldWrapper phase=deep pid=4242\n")
    try:
        waiter = _spawn(lock_path, "waiter", "next", 0.5, 1.0, _marker(after), poll=0.3)
        out, _ = waiter.communicate(timeout=60)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    assert waiter.returncode == 4, "a compile that did not hold the lock must not run:\n%s" % out
    assert "LOCK-LEGACY-HOLDER" in out, out
    assert "NOT RUN" in out, out
    assert not after.exists()


def _alive(pid: int) -> bool:
    return subprocess.run(["ps", "-p", str(pid), "-o", "pid="],
                          capture_output=True, text=True).stdout.strip() != ""


def test_interrupted_holder_takes_its_command_with_it(tmp_path, lock_path):
    """SIGTERM to the holder must not leave the wrapped compile running.

    A command that outlives its holder is the exact thing the lock is for: the
    next holder takes the lease at expiry while that command is still writing.
    The lease must also not still claim a hold, or every later taker reads this
    as a crash. Detector: the pre-lease wrapper leaks the child and exits without
    releasing anything.
    """
    if LEGACY:
        pytest.skip("the pre-fix wrapper has no lease to release")
    pid_file = tmp_path / "child.pid"
    child_src = "import os, sys, time; open(sys.argv[1], 'w').write(str(os.getpid())); time.sleep(120)"
    holder = _spawn(lock_path, "interrupted", "kill-me", 5.0, 60.0,
                    [sys.executable, "-c", child_src, str(pid_file)])
    child = None
    try:
        _wait_for_lease(lock_path)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not pid_file.exists():
            time.sleep(0.05)
        child = int(pid_file.read_text())
        assert _alive(child), "the wrapped command never started"
        os.kill(holder.pid, signal.SIGTERM)
        holder.wait(timeout=15)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and _alive(child):
            time.sleep(0.1)
        assert not _alive(child), (
            "the command outlived its holder — it runs the compile the lock "
            "serializes, concurrently with the next holder")
        assert holder.returncode == 143, (
            "128 + SIGTERM, the conventional code; got %s" % holder.returncode)
        lease = json.loads(pathlib.Path(lock_path + ".lease").read_text())
        assert lease["holding"] is False, (
            "the lease still claims a hold after the holder was interrupted")
        assert lease.get("aborted") == "SIGTERM", lease
    finally:
        if child is not None and _alive(child):
            os.kill(child, signal.SIGKILL)
        _kill(holder)


def test_busy_line_keeps_its_shape(tmp_path, lock_path):
    """The line the existing greps match on is unchanged."""
    holder = _spawn(lock_path, "busybee", "before", 2.0, 30.0, _hold(tmp_path / "held.marker", 3.0))
    waiter = None
    try:
        time.sleep(1.0)
        waiter = _spawn(lock_path, "waiter", "next", 2.0, 30.0, _marker(tmp_path / "after.marker"), poll=0.3)
        waiter_out, _ = waiter.communicate(timeout=60)
    finally:
        _kill(waiter)
        _kill(holder)
    assert re.search(r"LOCK-BUSY \(holder=\S+ phase=\S+ pid=\d+\); retrying in \d+s", waiter_out), waiter_out
    assert waiter.returncode == 0, waiter_out
