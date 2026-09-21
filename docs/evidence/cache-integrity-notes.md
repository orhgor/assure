# Post-audit wave — orchestrator notes (RemarkableButterfly)
Started 2026-09-19. Worktree: /tmp/wt-postaudit (branch: TBD, based 5b36183).
Phase order: 0 -> A -> B -> C -> D. Sequential (B and C both write prototype/shell.js).

## Phase 0 — state

### 0.1 Box state (`i-03e39eccc57572191`) — measured 2026-09-19
| Item | Value |
|---|---|
| Box HEAD | `429fd09926ff3579ec90d732ec05dd1a026689a8` (`429fd09`) |
| Box HEAD parent | `7fde77f fix(shell): … a source contradicts …` (single parent) |
| Box branch | `prototype/shell-skeleton` |
| Box porcelain | empty (clean) |
| `git show HEAD:prototype/shell.js` md5 | `7c30e8e65cd0993b5b5a84b517d9a929` (262754 B) |
| disk `prototype/shell.js` md5 | `7c30e8e65cd0993b5b5a84b517d9a929` (262754 B, 2026-09-18 22:57:28 UTC) |
| `git show HEAD:prototype/dev-server.py` md5 | `58beb1f2295c5a00255b7110daed7dac` (17651 B) |
| disk `dev-server.py` md5 | `58beb1f2295c5a00255b7110daed7dac` |
| `index.html` md5 | `4fff77561e5c0ca736543edcd330b219` (23483 B) |
| `shell.css` md5 | `cb8e3e88864db2ad281c7385c86873a0` (71464 B) |
| `about.html` md5 | `10f837f764d43914f74f8eb5e1618974` (19109 B) |
| static unit | `assure-prototype-static.service` loaded/active/running, ActiveEnterTimestamp `Fri 2026-09-18 22:34:03 UTC` |
| app unit | `assure-prototype.service` loaded/active/running, ActiveEnterTimestamp `Fri 2026-09-18 22:34:24 UTC` |
| static ExecStart | `/home/ubuntu/assure-prototype/.venv/bin/python prototype/dev-server.py` |
| listeners | app `127.0.0.1:8890`, static `0.0.0.0:8891`, cloudflared `127.0.0.1:20241` |
| compile lock | free at measure time (`flock -n /tmp/assure-compile.lock` → `LOCK-FREE`) |

Served bytes check: `curl -s http://127.0.0.1:8891/shell.js` returned 0 bytes / md5 `d41d8cd9…`
(the empty-string digest) — the static server does not serve that bare path (the shell is served
under the tunnel host, not the origin root). **HEAD == disk is therefore the served proof**, not
the origin curl. Named as a gap, not a contradiction.

### 0.2 Box vs local — "content-identical, SHA-diverged because the box cannot push"
- local canonical `fixb/success-marker` tip: `7c8a422` (shell.js blob md5 `7c30e8e6…`, 262754 B)
- box `prototype/shell-skeleton` tip: `429fd09` (shell.js blob md5 `7c30e8e6…`, 262754 B)
- `git cat-file -e 429fd09` locally → `fatal: Not a valid object name` — the SHA is unknown locally.
- Box has TWO commits the local lineage squashes into one:
  `7fde77f` (noun "contradicts") then `429fd09` (noun "denies"); local `7c8a422` is a single
  commit on `5b36183`.
**Verdict: same content, different SHAs, box cannot push — expected, not a defect.**

### 0.3 Queued branches — the brief's "each on 5b36183" is only true for two of five
| Commit | Ref | merge-base with `5b36183` | `5b36183` an ancestor? |
|---|---|---|---|
| `2e8200a` | `fix3b/pdf-retrieval` | `a536a52` | **NO** |
| `9cadf86` | `fix3d/audit-drops-and-fresh-fk` | `a536a52` | **NO** |
| `f674370` | (same branch, later) | `a536a52` | **NO** |
| `8ff6614` | `docs/drift-3e-2026-09-19` | `a536a52` | **NO** |
| `7c8a422` | `fixb/success-marker` | `5b36183` | YES |
| `0b633a4` | `fix/about-page-truth` | `5b36183` | YES |

