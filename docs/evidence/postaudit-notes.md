# Post-audit fixes — incremental notes (branch `postaudit/fixes`)

One agent, one `shell.js` writer. Phases 0 → A → B → C, serialised.
Worktree: `/tmp/wt-postaudit`, branch `postaudit/fixes`, based on `5b36183`.
Every pytest runs as `/Users/og/Untitled/.venv/bin/python` with cwd `/Users/og/Untitled`.
Compiles serialise under `/tmp/assure-compile.lock`.

---

## PHASE 0 — read-only state (COMPLETE)

### 0.1 Box state (`i-03e39eccc57572191`)

| Item | Value |
|---|---|
| Box HEAD | `5b36183 feat(shell): the 401 fix on the box shell, union-merged with the local lineage` |
| Box HEAD parent | `43a9450` (single parent — **not a merge commit**) |
| Box `git status --porcelain` | empty (clean) |
| Served `shell.js` md5 | `27df966b6b7d35092d6971e827602929` (260527 bytes, root:root, Sep 18 22:33) |
| Served `dev-server.py` md5 | `58beb1f2295c5a00255b7110daed7dac` (17651 bytes, ubuntu:ubuntu, Sep 18 22:03) |
| `assure-prototype-static.service` | loaded **active running**; `ActiveEnterTimestamp=Fri 2026-09-18 22:34:03 UTC` |
| `assure-prototype.service` | loaded **active running**; `ActiveEnterTimestamp=Fri 2026-09-18 22:34:24 UTC` |
| Static ExecStart | `/home/ubuntu/assure-prototype/.venv/bin/python prototype/dev-server.py` |
| DB | `/home/ubuntu/assure-prototype/prompt_matrix/history.sqlite` (no `data/` dir; no sqlite3 CLI on box) |
| Docker | none in use (`docker ps` empty) |

**Brief said box HEAD is `43a9450` and the merge has NOT landed. Both are now stale.**
Box HEAD is `5b36183`, one commit above `43a9450`.

### 0.2 Local tips

| Ref | Tip | Subject |
|---|---|---|
| local HEAD | `f3f6900` | `feat(math-check): the relational tier, checked in z3 against the locked facts` |
| `fix3b/pdf-retrieval` | `2e8200a` | `feat(retrieval): a fetched PDF is read by the upload path's extractor` |
| `fix3d/audit-drops-and-fresh-fk` | `9cadf86` (then `f674370`) | `feat(db): the seven FK declarations for fresh creation` / `feat(audit): a dropped audit row is counted, and no longer blocks the next write` |
| `docs/drift-3e-2026-09-19` | `8ff6614` | `docs(state,anti-claims): re-measure the auth posture, and correct what moved` |

All three match the brief's stated tips. Local HEAD `f3f6900` and box HEAD `5b36183` are
**divergent lineages** (neither an ancestor of the other); `5b36183` is not an ancestor of
`f3f6900`, and `f3f6900` is not an ancestor of `staging` (`5fb5be7`).

### 0.3 Did the merge land?

Authoritative check: `git log --oneline --all | grep "merge: union resolution of the shell fork"` on the box.

Raw output: **empty** — no commit with that subject exists anywhere on the box.

But the union resolution **is** the served content: `5b36183`'s message reads
> `feat(shell): the 401 fix on the box shell, union-merged with the local lineage`
> "Two files change; the other two of the four-file resolution were already this content, which is why they are absent here (index.html and shell.css are the box revisions, and the local lineage is a strict line-subset of both, so the deploy of each was a no-op by content)."

**Verdict: the merge landed as a SQUASHED single-parent commit (`5b36183`), not as a merge commit with the brief's exact subject.** The brief's specific check returns *no*; the substance it was testing for is *present*.

Worktree based on `5b36183` — its `prototype/shell.js` md5 is `27df966b6b7d35092d6971e827602929`, **byte-identical to the served asset**, and its `prototype/dev-server.py` is `58beb1f2295c5a00255b7110daed7dac`, also byte-identical. The worktree base IS staging's served state.

### 0.4 THE SANDBOX CHECK — **NO**

Query: `SELECT id FROM projects WHERE id = 'sandbox'` on `/home/ubuntu/assure-prototype/prompt_matrix/history.sqlite`.

