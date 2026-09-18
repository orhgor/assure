#!/usr/bin/env python3
"""The compile lock, with a lease: a bounded hold that a waiter can recover.

THE CONVENTION THIS REPLACES
----------------------------
``/tmp/assure-compile.lock`` was an ``flock`` held for the length of a whole
measurement phase (install, compile, audit), with a free-text breadcrumb
(``holder=FixA phase=before pid=8001``) written once at acquire. Two things
followed from that:

* The hold had no bound. A holder that was stopped, blocked, or simply long
  kept the lock for as long as it liked, and nothing recorded that it had
  outlived anything.
* The waiter bounded *itself* instead. ``withlock_retry.py`` retried for
  ``RETRY_SECONDS`` and then printed ``NOT RUN`` — so the waiter yielded and
  the holder kept the lock. A real end-to-end compile (``bg_84``,
  ``RETRY_SECONDS=900``) spent its entire window against a legitimate hold and
  never ran; its log was 180 ``LOCK-BUSY`` lines and no results file.

WHAT CHANGED
------------
The outer mutex is unchanged (``flock`` on `<path>`), so every existing waiter
and holder still excludes every other one, including the old copies. Three
things are added:

1. **A lease.** The holder writes a heartbeat every ``lease/3`` seconds
   (default lease 90 s -> heartbeat 30 s) into ``<path>.lease``. Heartbeat age
   comes from ``time.monotonic()`` plus a boot marker, so a laptop that sleeps
   does not make a healthy holder look dead on wake.
2. **A generation.** The holder also flocks ``<path>.gen.<N>``, where N is a
   number carried in the lease file. That file is the mutex a waiter *can* take:
   when the lease is stale, the waiter opens generation N+1, holds it, and
   writes its own lease. The old holder sees the generation move at its next
   heartbeat, logs ``LOCK-LEASE-LOST``, and kills the command it was running.
   (An ``flock`` cannot be stolen; a generation can.)
3. **A waiter that does not out-run the lease.** The waiter's deadline counts
   *no progress*, not wall clock: every heartbeat advances the progress clock.
   A holder doing real work is never starved; a holder that has gone quiet is
   superseded at ``lease`` (90 s), not at the waiter's window. ``NOT RUN`` is
   now what happens to a silent *legacy* holder — one from a copy of the old
   wrapper, whose free-text breadcrumb carries no generation — because such a
   holder must never be double-run.

Recovery is observable, not silent: ``LOCK-TAKEOVER`` / ``LOCK-STALE-RECOVERED``
/ ``LOCK-LEASE-LOST`` on stdout, a monotonic integer in ``<path>.takeovers``,
and a journal line in ``<path>.recoveries``.

USAGE
-----
    compile_lock.py [--lock PATH] [--holder NAME] [--phase PHASE]
                    [--lease S] [--max-wait S] [--poll S] -- <cmd> [args...]

Exit codes: the command's own, or 3 = superseded (the lease was lost and the
command was killed), 4 = NOT RUN (no holder progress and no recoverable lease
within ``--max-wait``). Same lock path, same ``LOCK-ACQUIRED`` /
``LOCK-BUSY (...)`` / ``LOCK-RELEASE`` lines as before.
"""

from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
import sys
import time

DEFAULT_PATH = "/tmp/assure-compile.lock"
DEFAULT_LEASE_S = 90.0
DEFAULT_MAX_WAIT_S = 3600.0
DEFAULT_POLL_S = 5.0
# How long a superseded holder gives its child to die before SIGKILL.
_TERM_GRACE_S = 5.0

EXIT_SUPERSEDED = 3
EXIT_NOT_RUN = 4


# --------------------------------------------------------------------------- #
# state
# --------------------------------------------------------------------------- #

def _boot_marker() -> str:
    """Stable inside one boot, different across boots, comparable across processes.

    ``time.monotonic()`` on Darwin is ``mach_absolute_time``: it does not advance
    while the machine is asleep, and it is the same for every process on the
    box. ``monotonic - time`` therefore identifies the boot without a syscall.
    """
    return "%.3f" % (time.monotonic() - time.time())


BOOT = _boot_marker()


def _sanitize(value: object) -> str:
    return "_".join(str(value).split()) or "?"


def _lease_path(path: str) -> str:
    return path + ".lease"


def _gen_path(path: str, gen: int) -> str:
    return "%s.gen.%d" % (path, gen)


def _read_lease(path: str) -> dict:
    """The canonical state, or {} when absent/unreadable (a legacy holder)."""
    try:
        with open(_lease_path(path)) as fh:
            data = json.loads(fh.read() or "{}")
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _legacy_identity(path: str) -> str:
    """The free-text line an old wrapper wrote into the lock file itself."""
    try:
        with open(path) as fh:
            text = fh.read(300)
    except OSError:
        return ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("holder="):
            return line
    return ""


