"""Wedge probe: does run_draft_pipeline hold the SQLite pool after the entailment stage?

The reproducibility recipe for the fourth connection-leakage instance, and the reason
the classification in docs/evidence/connection-leakage-notes.md rests on a measurement
rather than on the two C-level `sample` captures (which cannot name their lock).

Finds it standing in the repository root: `WEDGE_ITERS=3 python
scripts/probe_pipeline_pool.py`. No keys, no network, no compile lock — the model call
and the entailment checker are stubbed, and the leak needs only a pipeline *start*.

On the pre-fix base it reads (three standalone runs):

    before loop: pool_holders=5   open_fds=28
    iter 0: 0.0s  pool_holders=20  open_fds=59
    iter 1: 75.1s pool_holders=20  open_fds=89
    iter 2: 75.2s pool_holders=20  open_fds=119

119 open descriptors, 0% CPU, alive, no output — instance (b)'s signature, produced
deterministically. With the fix the same run reads db_open_connections=0 with open_fds
flat at 20-21 and every iteration at 0.0s.

  db_open_connection_count()  connections taken and not given back (the family's
                              resource), on /api/health as db_open_connections
  len(_pool_holders)          pooled checkouts currently out
  fd count                    descriptors open in this process (instance (b)'s shape)

Usage: WEDGE_ITERS=15 python probe.py
Exit:  0 = every iteration completed, 3 = wedged (stacks dumped to WEDGE_OUT)
"""

from __future__ import annotations

import faulthandler
import os
import pathlib
import sys
import tempfile
import threading
import time
import traceback

WORKTREE = os.environ.get("WEDGE_WORKTREE", str(pathlib.Path(__file__).resolve().parents[1]))
ITERS = int(os.environ.get("WEDGE_ITERS", "15"))
STALL_S = float(os.environ.get("WEDGE_STALL", "25"))
OUT = os.environ.get("WEDGE_OUT", "/tmp/wedge-probe/stall.txt")

DB_DIR = tempfile.mkdtemp(prefix="wedge-")
DB = os.path.join(DB_DIR, "history.sqlite")
os.environ["DATABASE_PATH"] = DB
os.environ["SQLITE_USE_POOL"] = "1"
os.environ["ASSURE_LOG_DIR"] = os.path.join(DB_DIR, "logs")
os.environ["ASSURE_FROZEN_PROJECTS"] = ""
os.environ["PEM_ENABLE_HISTORY"] = "0"
sys.path.insert(0, WORKTREE)

from prompt_matrix.db.connection import init_db  # noqa: E402
from prompt_matrix.db.jdf_repository import ensure_project  # noqa: E402
from prompt_matrix.db.substrate_repository import save_substrate_entry  # noqa: E402
from prompt_matrix.routers import draft as draft_mod  # noqa: E402

SOURCE = """ACME PROPERTY UNDERWRITING GUIDELINE 2026

Coverage limits. The maximum general liability per occurrence is 2,000,000 dollars. The annual
aggregate is 4,000,000 dollars. Coverage for flood is excluded in Zone A.

Wind and hail. For coastal and high-hazard locations in Suffolk County, the wind and hail
deductible is 2 percent of insured value at each location. Inland locations carry a 1,000 dollar
flat deductible.

Deductibles under the current policy. The all-other-perils deductible is 25,000 dollars. The
wind deductible is 2 percent of insured value. The policy period runs from 1 January 2026 to
31 December 2026.

Renewal offer. The renewal raises the all-other-perils deductible to 50,000 dollars and keeps the
wind deductible at 2 percent of insured value. The renewal term is 1 January 2027 to
31 December 2027. The coverage limit is unchanged at 2,000,000 dollars.

Declarations. The named insured is Boston Metro Real Estate LLC. The policy number is
BM-2026-0142. The renewal offer number is BM-2027-0143.
"""

DRAFT = """## Coverage limits

The maximum general liability per occurrence is 2,000,000 dollars and the annual aggregate is
4,000,000 dollars. Flood is excluded in Zone A.

## Wind and hail

For coastal and high-hazard locations in Suffolk County the wind and hail deductible is 2 percent
of insured value at each location. Inland locations carry a 1,000 dollar flat deductible.

## Current deductibles

The all-other-perils deductible is 25,000 dollars and the wind deductible is 2 percent of insured
value for the period 1 January 2026 to 31 December 2026.

## Renewal offer

The renewal raises the all-other-perils deductible to 50,000 dollars and keeps the wind deductible
at 2 percent of insured value for 1 January 2027 to 31 December 2027.
"""


class _Delta:
    def __init__(self, content: str) -> None:
        self.content = content


class _Message:
    def __init__(self, content: str) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str) -> None:
        self.delta = _Delta(content)
        self.message = _Message(content)
        self.finish_reason = "stop"


class _Usage:
    prompt_tokens = 900
    completion_tokens = 200
    cache_read_input_tokens = 0


class _Chunk:
    def __init__(self, content: str | None) -> None:
        self.choices = [_Choice(content)] if content is not None else []
        self.usage = None


