# The connection-leakage family — enumerated, fixed at the cause, and countable

Branch `fix/connection-leakage-family`, base **`b33019c`** (`postaudit/cache-integrity`,
which is the box lineage: `7c8a422` → `b33019c` off `5b36183`).

Measured against, md5 at the base → md5 after this branch:

| file | at `b33019c` | after |
|---|---|---|
| `prompt_matrix/db/connection.py` | `af1ef8fe1f9336c0e67fba1189816bf3` | `01579dace8f39739ef6a7363a1afe737` |
| `prompt_matrix/history.py` | `6110553002176ad97d64cd7cc0b016bf` | `779c503d4c3582a243c8b82497920538` |
| `prompt_matrix/db/pool.py` | `960587472932f2b01aab158517b4466c` | `9a826e73d20386b04b7dcdae8b06ed54` |
| `prompt_matrix/db/open_connections.py` | absent | `ca8e6cbb56aaf48d7aabce00677953b7` |
| `prompt_matrix/lib/logger.py` | `d0cd40b2b1d7efffefdcef8cbbc84105` | `ec8cfa3e2621c479323829b510795658` |
| `prompt_matrix/lib/telemetry.py` | `89ec644d5891e5151d1efb0da55739bb` | `3f67f1bc1ed2dc8406b244ab5e270865` |
| `prompt_matrix/routers/health.py` | `f2e84ef7cb8a0425fbedf7aac4dacc1f` | `c9158eb20f2246b9e5989997424d9210` |
| `prompt_matrix/routers/draft.py` | `a73f4cb29bd763affcd36d96b4d91eec` | `f51da461a0baa4e948ad07b2a55ddf99` |
| `prompt_matrix/db/audit_repository.py` | `1731fbda912596933a08daf9814f7907` | `949dc3d3bb21fdc95720da28ee232072` |
| `prompt_matrix/db/jdf_repository.py` | `bf3b57d566123f2d4bdafd29ece6d407` | `ac39e6015438ae69436d52ea1f4fb583` |
| `prompt_matrix/db/substrate_repository.py` | `a63e5caac0be13ebd6743772bd7ef3b4` | `373afb8d5c14e4bf5fa08108d4228126` |
| `prompt_matrix/web.py` | `497686b92fda24e90d99be9e90b42b50` | `98bfba65b266f3bc6554309808ae4db6` |
| `prompt_matrix/db/pipeline_cache.py` | `ab7189e2d4d9baead8007b2531236754` | unchanged (fixed in Phase D) |
| `tests/test_connection_leakage.py` | absent | new |

Not touched, as briefed: `prototype/shell.js`, the A1 files on `fixa/intent-handoff`,
and the compile lock (`scripts/compile_lock.py`).

## 1. The shape, stated once

**A failed or interrupted write leaves a resource that damages the NEXT writer.** Four
instances have now been measured; every one is a connection, or the lock its open
statement holds, outliving the operation that took it:

| # | instance | what was held | damage to the next writer |
|---|---|---|---|
| a | `logger.py`'s audit drop (**`f674370`**, the other lineage) | the failed insert's connection, uncommitted | the next audit write timed out, `database is locked` |
| b | `bg_44`'s run, 119 open descriptors, `state R`, CPU `00:00` | the pool, plus a direct connection per later DB touch | every later DB touch waited 5s and leaked an fd |
| c | `save_pipeline_cache` (**`de0e1bc`**) | the refused statement, uncommitted | the next cache write was silently lost (`landed: []`) |
| d | `run_draft_pipeline`'s post-entailment park | the same as (b) — see §4 | the park itself, and the fd storm |

## 2. Failure paths found, and what each left open

The enumeration came from an AST scan of every `try` in `prompt_matrix/` whose body
touches a connection, statement, or cache write and whose handler swallows (`pass`,
`return`, or a log with no `raise`), then a manual read of each hit to separate "holds a
connection or an open transaction" from "swallows a read".

### Fixed here