def _write_lease(path: str, state: dict) -> None:
    """Atomic replace: a reader sees the old line or the new one, never half."""
    tmp = "%s.tmp%d" % (_lease_path(path), os.getpid())
    with open(tmp, "w") as fh:
        json.dump(state, fh, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, _lease_path(path))


def _journal(path: str, line: str) -> None:
    try:
        with open(path + ".recoveries", "a") as fh:
            fh.write("%s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), line))
    except OSError:
        pass


def _bump_takeovers(path: str) -> int:
    """The monotonic counter, in the shape ``audit_drops``/``cache_drops`` use."""
    counter = path + ".takeovers"
    try:
        with open(counter) as fh:
            n = int((fh.read() or "0").strip() or "0")
    except (OSError, ValueError):
        n = 0
    n += 1
    try:
        with open(counter, "w") as fh:
            fh.write("%d\n" % n)
    except OSError:
        pass
    return n


def _age_s(state: dict, now: float) -> float:
    """Seconds since the holder's last heartbeat; inf when it never wrote one."""
    if state.get("boot") != BOOT:
        return float("inf")
    try:
        return max(0.0, now - float(state["hb_mono"]))
    except (KeyError, TypeError, ValueError):
        return float("inf")


def _progress_ts(path: str, state: dict) -> float:
    """A monotonic stamp that advances whenever the holder does anything.

    A canonical holder advances it with every heartbeat. A legacy holder cannot
    heartbeat, so the breadcrumb file's mtime is the only evidence of life it
    leaves; the waiter reads progress from that instead.
    """
    if state.get("boot") == BOOT:
        try:
            return float(state["hb_mono"])
        except (KeyError, TypeError, ValueError):
            pass
    for candidate in (path, _lease_path(path)):
        try:
            return os.stat(candidate).st_mtime
        except OSError:
            continue
    return 0.0


# --------------------------------------------------------------------------- #
# flock helpers
# --------------------------------------------------------------------------- #

class Lock:
    """An flock held on one file, closed only on release."""

    def __init__(self, path: str, fd: int):
        self.path = path
        self.fd = fd

    @classmethod
    def try_acquire(cls, path: str) -> "Lock | None":
        try:
            fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError:
            return None
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return None
        return cls(path, fd)

    def release(self) -> None:
        try:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(self.fd)
        except OSError:
            pass


def _file_held_by_other(path: str) -> bool:
    """Is ``path`` flocked by a live process other than us?"""
    probe = Lock.try_acquire(path)
    if probe is None:
        # Either it is held, or it cannot be opened at all; both mean "do not
        # assume free" for a legacy holder we cannot bound.
        return True
    probe.release()
    return False


def _take_path(path: str) -> "Lock | None":
    """The old, outer mutex: held when free, so old waiters still see us busy."""
    return Lock.try_acquire(path)


def _write_breadcrumb(path: str, holder: str, phase: str) -> None:
    """The line old copies of the wrapper read and print. Overwritten in place."""
    line = "holder=%s phase=%s pid=%d\n" % (holder, phase, os.getpid())
    try:
        with open(path, "r+") as fh:
            fh.seek(0)
            fh.write(line)
            fh.truncate()
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# acquisition
# --------------------------------------------------------------------------- #

class Acquired:
    def __init__(self, gen: int, gen_lock: Lock, path_lock: "Lock | None", how: str):
        self.gen = gen
        self.gen_lock = gen_lock
        self.path_lock = path_lock
        self.how = how


def _recover(path: str, how: str, state: dict) -> None:
    """Log and count a lock recovered without its holder releasing it."""
    line = "LOCK-STALE-RECOVERED reason=%s stale=%s" % (how, _busy_line(path, state))
    print(line, flush=True)
    _journal(path, line)
    print("LOCK-TAKEOVERS n=%d" % _bump_takeovers(path), flush=True)


def _try_acquire(path: str, holder: str, phase: str, lease: float, now: float):
    """One attempt. Returns ``(Acquired, None)`` or ``(None, busy_reason)``.

    The decision runs under a gate flock on ``<path>.gate`` so that two waiters
    cannot both decide to take over the same stale generation.
    """
    gate = Lock.try_acquire(path + ".gate")
    if gate is None:
        return None, "gate"
    try:
        state = _read_lease(path)

        if state:
            gen = int(state.get("gen", 0) or 0)
            gen_lock = _take_generation(path, gen)
            if gen_lock is not None:
                # Nobody holds this generation: the holder released cleanly or
                # died. Recover it in place — the number only has to move when a
                # live holder must be invalidated.
                if state.get("holding"):
                    _recover(path, "holder-crash", state)
                return Acquired(gen, gen_lock, _take_path(path), "recovered"), None
            # The generation is held. A fresh heartbeat is a live holder doing
            # work; a stale one has outlived its lease.
            if _age_s(state, now) <= lease:
                return None, "lease-fresh"
            new_gen = gen + 1
            gen_lock = _take_generation(path, new_gen)
            if gen_lock is None:
                return None, "race"
            line = (
                "LOCK-TAKEOVER reason=lease-expired stale_holder=%s stale_phase=%s "
                "stale_pid=%s stale_age=%.0fs gen=%d->%d"
                % (state.get("holder", "?"), state.get("phase", "?"),
                   state.get("pid", "?"), _age_s(state, now), gen, new_gen)
            )
            print(line, flush=True)
            _journal(path, line)
            print("LOCK-TAKEOVERS n=%d" % _bump_takeovers(path), flush=True)
            return Acquired(new_gen, gen_lock, _take_path(path), "lease-expired"), None

        # A legacy holder: free text in the breadcrumb, no generation. Its hold
        # cannot be bounded and must not be broken — a compile that did not hold
        # the lock is void, so taking this one over would run two at once.
        if _file_held_by_other(path):
            return None, "legacy"
        gen_lock = _take_generation(path, 1)
        if gen_lock is None:
            return None, "race"
        if _legacy_identity(path):
            _recover(path, "legacy-holder-gone", {})
        return Acquired(1, gen_lock, _take_path(path), "recovered"), None
    finally:
        gate.release()


def _take_generation(path: str, gen: int) -> "Lock | None":
    return Lock.try_acquire(_gen_path(path, gen))


# --------------------------------------------------------------------------- #
# holding
# --------------------------------------------------------------------------- #

def _terminate(child: "subprocess.Popen") -> None:
    if child.poll() is not None:
        return
    try:
        child.send_signal(signal.SIGTERM)
        child.wait(timeout=_TERM_GRACE_S)
    except (subprocess.TimeoutExpired, OSError):
        try:
            child.kill()
        except OSError:
            pass


def _run_under_lease(cmd, path, holder, phase, lease, acquired: Acquired) -> int:
    """Run the command with a heartbeat; lose the lease instead of keeping it."""
    state = {
        "gen": acquired.gen,
        "holder": holder,
        "phase": phase,
        "pid": os.getpid(),
        "boot": BOOT,
        "lease": lease,
        "acquired": time.time(),
        "hb_mono": time.monotonic(),
        "hb_wall": time.time(),
        "holding": True,
    }
    _write_lease(path, state)
    _write_breadcrumb(path, holder, phase)

    child = subprocess.Popen(cmd)
    heartbeat = max(1.0, lease / 3.0)
    superseded = False
    while child.poll() is None:
        slept = 0.0
        while slept < heartbeat and child.poll() is None:
            time.sleep(min(0.25, heartbeat - slept))
            slept += 0.25
        if child.poll() is not None:
            break
        gate = Lock.try_acquire(path + ".gate")
        if gate is None:
            continue
        try:
            current = _read_lease(path)
            if int(current.get("gen", acquired.gen) or acquired.gen) != acquired.gen:
                line = (
                    "LOCK-LEASE-LOST gen=%d superseded_by_gen=%s holder=%s pid=%s"
                    % (acquired.gen, current.get("gen", "?"),
                       current.get("holder", "?"), current.get("pid", "?"))
                )
                print(line, flush=True)
                _journal(path, line)
                superseded = True
                break
            state["hb_mono"] = time.monotonic()
            state["hb_wall"] = time.time()
            _write_lease(path, state)
        finally:
            gate.release()
        if acquired.path_lock is None:
            # A takeover can start while the stale holder still owns the outer
            # flock; take it the moment that holder lets go.
            late = _take_path(path)
            if late is not None:
                acquired.path_lock = late
                print("LOCK-MUTEX-ATTACHED gen=%d" % acquired.gen, flush=True)
                _write_breadcrumb(path, holder, phase)

    if superseded:
        # The lease is gone: the command does not get to finish. It is killed
        # rather than reported as a result, because its run was a double-run.
        _terminate(child)
        rc = EXIT_SUPERSEDED
    else:
        rc = child.wait()

    gate = Lock.try_acquire(path + ".gate")
    try:
        state["holding"] = False
        state["released"] = time.time()
        current = _read_lease(path)
        if int(current.get("gen", acquired.gen) or acquired.gen) == acquired.gen:
            _write_lease(path, state)
    finally:
        gate.release()
    acquired.gen_lock.release()
    if acquired.path_lock is not None:
        acquired.path_lock.release()
    print("LOCK-RELEASE rc=%s" % rc, flush=True)
    return rc


# --------------------------------------------------------------------------- #
# waiting
# --------------------------------------------------------------------------- #

def _busy_line(path: str, state: "dict | None" = None) -> str:
    """The holder's identity, in the shape old waiters printed."""
    state = _read_lease(path) if state is None else state
    if state:
        holder = state.get("holder", "?")
        phase = state.get("phase", "?")
        pid = state.get("pid", "?")
    else:
        parts = dict(
            piece.split("=", 1) for piece in _legacy_identity(path).split() if "=" in piece
        )
        holder, phase, pid = parts.get("holder", "?"), parts.get("phase", "?"), parts.get("pid", "?")
    return "holder=%s phase=%s pid=%s" % (holder, phase, pid)


def acquire(path, holder, phase, lease, max_wait, poll):
    """Wait for the lock, bounded by *no progress* rather than by wall clock.

    ``max_wait == 0`` is the old no-retry wrapper: one attempt, then NOT RUN.
    ``max_wait < 0`` never gives up.
    """
    poll = max(0.05, poll)
    heartbeat = max(1.0, lease / 3.0)
    # The waiter's bound must not expire before the lease does: giving up sooner
    # than `lease` would hand the queue back to the holder, which is the whole
    # defect this lease exists to fix. A window shorter than one heartbeat also
    # cannot tell work from a wedge, so it is raised to a full lease plus two
    # heartbeats — logged, not silent.
    if max_wait > 0 and max_wait < lease + 2 * heartbeat:
        effective = lease + 2 * heartbeat
        print(
            "LOCK-MAX-WAIT-FLOORED requested=%.0fs effective=%.0fs lease=%.0fs heartbeat=%.0fs"
            % (max_wait, effective, lease, heartbeat), flush=True)
        max_wait = effective
    progress = time.monotonic()
    seen = _progress_ts(path, _read_lease(path))
    announced_legacy = False
    first = True
    while True:
        got, reason = _try_acquire(path, holder, phase, lease, time.monotonic())
        if got is not None:
            return got
        if max_wait == 0 and first:
            print("LOCK-BUSY (%s); not retrying (single attempt)" % _busy_line(path), flush=True)
            return None
        first = False
        if reason == "legacy" and not announced_legacy:
            announced_legacy = True
            print(
                "LOCK-LEGACY-HOLDER (%s) no generation, no lease: waiting, never taking over"
                % _busy_line(path), flush=True)
        latest = _progress_ts(path, _read_lease(path))
        if latest > seen:
            seen = latest
            progress = time.monotonic()
        if max_wait > 0 and time.monotonic() - progress >= max_wait:
            print(
                "NOT RUN: no holder progress for %.0fs and no lease to recover (holder: %s)"
                % (max_wait, _busy_line(path)), flush=True)
            return None
        print("LOCK-BUSY (%s); retrying in %.0fs" % (_busy_line(path), poll), flush=True)
        time.sleep(poll)


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #

_OPTIONS = {
    "--lock": "lock",
    "--holder": "holder",
    "--phase": "phase",
    "--lease": "lease",
    "--max-wait": "max_wait",
    "--poll": "poll",
}


def _parse(argv):
    opts = {
        "lock": os.environ.get("ASSURE_COMPILE_LOCK", DEFAULT_PATH),
        "holder": os.environ.get("ASSURE_COMPILE_HOLDER", ""),
        "phase": os.environ.get("PROBE_LABEL", "?"),
        "lease": float(os.environ.get("ASSURE_COMPILE_LEASE_S", DEFAULT_LEASE_S)),
        "max_wait": float(os.environ.get("ASSURE_COMPILE_MAX_WAIT_S", DEFAULT_MAX_WAIT_S)),
        "poll": float(os.environ.get("ASSURE_COMPILE_POLL_S", DEFAULT_POLL_S)),
    }
    rest = list(argv)
    cmd = []
    while rest:
        token = rest.pop(0)
        if token == "--":
            cmd = rest
            break
        if token in _OPTIONS:
            if not rest:
                raise SystemExit("compile_lock: %s needs a value" % token)
            key = _OPTIONS[token]
            value = rest.pop(0)
            opts[key] = float(value) if key in ("lease", "max_wait", "poll") else value
            continue
        cmd = [token] + rest
        break
    if not opts["holder"]:
        opts["holder"] = os.environ.get("USER") or "?"
    return opts, cmd


def main(argv=None) -> int:
    opts, cmd = _parse(sys.argv[1:] if argv is None else argv)
    if not cmd:
        raise SystemExit("compile_lock: nothing to run (usage: compile_lock.py [opts] -- <cmd>)")
    path = opts["lock"]
    holder = _sanitize(opts["holder"])
    phase = _sanitize(opts["phase"])
    got = acquire(path, holder, phase, opts["lease"], opts["max_wait"], opts["poll"])
    if got is None:
        return EXIT_NOT_RUN
    print("LOCK-ACQUIRED %s gen=%d" % (path, got.gen), flush=True)
    return _run_under_lease(cmd, path, holder, phase, opts["lease"], got)


if __name__ == "__main__":
    sys.exit(main())