So the five queued items sit on **two different bases**: `7c8a422`/`0b633a4` on the box shell
lineage, and `2e8200a`/`9cadf86`+`f674370`/`8ff6614` on the `a536a52` lineage. Measured, not assumed.

### 0.4 `SANDBOX_VERDICT = no` (already answered; re-measured read-only)
- box `SELECT COUNT(*) FROM projects` → **144**
- `SELECT id FROM projects WHERE id='sandbox'` → **`[]`** → absent
- `pipeline_cache` rows: **336** — kinds `[('ast', 122), ('entailment', 214)]`
- rows whose `project_id` is not a real project: **`[]`** (zero sentinel rows)
- `kind='relational_metric'` rows: **0**; `kind='redhat'` rows: **0**

### Lineage fact discovered while quoting D0 (material to where D lands)
`prompt_matrix/services/relational_translate.py` **does not exist** in the box lineage
(`5b36183`/`7c8a422`/`429fd09`) — measured: absent on the box, absent in the worktree,
present only on local `feat/math-check-tier2` (`2eb27fc`), whose `prototype/shell.js` is
`bef198bf…` (the pre-`7c30e8e6` content). `entailment_cache.py` (`757d75b9…`),
`pipeline_cache.py` (`40540565…`) and `omp_memory.py` (`03126785…`) are byte-identical
across both lineages; `lib/logger.py` and `web.py` are not.

---

# RE-SCOPE (IRC from `Main`, mid-run): A, B, C dropped; D is mine

Main's ruling: `DomesticKite` holds A/B/C and is further along (C1 `3999c38`, C3 `7e18dc8`,
A2 `6f9074a`, B2 `5680948`, C1-counter `9cf7c9b`, C2 `e4295f9`); both touch `prototype/shell.js`,
so one writer per file gives it the file. **I write no shell.js.** Phase D is mine alone.

**Own worktree, per the ruling.** `/tmp/wt-paw` (branch `postaudit/wave`, forked from
`postaudit/fixes`) was created and then removed; the final worktree is
**`/tmp/wt-postaudit-d`, branch `postaudit/cache-integrity`**.
I also rebased `postaudit/fixes` onto `7c8a422` earlier in this run (before the ruling) — that
rewrote DomesticKite's five SHAs mid-run. It survived (clean rebase; it reported the new SHAs),
but it was wrong: **rebase only branches you own.** Recorded.

## Base for D — chosen, with the reason measured
`postaudit/cache-integrity` is based on **`2eb27fc`** (`feat/math-check-tier2`), not on
`7c8a422`/`5b36183`. Measured reasons:

| File | 5b36183 / 7c8a422 lineage | 2eb27fc |
|---|---|---|
| `services/relational_translate.py` (sentinel 2) | **ABSENT** | `0e37433d…` |
| `services/entailment_cache.py` (sentinel 1) | `757d75b9…` | `757d75b9…` (identical) |
| `services/omp_memory.py` | `03126785…` | `03126785…` (identical) |
| `lib/logger.py` + `web.py` (the `audit_drops` template) | **absent** | present (`_audit_drops`, `payload["audit_drops"]`) |
| `scripts/aws/migrate_fk_constraints.py` | absent | present |

D0 names **both** sentinels, so the branch must contain both files; only the math-check
lineage does. That lineage also carries the `audit_drops` template the brief names, on
`f674370` ("a dropped audit row is counted, and no longer blocks the next write") — which is
the same defect shape as D and gives the precedent for the rollback (`db/connection.py`'s
fresh `pipeline_cache` DDL has **no** FK there; the FK is declared by `9cadf86`
`fix3d/audit-drops-and-fresh-fk` for fresh databases, and by
`scripts/aws/migrate_fk_constraints.py` for the migrated ones).

