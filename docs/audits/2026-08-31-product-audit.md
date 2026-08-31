# Audit: Product revision table

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Surfaces | `landing/audit.html`, `landing/index.html` nav, `landing/404.html` |
| Auditor | agent |
| Standard | `docs/WEBPAGE_AUDIT_STANDARD.md` §A |
| Preflight | Statuses from `prompt_matrix/` templates, `i18n.py`, `bandit.py`, `web.py`. Not from the pasted mock counts. |

## Summary

Public tracker at `/audit.html` uses the same chrome as the landing. Twelve named revisions. All twelve are already in the tree. The mock’s 6 completed / 4 in progress / 2 planned was stale.

## Counts (from this tree)

| Label | Count |
|---|---|
| Revisions | 12 |
| Completed | 12 |
| In progress | 0 |
| Planned | 0 |

## Rows

| Feature | Priority | Status | Evidence |
|---|---|---|---|
| Feedback buttons | P0 | Done | `templates/index.html` `#feedback-yes` / `#feedback-no`, aria-labels |
| Loading spinner | P0 | Done | `#run-busy`, `AssureUI.setControlBusy` |
| Upgrade prompts | P0 | Done | `#upgrade-banner`, link to `/pricing` |
| Privacy lock copy | P0 | Done | `footer.copy` in product; landing footer |
| Trust signals | P1 | Done | `#trust-strip`, overlap % and flagged citations |
| Locked Pro features | P1 | Done | `personas.list_personas(include_locked=True)`, `history.locked` |
| Keyboard shortcut | P1 | Done | form `keydown` Cmd/Ctrl+Enter |
| Seven languages | P0 | Done | `i18n.py` catalogs + Flask-Babel `.po` |
| Ratings on this machine | P2 | Done | `bandit.py` epsilon-greedy, scores in local SQLite |
| History export | P2 | Done | `POST /api/export` markdown / html / prompty / pdf |
| Landing page | P0 | Done | `landing/index.html`, IBM Plex, Pro $5 |
| Audit page | P0 | Done | `landing/audit.html` |

## Honesty vs the pasted mock

| Pasted | Shipped |
|---|---|
| Inter | IBM Plex |
| GitHub `promptmatrix/pem` | omitted (no verified remote in this pass) |
| 6 / 4 / 2 | 12 / 0 / 0 |
| Upgrade, privacy, locks, keys, export, trust as planned or in progress | Done in the tree |
| Emoji lock in footer | lock copy without emoji |
| `#` detail links | app origin or `/` / `audit.html` |

## Gates

| Gate | Result | Notes |
|------|--------|-------|
| Composition | Pass | No hero. Title, lead, four stats, one table. Stats are this page’s job. |
| Type & orphans | Pass | IBM Plex. Last words glued. |
| Alignment / shell | Pass | 1080px shell. 4-up stats at 768+. Table stacks under 768 (header row hidden). |
| Content honesty | Pass | Statuses checked against files this turn. |
| Cache bust | Pass | `site.css?v=6`, `site.js?v=6` |
| Forms / analytics | N/A | |

## Viewports

CSS holds 390 / 768 / 1080 / 1440. Not clicked in an automated browser this pass.