Raw output:
```
tables: [... 'project_budgets', 'projects', ...]
SANDBOX_ROW: []
SANDBOX_EXISTS: False
ALL_PROJECTS: [('1c-det-copy-0f68e5-1eff24',), ('2c-fetch-api',), ... ('audit-scratch2-6ae069',), ...]
```

**Answer: NO.** The `sandbox` row is absent from `projects`, while `project_budgets` exists as a table. So the sandbox budget path is **already raising on staging** and **C3 FIRES**.

Confirmed the call chain on the box:
- `prompt_matrix/routers/sandbox.py:50` — `gov.budget_store.ensure_project(SANDBOX_PROJECT_ID)` is `run_sandbox_verify`'s **first executable act** after constructing the governor.

---

## SCOPING CHANGE (IRC from `Main`, mid-run) — B1 is NOT mine

`Main`'s message: **"SKIP B1 — THE MARKER IS ALREADY DONE"**. `FixB` committed `e3fdfae`
on `fixb/success-marker` (base `5b36183`), `shell.js` md5 `082693fa…` (pre-fix `27df966b…`),
reproducing the defect pre-fix and the fix at +20 ms/+2 s/+5 s, samples at
`docs/evidence/success-marker/b3-samples.txt`. **`shell.js` is mine for C2 only.**

**A1 is `FixA`'s, confirmed by `FixA` directly.** Asked over IRC before writing `draft.py`;
answer: *"YES — I own the intent handoff… Take A2 only… do not touch `_compile_system` /
`_INJECTION_DIRECTIVES` / `_DRAFT_SYSTEM` / `_draft_messages`, nor `answer_shape.py`'s new
`ask_directive()`."* `FixA` lands on `fixa/intent-handoff`; I rebase A2 onto it when it exists.

**My scope, therefore: A2, B2, C1, C2, C3.** Base rebased from `5b36183` to **`e3fdfae`**
(box HEAD + B1). Verified `prototype/shell.js` in the worktree = `082693fa…` = the box's
served asset.

### The merge (0.3) — resolved

`git log --oneline --all | grep "merge: union resolution of the shell fork"` on the box: **empty**.
But box HEAD `5b36183` message: *"feat(shell): the 401 fix on the box shell, union-merged with the
local lineage"* — **landed as a squashed single-parent commit, not a merge commit.** The brief's
exact grep returns no; the substance is present.

---

## A2 — the prompt version in the compile cache key (branch `postaudit/fixes`, base `e3fdfae`)

### The composition, quoted (`prompt_matrix/services/omp_memory.py:42-57`, pre-change)

```python
def compile_cache_key(
    project_id: str,
    source_text: str,
    target_ai: str = "",
    version: int = PIPELINE_VERSION,
) -> str:
    # Stable order: project_id | source_text | target_ai | str(version)
    digest = hashlib.sha256(
        "|".join(
            [
                str(project_id or ""),
                str(source_text or ""),
                str(target_ai or ""),
                str(version),
            ]
        ).encode("utf-8")
    ).hexdigest()[:8]
    pid = sanitize_omp_tag(project_id or "", max_len=32)
    return f"ast:{pid}:{digest}"
```

`PIPELINE_VERSION = 3` (`omp_memory.py:35`), and its comment states the version is deliberately
not bumped because *"bumping it would invalidate every entry"*.

**The prompt is in none of those four fields.** The audit measured it: an edit moved the
prompt's own sha256 (`c2b7926f -> 1cac8b13`) and left the key identical (`ast:p:de9116cd`).

### The change

`compile_cache_key` gains `prompt_version: int = 0`, appended to the digest **only when non-zero**
— so the unversioned composition stays byte-identical and an artifact keyed before this change
still matches it. `routers/draft.py` gains `PROMPT_VERSION = 1`, `prompt_fingerprint()`,
`_prompt_version_for()` and `_prompt_diverged()`; the key carries the version, each saved entry
records the fingerprint, and a hit whose recorded fingerprint differs is **not replayed**.

### The frozen-artifact carve-out (a finding the brief did not anticipate)