## PHASE 0 — D0

### The two sentinels, quoted (`postaudit/cache-integrity` @ `2eb27fc`)

`prompt_matrix/services/entailment_cache.py:87-91`
```
        save_pipeline_cache(
            cache_key, project_id or "entailment", CACHE_KIND, payload, ttl_days=ttl_days
        )
    except Exception:
        return
```

`prompt_matrix/services/relational_translate.py:325-333` (`CACHE_KIND = "relational_metric"`, `:59`)
```
    try:
        save_pipeline_cache(
            cache_key_value,
            project_id or CACHE_KIND,
            CACHE_KIND,
            {"claim": claim, "z3_version": z3_version()},
        )
    except Exception:
        return
```

### The swallows, quoted
`prompt_matrix/services/omp_memory.py:133-136`
```
    try:
        save_pipeline_cache(cache_key, project_id, "ast", payload)
    except Exception:
        pass
```
`prompt_matrix/services/omp_memory.py:192-195`
```
    try:
        save_pipeline_cache(key, project_id, "redhat", payload)
    except Exception:
        pass
```
`entailment_cache.py:90-91` and `relational_translate.py:332-333` are the two above.

### `pipeline_cache`'s `project_id` column and its FK
Fresh-database DDL, **this branch** (`prompt_matrix/db/connection.py:643-649`): `project_id
TEXT NOT NULL`, **no FK clause**. The declaration for fresh creation is `9cadf86`'s
(`prompt_matrix/db/connection.py:659-663` there):
```
            project_id TEXT NOT NULL,
            ...
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
```
The migrated shape is what staging runs, quoted from its `sqlite_master`:
```
CREATE TABLE "project_budgets" ( project_id TEXT PRIMARY KEY, ... ,
  FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE)
```
and `scripts/aws/migrate_fk_constraints.py` lists `pipeline_cache` in `TABLES` (`:46`), with its
header recording: *"Measured on staging 2026-09-18: … pipeline_cache 24 … — 756 orphans in
tables that had no constraint."* `prompt_matrix/history.py:58` sets
`PRAGMA foreign_keys=ON` on every connection, so the FK is enforced per connection.

## PHASE D — the fixes

Applied on `postaudit/cache-integrity`:
1. `lib/logger.py` — `cache_drop_count()` / `note_cache_drop(site, project_id, kind)`, the
   `audit_drops` template, plus a `_log.warning("[cache-drop] …")`.
2. `web.py` `/api/health` — `payload["cache_drops"] = cache_drop_count()`.
3. `db/pipeline_cache.py` — `save_pipeline_cache` now rolls the rejected statement back and
   re-raises: SQLite holds the write lock for an uncommitted statement, so a swallowed
   rejection left the shared connection unusable and the NEXT write failed `database is locked`
   (measured — the same cascade `f674370` fixed for audit rows).
4. `entailment_cache.store_verdict` — `project_id or "entailment"` → `project_id`; `except
   Exception: return` → `except sqlite3.IntegrityError:` + `note_cache_drop(...)`; module
   docstring carries the invariant.
5. `relational_translate.store_translation` — `project_id or CACHE_KIND` → `project_id`; same
   narrowing; invariant in the module docstring.
6. `omp_memory.save_ast_cache` / `save_redhat_critique` — both swallows narrowed to
   `sqlite3.IntegrityError` + `note_cache_drop`.
7. `scripts/precommit_cache_invariant.py` + a `.pre-commit-config.yaml` local hook
   (`cache-invariant`): AST guard that fails on an `or` fallback in the `project_id` argument
   of `save_pipeline_cache`, and on a bare `except Exception` guarding it.

## PHASE D0.4 — the answer: **"no, dead path"** (Option 3)

### The probe that settles it (raw, both DB shapes)
`/tmp/pah/probe_d04.py` instruments the two write sites and drives the real
`POST /api/projects/<project_id>/draft/stream` route. Raw output:

