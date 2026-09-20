# UI × UX matching audit

Audit of the surfaces in `prompt_matrix/` against the code behind them: what the
interface reads, sends and labels versus what the routes, services and catalogue
actually provide. Static analysis, no runtime.

Scope at time of audit: branch `staging-local`, deployed as `257c46a`.

## Summary

| Severity | Count | Findings |
|---|---:|---|
| breaks-user-visible | 0 | — |
| wrong-label | 0 | — |
| **dead-code** | **3** | health fields unread; two orphaned modules; `.bak` files tracked |
| cosmetic | 1 | landing cache-bust constants diverge |

The three breaks found earlier tonight are fixed and re-checked here: the
`data.verdict` field the endpoint never sent, the `verdictLabel` string called as
a function, and the hardcoded English in `verdictLabel`. Each now agrees across
the boundary.

## Findings

### D1 — every health field is written and none is displayed

```
FINDING:  /api/health exposes eight operational fields that no UI surface reads.
SURFACE:  prompt_matrix/static/*.js — zero files reference any of them
BACKEND:  prompt_matrix/routers/health.py, prompt_matrix/web.py (health route)
EVIDENCE: written in 1-6 Python modules each; 0 references across 40 JS files:
            audit_drops (3)          cache_drops (4)       db_open_connections (6)
            build_sha (4)            build_branch (1)      build_time (1)
            image_ref (1)            prompt_ready_summary (2)
SEVERITY: dead-code
```

These fields were built to be read: `build_sha` exists so a deploy can be
confirmed from outside the box, and the drop counters exist so a refused write is
a number rather than a line in a log. The values are correct — `/api/health`
returns them and the deploy gate asserts on them — but nothing a user or operator
looks at shows them. The data is reachable only by calling the endpoint directly.

This is the class of gap that made tonight's evidence-verdict bug invisible for
so long: the backend did the work and the surface never asked for it.

### D2 — `prompt_assembly.py` is present and unreferenced

```
FINDING:  619 lines carry a compile-prompt mechanism nothing calls.
SURFACE:  prompt_matrix/services/prompt_assembly.py
BACKEND:  routers/draft.py imports neither it nor its exports
EVIDENCE: zero importers across the tree (AST-verified, not grep alone)
SEVERITY: dead-code
```

Distinct from the wiring that was severed and restored: `evidence_assembly.py`
was reconnected (see `257c46a` and the verdict tests), whereas `prompt_assembly`
is an *alternative* to the live prompt mechanism and cannot be wired without
replacing `_COMPILE_INSTRUCTIONS`. Its features — source budget with truncation
markers, fingerprint, `compile_type` selection — are not in the live path.

Needs a decision, not a fix: adopt its mechanism into the live prompt, or remove
it. Leaving it invites exactly the misreading it caused twice tonight.

### D3 — backup files are tracked in git

```
FINDING:  Four editor/backup artifacts are committed, one of them 4019 lines.
SURFACE:  repository
BACKEND:  n/a
EVIDENCE: prompt_matrix/services/evidence_assembly.py.bak              (826 lines)
          prompt_matrix/cost_governance.py.20260917T210154Z.bak       (590 lines)
          prototype/shell.js.bak-20260918-111801                      (4019 lines)
          scripts/aws/_box.sh.bak
SEVERITY: dead-code
```

Swept in by `15dd0f4 chore: commit untracked files`. The `evidence_assembly.py.bak`
is particularly misleading: it sits beside the live module and differs from it,
so a reader cannot tell which is authoritative.

### C1 — landing cache-bust constants diverged

```
FINDING:  APP_CSS/APP_JS are assure-150 while LANDING_CSS is 60, from different lines
SURFACE:  prompt_matrix/ui_cache.py
BACKEND:  n/a (the file is the contract)
EVIDENCE: resolved as the higher of each pair during the branch merge
SEVERITY: cosmetic
```

Not a defect: the two lineages bumped different surfaces. Recorded so the next
person does not read the mixed numbering as a mistake.

## Clean areas — checked and consistent

- **i18n locale coverage.** All six locales (`EN`, `ES`, `ZH`, `FR`, `DE`, `TR`)
  carry every key the English catalogue defines: 0 missing in each. Verified as
  key *sets* per locale, which is the defect that matters.
- **Verdict keys.** `evidence.inspector.verdict.{supported,partial,not_supported,contradicted,unanchored,unverified}`
  exist in all six locales.
- **Endpoint reachability.** Every path fetched from JS resolves to a route.
  `/api/user/role` initially appeared missing; it is defined in `web.py:802,809`,
  outside `routers/`. No dead calls found.
- **Verdict shape agreement.** `/api/locks/<hash>/evidence` sends
  `{type, reason}`; `evidence_drawer.js` reads `verdict.type` and `verdict.reason`.
  Agreed.
- **Two evidence inspectors, no duplication.** `evidence_inspector.js` delegates
  to `AssureEvidenceDrawer` when the drawer is present (`evidence_inspector.js:32-36`),
  so it is a fallback for the legacy surface rather than a competing renderer.
- **i18n adoption in JS.** Seventeen files call `translate()`; the drawer was the
  outlier that hardcoded English and is now translated.

## Not checked

- **Runtime rendering.** This is static analysis. Whether a translated string
  overflows its container, or a verdict badge is legible at a given size, needs a
  browser; the existing snapshot suite is the tool and it fails in this
  environment for unrelated reasons (a pre-existing 0.7333 diff on
  `evidence-drawer.png` reproduced with the working tree stashed).
- **CSS coverage.** Class-by-class comparison of `style.css` against template and
  JS usage was not done — the file is large and this would need a purpose-built
  extractor rather than grep.
- **Accessibility.** `aria-*`, focus order and contrast were not audited.
- **Route response completeness.** Only the routes JS calls were checked. A route
  no surface calls, returning fields no consumer reads, would not appear here.