`demo-3235f5` is in `_FROZEN_PROJECTS_DEFAULT` (`draft.py:262`) and has **two warm `ast:` rows on
the box**: `ast:demo-3235f5:1e9c9516` and `ast:demo-3235f5:1784a441` (queried 2026-09-19). A
version in the key for *all* projects turns those into misses, and `frozen_cold_compile_blocked`
then refuses — **the ICP demo would render the frozen-refusal card instead of its document.**
So `_prompt_version_for` returns `0` for a frozen project: the pinned artifact composes the key it
always did. Same idiom as the file's existing memo-shape carve-out.

### Evidence (`postaudit/fixes`, base `e3fdfae`) — `verify_a2.py`

```
prompt_matrix: /tmp/wt-postaudit/prompt_matrix/__init__.py
draft: /tmp/wt-postaudit/prompt_matrix/routers/draft.py
omp_memory: /tmp/wt-postaudit/prompt_matrix/services/omp_memory.py
IMPORT-ROOT-OK

PIPELINE_VERSION=3  PROMPT_VERSION=1
OLD COMPOSITION (pre-A2)        : ast:postaudit-a2-proj:df60884f
NEW prompt_version=0            : ast:postaudit-a2-proj:df60884f
NEW prompt_version=1            : ast:postaudit-a2-proj:15ab65e0
IDENTICAL WHEN 0                : True
MOVES WHEN NON-ZERO             : True

PROMPT FINGERPRINT              : 37b4da61
_prompt_version_for('demo-3235f5') : 0
_prompt_version_for(non-frozen)    : 1
demo key with carve-out         : ast:demo-3235f5:f6335002
```

The live compile check (cold -> replay -> one-word prompt edit -> no replay) is the remaining
A2 evidence; its first attempt was **NOT RUN** (compile lock held by `FixA` for the full 300 s).

---

## B2 — the oversized source is refused, naming the cap

### The cap, quoted (`routers/draft.py:219-220`)

```python
SUBSTRATE_CONTEXT_CHARS_PER_FILE = 4000
SUBSTRATE_CONTEXT_CHARS_TOTAL = 16000
```

**Unit: characters.** `_build_substrate_context` does `excerpt = text[:SUBSTRATE_CONTEXT_CHARS_PER_FILE]`
(`:442`) — a positional cut, no page chosen by relevance. Audit measurements: 36,647 -> 4,039
(11.0 %), 58,862 -> 4,038 (6.9 %), 43,167 -> 4,035 (9.3 %), and the cut lands mid-word.

### The change

`_SOURCE_TOO_LONG_REASON`, `_source_too_long_message(limit)` and `_oversized_source(rows)` added
next to the other pre-flight refusals; the refusal fires in the **cold path only**, above the
budget preflight and above the model. Message, verbatim rendered:

> The source exceeds 4000 characters; the current pipeline cannot process it in one pass.
> Upload a shorter document, or split the source across multiple uploads.

It names the cap and the pipeline; it never says the source "may not cover the question".
Cold path only, because a replay builds no prompt and so has no prefix the refusal could be
about — and refusing a warm compile would refuse a document this pipeline already produced.

### Evidence (`postaudit/fixes`, base `e3fdfae`) — `verify_b2.py`

`_train_model` is replaced by a sentinel that records the call and raises, so "the model was
never reached" is proven, and no provider is contacted (no compile lock is taken — the harness
cannot make a model call even if the fix is wrong).

```
CAP (SUBSTRATE_CONTEXT_CHARS_PER_FILE) = 4000
oversized fixture: 34900 chars; normal fixture: 1843 chars
_oversized_source(oversized) = ('underwriting-policy.md', 34898)
_oversized_source(normal)    = None

OVERSIZED : {"cap": 4000, "draft_chars": 0, "events": ["error", "complete", "DONE"],
             "http_status": 422, "model_reached": false, "reason": "source_exceeds_context_cap",
             "refusal": "The source exceeds 4000 characters; the current pipeline cannot
             process it in one pass. Upload a shorter document, or split the source across
             multiple uploads.", "source_chars": 34900}
NORMAL    : {"http_status": null, "model_reached": true, "raised": "ModelReached",
             "source_chars": 1843}
```

The NORMAL case reaches the model, so the refusal is about length and not about the source.
(Line in the harness is `_stream_model`; `_train_model` above is a typo for it.)

---

## C3 — the sandbox foreign key (**fires**: 0.4 = NO)

### 0.4 answer