```
CASE 1 — GOLDEN PATH, project the app created
  write: {"site": "omp_memory", "received_project_id": "'d04-with-parent'", "truthy": true,
          "kind": "ast", "projects_row_exists": true, "outcome": "written"}
  run2 http 200 events: ['status … "stage": "cache", "message": "Loaded from memory…",
          "omp_cached": true, "cache_key": "ast:d04-with-parent:f99d61a5"', …]
  run2 writes (a replay writes nothing): []
```

The route's `project_id` is a URL path segment (`@app.post("/api/projects/<project_id>/draft/stream")`),
so it is non-empty by construction; every caller of the four write sites passes that value
(`draft.py:1180` → `check_entailment(..., project_id=project_id)`; `draft.py:1274` →
`save_ast_cache(cache_key, project_id, …)`; `draft.py:1432` → `save_redhat_critique(project_id, …)`;
on the math-check lineage `draft.py:1406` → `translate_claim(claim, facts, project_id=project_id)`).

### The same question answered from the deployed database
Pre-migration staging backup `/home/ubuntu/backups/history.sqlite.20260918T194353Z.bak`
(the table had **no FK then**, so a sentinel write would have persisted):
```
literal 'entailment':          pipeline_cache rows=0   projects rows=0
literal 'relational_metric':   pipeline_cache rows=0   projects rows=0
literal 'math_check_relational': pipeline_cache rows=0 projects rows=0
literal 'default':             pipeline_cache rows=3   projects rows=1   (the migration's own sentinel)
any NULL project_id: 0     any empty-string project_id: 0
distinct project_id count: 79     kinds: [('ast',), ('entailment',)]
the 24 orphans: all real-looking scratch ids ('probe-f2f3-…', 'phase-c-guard-…', 'wave3-…'),
                none of them a sentinel
```
**The fallback has never fired in production.** The 24 orphans that
`migrate_fk_constraints.py` deleted were rows whose *real* project id had no parent — the class the
counter exists for, not the sentinel.

**Verdict: Option 3 — remove the sentinel fallbacks.** Not Option 1 (prohibited: it seeds a sentinel
as a project). Not Option 2 (a table rebuild buys nothing when no caller passes `None`; measured).
Residual: a falsy id remains *expressible* through the function defaults (`check_entailment(...,
project_id="")`, `translate_claim(..., project_id="")`), so the invariant is enforced at the write as
well — see below — and statically by the new pre-commit rule.

## PHASE D — evidence

Branch **`postaudit/cache-integrity`**, base **`7c8a422`** (`shell.js` md5 `7c30e8e6…`, the served
file) — the deployable half. Branch **`postaudit/cache-integrity-relational`**, base **`2eb27fc`**
— the relational half, whose module does not exist on the other lineage.

**A third defect, measured, that the brief did not name — the connection is poisoned.**
`/tmp/pah/probe_prefix.py`, on the pre-fix base `7c8a422`, migrated shape:
```
B. a refused write (project_id with no `projects` row):
   store_verdict returned without raising (swallowed)
   rows for the parentless id: []
C. the write AFTER the refusal:
   landed: []
```
The rejected statement is left uncommitted, SQLite keeps the write lock, and **the next legitimate
cache write — for a real project — is lost too**. So `save_pipeline_cache` now rolls the failed
statement back before re-raising, and one test covers it.

And the sentinel's own damage, on the shape without the constraint (`PROBE_SHAPE=nofk`, pre-fix):
```
D. a falsy project_id:
   rows now: [('entailment:orphan', 'no-such-project', 'entailment'),
              ('ast:real-proj:1', 'real-proj', 'ast'),
              ('entailment:falsy', 'entailment', 'entailment')]      <- the literal AS the project id
```
```
math lineage: stored rows: [('relational_metric:falsy', 'relational_metric', 'relational_metric'),
                           ('relational_metric:orphan', 'no-such-project', 'relational_metric')]
```
A falsy id is now refused by `save_pipeline_cache` itself (raised as `sqlite3.IntegrityError`, the
error the constraint raises) *and* by the FK where one exists, so the two shapes agree.