class _FakeStream:
    """One object for both call shapes litellm is read in.

    The draft streams it (`for chunk in stream`, reading chunk.choices[0].delta), the
    summariser reads it directly (response.choices[0].message.content). Serving both
    keeps the probe driving the real pipeline rather than dying at the first call it
    does not match, which is what makes the measurement below about the pipeline.
    """

    def __init__(self, text: str) -> None:
        self._text = text
        self.choices = [_Choice(text)]
        self.usage = _Usage()

    def __iter__(self):
        for line in self._text.splitlines(keepends=True):
            yield _Chunk(line)
        last = _Chunk(None)
        last.usage = self.usage
        yield last


def _fake_completion(**_kwargs):
    """A streamed draft: the same shape litellm yields, no provider involved."""
    return _FakeStream(DRAFT)


def _install_stubs() -> None:
    import litellm

    litellm.completion = _fake_completion  # type: ignore[assignment]

    def _stub_entailment(claim: str, source: str, *, project_id: str = "") -> dict:
        return {"verdict": "yes", "reasoning": "stub", "model": "stub/model"}

    draft_mod.check_entailment = _stub_entailment  # type: ignore[assignment]


def _fd_count() -> int:
    for path in ("/dev/fd", "/proc/self/fd"):
        try:
            return len(os.listdir(path))
        except OSError:
            continue
    return -1


def _instrumentation() -> str:
    parts = []
    try:
        from prompt_matrix.db.open_connections import (
            db_open_connection_count,
            db_open_connection_sites,
        )

        parts.append(f"db_open_connections={db_open_connection_count()}")
        parts.append(f"sites={db_open_connection_sites()}")
    except Exception as exc:  # the gauge is absent on a pre-fix tree
        parts.append(f"db_open_connections=absent ({exc})")
    try:
        from prompt_matrix.db.pool import _pool_holders

        parts.append(f"pool_holders={len(_pool_holders)}")
    except Exception as exc:
        parts.append(f"pool_holders=? ({exc})")
    parts.append(f"open_fds={_fd_count()}")
    parts.append(f"threads={threading.active_count()}")
    return "  ".join(parts)


def _dump_stacks(label: str) -> None:
    lines = [f"=== STALL {label} :: {_instrumentation()} ==="]
    frames = sys._current_frames()
    for thread in threading.enumerate():
        frame = frames.get(thread.ident)
        lines.append(f"--- thread {thread.name} (ident={thread.ident}, daemon={thread.daemon}) ---")
        if frame is not None:
            lines.extend("".join(traceback.format_stack(frame)).splitlines())
    with open(OUT, "w") as handle:
        handle.write("\n".join(lines) + "\n")
    faulthandler.dump_traceback(all_threads=True, file=sys.stderr)


_HEARTBEAT = {"n": 0, "where": "start", "done": False}


def _watchdog() -> None:
    last = -1.0
    while True:
        time.sleep(2.0)
        if _HEARTBEAT["done"]:
            return
        if _HEARTBEAT["n"] != last:
            last = _HEARTBEAT["n"]
            continue
        time.sleep(STALL_S)
        if _HEARTBEAT["n"] == last and not _HEARTBEAT["done"]:
            _dump_stacks(f"stuck at {_HEARTBEAT['where']}")
            print(f"WEDGED at {_HEARTBEAT['where']} :: {_instrumentation()}", flush=True)
            os._exit(3)


def main() -> int:
    _install_stubs()
    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    init_db()
    project = "wedge-probe"
    ensure_project(project, "wedge probe")
    entry = save_substrate_entry(
        project,
        filename="acme-guideline.md",
        page_count=1,
        extracted_text=SOURCE,
        file_size_bytes=len(SOURCE),
    )
    substrate_id = entry["id"] if isinstance(entry, dict) else str(entry)
    print(f"=== WEDGE PROBE :: db={DB} :: worktree={WORKTREE} ===", flush=True)
    print(f"before loop :: {_instrumentation()}", flush=True)

    watchdog = threading.Thread(target=_watchdog, daemon=True, name="wedge-watchdog")
    watchdog.start()

    for index in range(ITERS):
        _HEARTBEAT["where"] = f"iter {index} (start)"
        started = time.time()
        events: list[str] = []
        gen = draft_mod.run_draft_pipeline(
            project,
            intent=f"Summarize the coverage limits. (run {index})",
            substrate_file_ids=[substrate_id],
            force=True,
            request_id=f"wedge-{index}",
        )
        try:
            for frame in gen:
                _HEARTBEAT["n"] += 1
                _HEARTBEAT["where"] = f"iter {index} (streaming)"
                events.append(frame[:60])
        except Exception as exc:  # a pipeline error is a result, not a wedge
            print(f"iter {index}: raised {type(exc).__name__}: {exc}", flush=True)
        elapsed = time.time() - started
        print(
            f"iter {index}: {elapsed:.1f}s frames={len(events)} :: {_instrumentation()}",
            flush=True,
        )

    _HEARTBEAT["done"] = True
    print("=== NO WEDGE in {0} iterations ===".format(ITERS), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