| site | what it left open | fix |
|---|---|---|
| `lib/logger.py:152-236` `AuditLogger.log_audit` | On any failure between connect and close: the connection stayed open **and uncommitted**, so it held SQLite's write lock. The drop path is reached by ~40 route call sites. | `finally:` now releases it — rollback *then* quiet close, via `_release_direct_connection`. |
| `lib/telemetry.py:21-45` `collect_system_metrics` | Same shape (connect → INSERT → `commit()` → `close()` in one `try`): a failed commit left the connection and its write lock open, on a path that runs on a schedule. | `closing_connection()` — rollback + close in a `finally`. |
| `lib/telemetry.py:48-61` `prune_old_logs` | Same, with two `DELETE`s and a `VACUUM`: the widest uncommitted window in the codebase. | Same helper. |
| `routers/health.py:59-70` `/health` sqlite probe | `conn.close()` was inside the `try`; a failing `SELECT 1` (a locked or damaged database — exactly when `/health` matters) left the connection open **once per poll**. | `closing_connection()`. |
| `routers/health.py:94-110` `/health` backup/metrics probe | Same shape, two `SELECT`s, one leaked connection per failed poll. | Same helper. |
| `db/audit_repository.py:17-24` `_connect()` + `fetch_audit_entries` | Never closed at all — not on a failure path, on every path. Every compliance export abandoned one connection and its open read transaction. | `_connect()` deleted; the one caller scopes it. |
| `db/connection.py:993-1011` `_connect_with_retry` | `conn.close()` sat in an `except sqlite3.OperationalError` guard, so a connection was abandoned by any *other* failure (a pragma raising `DatabaseError`) while the loop raised out. | `except BaseException` → `_release_direct_connection(conn)` before the retry/raise decision. |
| `db/pool.py:78-102` `checkout_dbapi_connection` | `raise RuntimeError` with the fairy already out of the pool: the pool was permanently one connection shorter, and the next writer needing that slot waited out `pool_timeout` and failed. | The fairy is closed (returned) before the raise; a return that itself fails is logged and stays counted. |
| `db/pool.py:105-137` `release_dbapi_connection` | `except Exception: pass` around both closes: a connection that could not be closed, and could not be returned, vanished from every bookkeeping structure. | Release failures are logged and **stay counted** in `db_open_connections` — best-effort kept, the leak made visible. |
| `db/connection.py:789-817` `init_db` | A migration failing half way left its DDL uncommitted on the caller's connection (usually the request's): write lock held, and the next `commit()` on that connection committed the partial migration. | `_migrations_guarded`: rollback on the failure path, before the error travels. |
| `db/connection.py:800` `init_db` (connection acquisition) | `get_db()` with no scope: one pooled checkout per call, never returned. Called by nearly every repository function — **6-12 per pipeline start**, measured. | `db_scope()`. |
| `routers/draft.py:763-812` `run_draft_pipeline` | Standalone (no request), every `get_db()` inside the pipeline opened a pooled checkout that nothing returned. One start took the whole pool. | The public generator wraps the body in `db_scope()`. |

### Already fixed at the cause before this branch — kept as guards, not re-fixed

| site | state |
|---|---|
| `db/pipeline_cache.py:88-98` `save_pipeline_cache` | Rolls the refused statement back and re-raises (`de0e1bc`). Unchanged here; `test_a_refused_cache_write_...` in `tests/test_cache_integrity.py` is its guard. |
| `services/entailment_cache.py:108-119`, `services/omp_memory.py`, `routers/draft.py:1273,1431` | The `except Exception: pass/return` swallows around `save_pipeline_cache` remain **by design** (best-effort cache writes); what changed is that the cause now rolls back, so a swallow cannot leave the shared connection poisoned. |
| `lib/logger.py` `log_audit`'s `finally` | The other lineage's `f674370` closes the connection on both paths. This branch's version is the same `finally` with rollback + quiet close + gauge drop; it is not a re-discovery, and the trunk already carries theirs. |

### Checked and not members (swallow a *read*; no resource outlives the call)

`services/jdf_memory.py:259,469`, `services/audit_bundle.py:98`,
`services/jdf_sidecar.py:301,320`, `services/entailment_cache.py:76-90` and
`history.py:356,378` — these catch around `SELECT`s (or a module import) on the
request-scoped handle. The swallowed statement is finished when the handler runs, so
nothing is held; the damage they do is a silently wrong answer, not a held resource.
Said plainly because the AST scan named them and a successor should not re-read them.

## 3. `db_open_connections` on `/api/health`

Same shape as `audit_drops` and `cache_drops`: module-level state
(`prompt_matrix/db/open_connections.py`), one lock, one accessor, one health key.
`payload["db_open_connections"] = db_open_connection_count()` in the same handler
(`web.py:1035-1046`).

It is a **gauge**, not a cumulative counter: a connection is counted when the shared
layer hands it out and dropped when it is really back — returned to the pool, or
closed. Keyed by `id(conn)` because a `sqlite3.Connection` is not weak-referenceable
(measured), and holding a strong reference would keep a leaked connection alive, which
would make the detector the defect. `db_open_connection_sites()` returns the same count
grouped by the **caller** that took each connection (`file:line:function`), so a leak is
named from the health payload rather than from a stack sample.

Measured, deliberate load (4 threads × 25 pooled writes, all four connections held at
once behind a `threading.Barrier` whose action reads the count):
`observed == [4]` while held, `0` after. Under the same shape on `/api/health`:
baseline → `+1` with a connection in hand → baseline again.