### Tests — branch, and result
`tests/test_cache_integrity.py`, 7 tests per branch.
- `postaudit/cache-integrity` (base `7c8a422`): **7 passed**
- `postaudit/cache-integrity-relational` (base `2eb27fc`): **7 passed**
Both run as `/Users/og/Untitled/.venv/bin/python` with `cwd` in the worktree and
`PYTHONPATH=/tmp/pah` + `PAH_WORKTREE=<worktree>`; the harness prints
`PROOF prompt_matrix imported from: <worktree>/…`.

### Proven to fail against pre-fix code
`tests/test_cache_integrity.py` copied into a worktree at the pre-fix base `7c8a422`:
```
tests/test_cache_integrity.py:31: in <module>
    from prompt_matrix.lib.logger import cache_drop_count
E   ImportError: cannot import name 'cache_drop_count' from 'prompt_matrix.lib.logger'
1 error in 0.20s
```
With **only the counter shimmed in** (same names, write behaviour untouched) so the tests reach the
behaviour — 5 failed, 2 passed:
```
FAILED test_a_write_the_foreign_key_refuses_is_counted_and_writes_nothing - assert 0 == (0 + 1)
FAILED test_a_refused_write_leaves_the_connection_usable - assert 0 == 1  (rows == [])
FAILED test_a_falsy_project_id_is_not_stored_under_a_sentinel - assert 0 == (0 + 1)
FAILED test_a_failure_that_is_not_a_foreign_key_propagates - Failed: DID NOT RAISE OperationalError
FAILED test_health_reports_the_refusals_it_has_seen - KeyError: 'cache_drops'
```
Same file on the relational base `2eb27fc` with the shim: **4 failed, 3 passed**.

**HONEST GAP — the two D4 end-to-end tests pass pre-fix.** With the counter shimmed in, both
`test_a_compile_hits_the_cache_twice_and_the_second_run_is_the_hit` and
`test_a_math_check_hits_the_cache_twice_and_the_second_run_is_the_hit` pass against pre-fix code:
they assert the cache contract each path must **keep** across the fix (second run is a hit), and the
pre-fix defect is the *silent refusal*, which they do not themselves provoke. Against pre-fix code
they fail only at module import (the `cache_drop_count` import above). The defect is caught by the
five tests that do fail pre-fix. Stated rather than papered over.

### Pre-existing, not mine
`tests/test_omp_memory.py::test_draft_pipeline_cache_hit_skips_claude` fails **identically on the
pre-fix base** (`1 failed in 1.73s`, same `zero_anchored_claims` 422) — the R2 provenance refusal
rejects that fixture draft. Not caused by this change.
`ruff check` on the base files reports **19 pre-existing errors** (`--select E4,E7,E9,F,I`); the
`--fix`/`format` churn they produced was reverted so the diff stays scoped. The new files
(`scripts/precommit_cache_invariant.py`, `tests/test_cache_integrity.py`) are ruff-clean.

### Lineage gap, measured (it forces the split)
`prompt_matrix/lib/source_labels.py` exists at `5b36183`/`7c8a422` (and on the box) but **not** at
`2eb27fc`, so `services/web_retrieval.py:48`'s import fails there and `create_app` cannot be built on
the math-check lineage at all. Conversely `services/relational_translate.py` (and `relational_z3.py`)
do not exist on the shell lineage. **No commit in this repo has both**:
```
7c8a422  source_labels=Y audit_drops=N relational=N
2eb27fc  source_labels=N audit_drops=Y relational=Y
```
So Phase D lands as two commits on two branches; the `/api/health` exposure is on
`postaudit/cache-integrity` only, because that is the lineage where the route can be tested.