`SELECT id FROM projects WHERE id = 'sandbox'` on the migrated staging DB: **`[]` → NO**, the row
is absent. So the sandbox budget path is already raising on staging.

### The schema, quoted from staging (`sqlite_master` on the box)

```sql
CREATE TABLE "project_budgets" ( project_id TEXT PRIMARY KEY, token_limit INTEGER NOT NULL
DEFAULT 250000, tokens_used INTEGER NOT NULL DEFAULT 0, last_reset DATETIME DEFAULT
CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP , FOREIGN KEY (project_id)
REFERENCES projects(id) ON DELETE CASCADE)
```

`history.py:58` sets `PRAGMA foreign_keys=ON` per connection, so the FK is enforced.

### Reproduction on staging, quoted

```
STAGING ensure_project: RAISED IntegrityError: FOREIGN KEY constraint failed
ROUTE HTTP 500
ROUTE BODY {"error":"FOREIGN KEY constraint failed","ok":false}
```

(`POST /api/sandbox/verify` via `create_app(require_auth=False)` + test client on the box. The
failure is `sandbox.py:50`'s first act, so no model call is reached.)

### The fix

`routers/sandbox.ensure_sandbox_project()` seeds the `projects` row, called from `web.create_app`
right after `init_db()`. The fixed identifier stays in one place and the row exists before any
request can reach it.

### Evidence (`postaudit/fixes`, base `e3fdfae`) — `verify_c3_fixed.py`

A fresh DB is rebuilt with **staging's exact `project_budgets` DDL** so the local reproduction has
the same shape (a fresh `init_db` in this base has no FK — `Fix3D`'s seven declarations are on
`fix3d/audit-drops-and-fresh-fk`, not in this lineage).

```
BEFORE: ensure_project: RAISED IntegrityError: FOREIGN KEY constraint failed
BOOT:   projects row after boot: [('sandbox', 'Sandbox')]
AFTER:  ensure_project: RETURNED (no error)
        project_budgets row: [('sandbox', 250000, 0)]
ROUTE:  HTTP 200   (providers stubbed; a stub returning "" for the model id had produced a
        spurious 500 "model_id or model required" — the FK error is gone)
HEALTH: HTTP 200  ok: true  status: "ok"
```

**Note on the local harness:** the failing INSERT leaves an uncommitted write transaction on a
connection `get_db()` never closes, which then reads as `database is locked` for the next writer.
The BEFORE step therefore runs in a subprocess so the transaction dies with the process.

---

# FINAL STATE — branch `postaudit/fixes`, worktree `/tmp/wt-postaudit`

**Base of the branch: `7c8a422`** (= the box's HEAD content; the box was at `429fd09` at the last read).
**Box pins at the last read: HEAD `429fd09`, `git status --porcelain` empty,
`shell.js 7c30e8e6`, `index.html 4fff7756`, `about.html 10f837f7` — all clean and equal
to their committed blobs.**

Out-of-scope, never touched: `demo-3235f5` (no compile, no write), the entailment logic,
the Red-Hat prompt, the NLI replacement, the version hash, the demo project. Nothing pushed.

## Commits

| SHA | What | Files |
|---|---|---|
| `3999c38` | C1 — the three false About claims | `prototype/about.html`, `prototype/index.html` |
| `7e18dc8` | C3 — the sandbox project row at boot | `routers/sandbox.py`, `web.py` |
| `6f9074a` | A2 — prompt version in the compile cache key | `db/pipeline_cache`-adjacent: `services/omp_memory.py`, `routers/draft.py` |
| `5680948` | B2 — the oversized-source refusal | `routers/draft.py` |
| `9cf7c9b` | C1 — the dialog's counter name | `prototype/index.html` |
| `e4295f9` | C2 — ROUTED TO names the served model, and survives reload | `prototype/shell.js`, `routers/draft.py`, `db/project_files.py` |

Post-C2 md5s: `shell.js a3e68e68d57c22a92eefecd5bc867714`,
`routers/draft.py 8a9e348538919f47f40bc2911363581a`,
`db/project_files.py d3f6f6d223bdab5dbf7dce102a96c93f`.

## A2 — evidence (`postaudit/fixes`, base `e3fdfae` for the key check, `7c8a422` for the live check)

Key mechanics (`verify_a2.py`), interpreter proved to read the worktree (IMPORT-ROOT-OK):
old composition == `prompt_version=0` == `ast:postaudit-a2-proj:df60884f`;
`prompt_version=1` == `ast:postaudit-a2-proj:15ab65e0`; `_prompt_version_for('demo-3235f5') == 0`.

Live pipeline (`live_a2_stub.py`; the three provider touchpoints stubbed, so no provider and
no compile lock — stated because a compile without the lock is void):
```
KEY ast:postaudit-a2-live:003d76bb
RUN1 cold   : replayed=false, entry written=true, recorded fp=37b4da61
RUN2 replay : cache_hit=true, replayed=true, stub token calls=0
--- EDIT one word of the prompt ('clear' -> 'crisp') ---
KEY string after edit : ast:postaudit-a2-live:003d76bb   (UNCHANGED)
FINGERPRINT after edit: 3f9e0d73
[compile-prompt-divergence] recorded=37b4da61 live=3f9e0d73 prompt_version=1 — the
prompt changed without a PROMPT_VERSION bump; the entry is not replayed
_prompt_diverged : True
```
**The key string does NOT move on a bare prompt edit — that is the design, the version is the
key's knob. What stops the replay is the fingerprint, and it did (not replayed, warning logged).**
So the brief's "the key changes" is satisfied by the version bump; a bare edit is caught by the
hash. Both quoted above.

**A2's real-compile live path is NOT RUN** (compile lock held by `FixA` twice, 300 s each time,
`NOT RUN: lock not acquired within 300s`). What would settle it: the same probe with the lock held.

## B2 — evidence

Cap quoted: `SUBSTRATE_CONTEXT_CHARS_PER_FILE = 4000` — **characters**. Refusal verified with
`_stream_model` replaced by a sentinel that records and raises, so no provider is contacted:
oversized (34,900 chars) -> `["error","complete","DONE"]`, `http_status 422`,
`reason source_exceeds_context_cap`, `model_reached false`; normal (1,843 chars) -> sentinel
reached. Refusal text names 4000 and never blames the source.

## C1 — evidence

The **served** page carried all three claims uncorrected (`about.html 10f837f7`); the earlier
pass `0b633a4` (branch `fix/about-page-truth`, `about.html 1a26d444`) is not in this lineage and
not on the box. Took it verbatim, then extended the same corrections to the dialog in
`prototype/index.html`, which the earlier pass left (`:42`, `:49`). `"shifts meaning between
paragraphs"` greps 0 in both files. **Part A is identical in both files: extracted between
`<h2>What Assure is</h2>` and the `Production ships with your authentication…` line, whitespace
stripped, blanks dropped — 26 non-blank lines each.**

## C2 — evidence

**Does the response metadata contain the serving model? YES.** Real call under the lock
(`probe_c2_meta.py`, LOCK-ACQUIRED / LOCK-RELEASE rc=0):
`requested deepseek/deepseek-chat`; `chunk.model 'deepseek-chat'`;
`_hidden_params.custom_llm_provider 'deepseek'`;
`api_base 'https://api.deepseek.com/beta/chat/completions'`.

Layers (`verify_c2_layers.py`, real code, canned response, 2.3 s):
```
LAYER 1  measure.model 'deepseek/deepseek-chat'  measure.serving_model 'deepseek-chat'
         measure.provider 'deepseek'
LAYER 1b response names nothing -> serving_model degrades to the request, provider ''
LAYER 2  manifest.lastCompiledRoute {"model":"deepseek-chat","provider":"deepseek"}
LAYER 2b no serving_model recorded -> {"model":"deepseek/deepseek-chat","provider":""}
LAYER 2c no gate block            -> {"model":"","provider":""}
```
The whole-pipeline variant (`verify_c2_stubbed.py`) delivered only a partial run: the harness
accumulated dozens of duplicate connections on one SQLite WAL and spun at ~0 CPU. On its first
run it also selected the **lock-inference** `usage` frame (`task_type summarize_node`) rather
than the draft's — the frame selection is fixed, and that run is the NOT RUN item below.
**NOT RUN: the real end-to-end compile's `usage` frame and its persisted `gate.measure`.** What
would settle it: the fixed `verify_c2_stubbed.py` run to completion, or a real compile under the lock.
The `usage` frame's shape and the persistence line are the only C2 claims not backed by raw output.

## OPEN — B1.2 (assigned to me by `Main` after the collision ruling)

`FixB`'s `7c8a422`/`429fd09` landed the status wording; the **Evidence drawer rendering the
per-claim verdict** is the open piece. Not started. It writes `shell.js`, whose only writer is me.

## Named, not fixed (per the brief)

1. The same model drafting and judging — a self-assessment, not an independent check.
2. The version hash is not a fingerprint (`checked_at` timestamps).
3. Red-Hat is not on the compile path — a product decision.
4. The litellm `[10,80]s` clamp against `PEM_TIMEOUT_SECONDS=180` — investigate the clamp first.
5. Writers passing possibly-nonexistent project ids (`cost_governance.py:251-263`,
   `db/pipeline_cache.py:66`) — `FixA`/`RemarkableButterfly` Phase D territory.
6. **Found while working:** `_build_substrate_context`'s `SUBSTRATE_CONTEXT_CHARS_TOTAL` (16000)
   silently *drops* a source once the running total exceeds it (`break`, `draft.py:444`). Same
   defect class as B2 — answering from fewer sources and blaming the source — via a different
   constant. Not fixed: the brief named one cap and one message.
7. **Found while working:** `db/project_files.py` `_parse_compiled` returns a fresh
   `empty_manifest()` for an unparseable `last_compiled_json`, so a corrupt blob reads as "no
   document" rather than as an error. Silent by construction.

---

# BRANCH REBASE — `postaudit/fixes` onto `429fd09` (the box tip)

`git rebase --onto 429fd09 7c8a422 postaudit/fixes`, plus one restore commit.
Content is fully preserved: **`git diff ab31fdd b34ff97` is EMPTY.**

## The finding: a box tip is NOT a superset of the local lineage

`git diff --name-status 7c8a422 429fd09` returns exactly one line:

```
D	docs/evidence/success-marker/b3-samples.txt
```

**`429fd09` is missing a path `7c8a422` carries.** So advancing this branch to the
box tip was not a pure reconciliation — it dropped 101 lines of B1's success-marker
evidence, and it did so **silently**. The replay produced **no conflict**, because
`prototype/shell.js` is byte-identical on both tips
(`86ed7820b3dbed2721dc62459838918b8ef60ae2`), so nothing in the rebase output
mentioned the file. A conflict is the safe failure — it refuses to proceed. This was
the other kind.

This is the only reason it is known: the two tips were diffed **before** the replay
rather than read afterwards. `git diff ab31fdd <tip>` would have caught it after the
fact, but only if someone thought to run it on a rebase that reported success. The
restore commit `b34ff97` carries the same account.

**Carry forward:** an advance to a box tip can DELETE a local-only file with no
conflict and no warning, and a box tip is the one that serves production. Diff the
two tips before replaying, not after.

## The rebase, audited

| role | pre-rebase | post-rebase |
|---|---|---|
| base | `7c8a422` | `429fd09` |
| C1 (about) | `3999c38` | `36a97b7` |
| C3 (sandbox) | `7e18dc8` | `bf65b8c` |
| A2 (cache key) | `6f9074a` | `9128fe1` |
| B2 (cap) | `5680948` | `f91c12e` |
| C1 (counter) | `9cf7c9b` | `3682135` |
| C2 (route) | `e4295f9` | `29574be` |
| docs | `ab31fdd` | `652b30f` |
| restore | — | `b34ff97` (new tip) |

`git diff ab31fdd b34ff97` → **empty**. `prototype/shell.js` blob
`63fb9166ac73c656dd9dc02cafbb275628a8fa91` — my C2 change on top of the box's
`86ed7820b3db`. Nothing pushed; `fixa/*` untouched.

---

# A2 REVISED — the prompt's HASH is in the cache key (`e3f33c1`)

Made on the REBASED tip (the replay had already run when the instruction arrived; per
Main's race rule the commit goes on the rebased tip and says so — no second rebase).

Composition: `compile_cache_key(..., version=PIPELINE_VERSION, prompt_material="")`, and
`prompt:<PROMPT_VERSION>:<sha256(prompt)[:8]>` is appended when `prompt_material` is
non-empty. `_prompt_key_material(project_id)` supplies it; `""` for a frozen project.

A version integer alone closed nothing — it closes it only if someone remembers to bump
it. The hash closes it because the key changes *because the prompt changed*. This matters
most against A1, which changes the prompt with no version bump anywhere: a version-keyed
cache would replay a pre-A1 compile after A1 lands.

Raw, one word edited in `_COMPILE_SYSTEM` (`clear` -> `crisp`):

```
fingerprint  37b4da61 -> 3f9e0d73
KEY BEFORE   ast:postaudit-a2-keys:44d50a81
KEY AFTER    ast:postaudit-a2-keys:a5f20220
KEY CHANGED  True
restoring the word restores the key exactly (44d50a81)

load(key_before) HIT    <- a replay would happen
load(key_after)  MISS   <- so the edited prompt cannot replay; the run is cold
```

Frozen carve-out intact: `_prompt_key_material('demo-3235f5') == ''`, and an empty
material composes the **byte-identical** pre-A2 digest — both sides
`ast:demo-3235f5:ba554605` on the same literal text — so a warm row written before this
change still matches.

Removed with it: `_prompt_diverged` and its call site, and the payload
`prompt_fingerprint` record. With the hash IN the key a hit means the fingerprints already
matched, so the check could only return False.

**Probe error, named because it is the same failure mode as the earlier one today:** my
first frozen-key check reported `IDENTICAL when empty: False`. That was the probe
comparing against a hand-rebuilt source text with different inputs, not the code. The
invariant reads `True` once both sides see the same input.

**NOT RUN:** the whole-pipeline cold run under this change. The stub-pipeline harness died
twice on a SQLite connection/FD storm (~0 CPU, 119 open fds) and wrote nothing.

# THE UNMERGED `shell.js` DELTA — decided, and none of it is wanted

Merge base `a536a52`. Source (math-check) changed `shell.js` +810/-257; destination (box)
+949/-299. `git diff f3f6900 429fd09` is **+144/-47**, so the 144 insertions are the BOX's
and are already in this branch. The ~47 lines that exist **only** on the math-check lineage
are four things, each checked rather than judged by size:

1. `"✓ Intent compiled · checks run in the pipeline"` — the **pre-B1** marker. Taking it
   regresses FixB's fix.
2. `renderEvidenceFooter(ev)` + `performGrounding(nodeId)` — `shell.js:5601` says verbatim
   `// renderEvidenceFooter and performGrounding are gone with the /ground path.` and
   `docs/runbooks/ground-path-removal.md:5-6` confirms the route and its engine were
   deleted. `performGrounding` POSTs to `/api/projects/<id>/nodes/<id>/ground`, which no
   longer exists (only `/tasks/ground` survives, a different route).
3. the `Evidence · no source matched` empty state — superseded by `_renderUnanchoredDrawer`
   (`shell.js:5253`, the 2B/2C work).
4. `var counts = {...}` and a comment block.

**Decision: take none of it.** At the eventual merge, resolve `shell.js` to the box's file.

---

# A2's payoff, measured across A1 — the evidence the failing test could not give

A1 landed on this branch's lineage (`fixa/intent-handoff` `24d1b54`, parent `8d43974`),
so the question A2 exists to answer became directly measurable: does a real prompt
change move the key with no version bump?

Same ask, same source, same model, two trees; `PYTHONPATH=<tree>` with the module path
printed to prove which tree was read. **`PROMPT_VERSION` is 1 on both — no bump.**

| | PRE-A1 (`8d43974`) | POST-A1 (`24d1b54`) |
|---|---|---|
| `PROMPT_VERSION` | 1 | 1 |
| `prompt_fingerprint()` | `37b4da61` | `31f999d6` |
| `_prompt_key_material` | `1:37b4da61` | `1:31f999d6` |
| KEY | `ast:postaudit-a1-check:12ec3d4c` | `ast:postaudit-a1-check:cfbc0b30` |
| `ask_is_data_phrase` | True | False |

The prompt genuinely changed (`ask_is_data_phrase` flipping True -> False IS A1's
content), the version did not move, and **the key moved anyway — the hash moved it.**
Under the version-only design these two trees produce the same key and A1 would replay
a pre-A1 draft under A1's prompt. This is the strongest evidence for the hash being key
material rather than a note beside the key, and it is a pure computation: no provider,
no lock, 4.4 s.

## And the failing test, fully separated

`tests/test_omp_memory.py::test_draft_pipeline_cache_hit_skips_claude` fails with
`422 compile refused (cache replay): zero_anchored_claims` — **and fails identically at
base `429fd09`**, before any A2 code exists. So the hash neither causes nor closes it.
The mechanism is visible in the capture: the row IS found (the log says *cache replay*),
then the replay branch re-runs `validate_compiled_draft` on the cached draft, which
returns `zero_anchored_claims` and refuses. **It is the gate re-run on replay, not the
key.** Main has recorded it as an unowned defect with its cause, to be taken as a design
question — whether the replay should re-run a gate that already ran when the row was
written, or the payload should carry what the gate needs.

---

# CORRECTION — the whole-pipeline cold run is GREEN, not NOT RUN

Reported NOT RUN twice because the results file had not been written when I checked.
The harness had not died; it took 363 s and completed. Raw, from
`verify_a2_hash.py` on the hash-carrying tip (three provider touchpoints stubbed, so no
provider and no compile lock — the harness cannot make a model call even if the change
is wrong):

```
prompt_fingerprint() : 37b4da61
KEY                  : ast:postaudit-a2-hash:8ec9aeab

RUN1 cold    : cache_hit=null, draft_calls=1, replayed=false
RUN2 replay  : cache_hit=true, draft_calls=0, replayed=true

--- one word of the prompt: 'clear' -> 'crisp' in _COMPILE_SYSTEM ---
prompt_fingerprint() AFTER : 3f9e0d73
KEY BEFORE : ast:postaudit-a2-hash:8ec9aeab
KEY AFTER  : ast:postaudit-a2-hash:095b44a9
KEY CHANGED: True

RUN3 after edit : cache_hit=null, draft_calls=1, replayed=false     <- COLD, not a replay
KEY once restored: ast:postaudit-a2-hash:8ec9aeab  (== key before: True)
```

This is the whole-pipeline form of the acceptance criterion: **the key changes on a
one-word edit and the run is cold.** `draft_calls` is the model-call counter — 1 on the
cold runs, **0 on the replay**, so the replay is a replay and the post-edit run is not.
Restoring the word restores the key, so the key is a pure function of the prompt.

The earlier NOT RUN stands only for the **real-provider** end-to-end run, which the
compile lock (held by another agent for the full window, twice) never allowed.

## The cache-replay defect — mechanism sharpened by FixA, after my note above

My entry recorded the cause as "the replay branch re-runs `validate_compiled_draft` and
refuses". FixA pinned WHERE the missing evidence comes from, measured at `24d1b54`, and it
is narrower and more useful than what I wrote:

- `tests/test_omp_memory.py:100-115` seeds `load_ast_cache` with
  `{"compiled": {...}, "verified": {"z3_status": "PASS", "redhat_count": 0, "gate_status": "pass"}}`
  — **no `provenance_stats`.**
- `draft.py:973-979` then calls
  `validate_compiled_draft(..., provenance=(cached.get("verified") or {}).get("provenance_stats") or {})`,
  so provenance is `{}`, `anchored == 0`, and the refusal is `zero_anchored_claims` about the
  literal draft `"cached draft"`.
- **The real writer does not produce such a row:** `draft.py:1444-1448` stores
  `{"compiled": compiled_payload, "verified": verified_payload}`, where `verified_payload` is
  `build_audit_summary(...)`'s output and carries `provenance_stats`. A row written by this
  pipeline carries the evidence and is not refused. The test's hand-built payload predates the
  gate's provenance argument.

So the defect is **the reason text, not the refusal**: `zero_anchored_claims` asserts "no
paragraph anchored" about a document no gate ever measured on this replay. FixA names the two
honest fixes — skip the grounding re-check when the row carries no evidence, or give the absent
evidence its own reason (`cached_provenance_missing`), with making `provenance_stats` a required
field of the cached payload the smaller one. Unowned by Main's choice; not patched.

And A1 is excluded as a cause from two directions: its line in that call
(`instruction=normalized_ask(intent)`) only removes the user's own ask from the echo scan and
cannot yield `zero_anchored_claims`, and I reproduced the failure at `429fd09`, where A1 is absent.