## 4. The suspect fourth instance: **family**, with the mechanism

**Verdict: family, and it is the same defect as (b)** — the shared resource is the
connection pool, not a transaction, but the shape is identical: a resource held with
nothing consuming it, and the *next* writer paying for it.

### What was on record before this branch

`FixA`'s report (`agent://FixA`, `deviations_and_flags`) flagged
"`run_draft_pipeline` intermittently wedges after the entailment stage (main thread
parked on a Python-level lock; stack sampled twice)" and committed it nowhere. The two
samples themselves are on disk, and are the only C-level captures of it:

- `/tmp/s2.txt` (md5 `18b0df6b5cb44d58bca5be2bc8e41866`, `sample` of pid 191,
  02:00:22), main thread:
  `list_vectorcall → _list_extend → gen_iternext → _PyEval_EvalFrameDefault →
  method_vectorcall_VARARGS_KEYWORDS → lock_PyThread_acquire_lock →
  _PyMutex_LockTimed → _PyParkingLot_Park → __psynch_cvwait`.
- `/tmp/s3.txt` (md5 `16e53c5827c939f52ffa2362edf8a4b4`, 02:22:09): the same park, at
  module level rather than inside the generator; 15 other threads idle in
  `_queue_SimpleQueue_get` (a `ThreadPoolExecutor`'s workers), one sample inside
  `pysqlite_connection_execute`.

`sample` prints C frames only, so those files can name neither the lock nor its holder.
That is the honest limit of the existing evidence, and it is why the classification
below rests on a new measurement rather than on re-reading them.

### The measurement (deterministic, no provider, no compile lock)

`scripts/probe_pipeline_pool.py` (added by this branch; it drives the real
`run_draft_pipeline` in-process with a stubbed `litellm.completion` — one fake object
serving both the streaming draft and the non-streaming summariser — and a stubbed
entailment checker, so it needs no keys, no network, and no compile lock) with a
Python-level watchdog that dumps `sys._current_frames()` for every thread plus this
instrument when progress stops. Both sides below are the same script, same stub, three
standalone runs, against the pre-fix base at `b33019c` in its own worktree
(`/tmp/wedge-prefix`) and against this branch:

```
PRE-FIX (`b33019c`)                        POST-FIX (this branch)
before loop: pool_holders=5  open_fds=30   before loop: db_open_connections=0  open_fds=22
iter 0: 125.3s  pool_holders=20  112 fds   iter 0: 0.1s  db_open_connections=0   24 fds
iter 1: 200.4s  pool_holders=20  126 fds   iter 1: 0.0s  db_open_connections=0   24 fds
```

Both pre-fix runs ended at `pool_holders=20` — the pool, held — with the descriptor
count still climbing when the run was stopped (126 and rising, `state R`, CPU idle),
which is instance (b)'s signature produced deterministically rather than intermittently.
`db_open_connections=absent` on the pre-fix side is the gauge reporting that its own
module does not exist there; the pre-fix numbers are `pool_holders` (the pre-existing
registry in `db/pool.py`) and the platform's fd count, so the two columns are not
comparable key-for-key. The seconds and the descriptors are, and they are the ones
quoted.

The earlier stub, which died at the summariser call rather than driving the whole
compile, produced the same geometry over three starts
(`0.0s/59 fds → 75.1s/89 fds → 75.2s/119 fds`): the leak needs only a pipeline *start*,
not a successful compile.

### The mechanism, named by the instrument

Outside a Flask request, `get_db()` handed out a **new pooled checkout per call** and
nothing returned it. `pool_size 5 + max_overflow 15 = 20` — one pipeline start took the
whole pool. Every later DB touch then waited out `pool_timeout` (5s), `_new_connection`
swallowed that `TimeoutError` with a warning and opened a **direct** connection instead,
so the process accumulated one more descriptor per touch and 75.1s per iteration is
exactly 15 × 5s of pool waits. The site attribution, taken from the gauge before the fix:

```
cost_governance.py:242:_connection                     x9  (x18, x27 across runs)
connection.py:800:init_db                              x6  (x9, x12)
pipeline_cache.py:119:sqlite_cache_expired             x1
pipeline_cache.py:34:fetch_pipeline_cache              x1
substrate_repository.py:289:fetch_substrate_entries_by_ids
jdf_repository.py:66:ensure_project                    x1
substrate_repository.py:71:save_substrate_entry        x1
```

### The fix, at the cause

`history.db_scope()`: inside a request it is the request's own handle (unchanged); outside
one it takes a connection, registers it for the thread, and **gives it back when the block
ends**. `get_db()` joins an open scope instead of opening another, so a standalone unit of
work now has the same one-connection property a request has always had. The measured
standalone boundaries are scoped: `init_db()`, `run_draft_pipeline`, `ensure_project`,
`save_substrate_entry`, `fetch_substrate_entries_by_ids`.

### Why this is not a harness artifact

The probe is a harness, but the defect is not: the same `get_db()` is reached by every
non-request caller — a script, a Celery task, the CLI, the MCP server, and any thread the
request never owned (the SSE keepalive pump's worker thread is named in
`history._new_connection`'s comment for exactly this reason). In a request the pool is
never at risk, which is why this survived: the leak needs no request to be absent, only a
caller that is not one.

### And why not SIGSTOP or the compile lock

`CompileLockLease` measured that a SIGSTOPped holder keeps its `flock`. That is not this:
the probe process was running (state `R`, the fd count climbing deterministically, 75.1s
of real waits per iteration), no lock was taken (deliberately — the compile lock belongs
to another task), and the mechanism is reproducible with no lock in the picture at all.
A lease cannot explain a process that never acquired the lock.

## 5. Tests: detectors and guards

`tests/test_connection_leakage.py` — the split is the point, and it is in the module
docstring:

**Detectors** (fail on the pre-fix base; they import nothing this branch added, so the
pre-fix run reaches the behaviour rather than an `ImportError`):

- `test_a_refused_audit_row_does_not_block_the_next_audit_write` — the next-writer proof:
  the refused row costs its own row and nothing else. Pre-fix: `assert [] == ['req-next']`
  — the second, legitimate audit write wrote nothing, because the first one's refused
  statement still held the write lock.
- `test_a_failed_pool_checkout_returns_the_connection_to_the_pool` — the fairy goes back
  before the raise. Pre-fix: `assert False is True` (`returned`).
- `test_a_failed_migration_leaves_no_open_transaction` — `conn.in_transaction` is False
  after a migration that wrote and then raised. Pre-fix: `assert True is False`. The
  failing migration does a DML statement before raising on purpose: DDL alone runs in
  autocommit, so a migration that only created tables would leave nothing to roll back
  and the assertion could not fail.

**Guards** (cannot fail pre-fix — the symbols they read did not exist — stated rather
than presented as coverage): `test_an_audit_write_leaves_no_connection_behind`,
`test_a_failed_direct_write_leaves_the_database_writable`,
`test_a_borrowed_connection_is_counted_while_it_is_held`,
`test_a_deliberate_load_holds_many_connections_and_gives_all_of_them_back`,
`test_pooled_writes_give_every_connection_back`,
`test_api_health_reports_the_open_connection_count`.

The whole file, run against the pre-fix base in its own worktree (`/tmp/wedge-prefix`,
`b33019c`) with nothing else changed: **9 failed** — the three detectors on behaviour,
the six guards on `ModuleNotFoundError: No module named 'prompt_matrix.db.open_connections'`
or `ImportError: cannot import name 'closing_connection'` — and against this branch:
**9 passed**.

Scoped run of the touched modules' own test files:
`tests/test_connection_leakage.py tests/test_logger.py tests/test_cache_integrity.py
tests/test_connection.py tests/test_sqlite_pool_release.py tests/test_db_pool_engine_rebind.py
tests/test_history.py tests/test_audit_log.py tests/test_health.py tests/test_audit_bundle.py
tests/test_substrate.py tests/test_cache_ttl.py tests/test_draft.py` → 60 passed, 1 failed.

**The one failure is not this change**: `tests/test_draft.py::test_run_draft_pipeline_omp_cache_hit`
fails identically on the pristine base (`git stash` of this branch, same assertion), and it
is the cache-replay `zero_anchored_claims` refusal that `FixA` and `Main` isolated
independently — pre-existing, unowned, and unrelated to connections.

## 6. Gaps, stated

- The gauge names a leak by caller only through `db_open_connection_sites()`, which is
  in-process API; the health key is the count alone, matching the two counters it sits
  beside.
- `get_db()` called **outside** any scope by a standalone caller still takes a checkout
  nobody returns. The measured hot paths are scoped; the counter is what makes any
  remaining one visible, and the fix is the same one line (`with db_scope():`) at that
  caller. The modules with the most remaining unwrapped sites are
  `db/substrate_repository.py` (9), `db/jdf_repository.py` (7) and the routers, all
  request-path today.
- The pre-fix capture reports `pool_holders` rather than `db_open_connections`, because on
  the pre-fix base the gauge has no hooks. The two numbers are not comparable one-to-one;
  the fd count and the per-iteration seconds are, and they are the ones quoted.
- The two `sample` files are C-level and cannot name the lock; the classification rests on
  the deterministic reproduction, not on those files, which are recorded here only so the
  classification has the same evidence a successor would start from.
