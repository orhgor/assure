"""Z3 solver pool integrity across garbage collection.

Regression guard for the 2026-09-17 segfault: ``TruthLedgerEngine.__del__`` used to
return its solver to the module pool from a finalizer. When the engine was only
reachable through a reference cycle, CPython ran that finalizer as part of a
cyclic collection and then finalized the *rest* of the unreachable set anyway — so
the solver it had just pooled was freed natively while the pool kept a wrapper over
the freed memory. The next borrow called ``solver.reset()`` on that dangling handle
and the process died with SIGSEGV inside z3's C++ (no exception to catch; the crash
surfaced in an unrelated test's fixture, which is what made it look flaky).

The invariant asserted here is the one that failure violated: a solver whose engine
is reclaimed by the collector must never end up in the pool.
"""

from __future__ import annotations

import gc

from prompt_matrix.ledger import truth_engine as te


def test_gc_finalized_engine_does_not_pool_its_solver():
    te._z3_pool.clear()

    engine = te.TruthLedgerEngine()
    # Self-reference makes the engine cyclic garbage, so __del__ runs from a
    # cyclic collection rather than from plain refcount teardown.
    engine._cycle = engine
    del engine
    gc.collect()

    assert te._z3_pool == [], (
        "a GC-finalized engine pooled its solver; that wrapper can outlive the "
        "solver's native memory and the next reset() would dereference freed memory"
    )


def test_explicit_close_still_pools_the_solver():
    """The pool still works when ownership is explicit (the point of close())."""
    te._z3_pool.clear()

    engine = te.TruthLedgerEngine()
    engine.close()

    assert len(te._z3_pool) == 1, "close() must return the solver to the pool"
    assert engine._released is True, "close() must be idempotent-marked"

    # Idempotence: a second close() must not double-pool.
    engine.close()
    assert len(te._z3_pool) == 1

    # A pooled solver is usable afterwards (this is the call that segfaulted when
    # the pool held freed memory).
    pooled = te._z3_pool.pop()
    pooled.reset()
    te._z3_pool.clear()
