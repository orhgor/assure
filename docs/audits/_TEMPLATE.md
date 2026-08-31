# Audit report template

Copy to `docs/audits/YYYY-MM-DD-<short-slug>.md` for every UI-facing change.

---

# Audit: \<feature or page name\>

| Field | Value |
|---|---|
| Date | YYYY-MM-DD |
| PR | # |
| Branch | `cursor/…` |
| Surfaces | e.g. `landing/`, `/` |
| Auditor | agent / human |
| Standard | `docs/WEBPAGE_AUDIT_STANDARD.md` |
| Preflight | n/a unless a claims/preflight doc exists |

## Summary

One or two sentences: what changed and whether craft held.

## Gates

| Gate | Result | Notes |
|------|--------|-------|
| Composition | | One job; hero budget; cards only if interactive |
| Type & orphans | | Intentional breaks / `&nbsp;`; viewports checked |
| Empty space / rhythm | | No dead bands; section padding matches neighbors |
| Alignment / shell | | Shell, measure, grids, form width |
| Color / contrast | | Tokens; AA for body-size text |
| Responsive | | Profile viewports (default 390 / 768 / 1080 / 1440) |
| Content honesty / assets | | Real assets; claims match preflight |
| Forms / analytics | | Self-hosted leads; events after success; no PII |
| Cache bust | | CSS/JS `?v=` bumped when visuals change |

## Failures found → fixes

| Issue | Fix commit / change |
|---|---|
| | |

## Viewports checked

- [ ] 390
- [ ] 768
- [ ] 1080
- [ ] 1440

## Residual risk

Anything not measured in browser, deferred a11y, or env-dependent.
