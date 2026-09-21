# Audit: Landing — Deterministic Truth Engine repositioning

| Field | Value |
|---|---|
| Date | 2026-09-08 |
| PR | local (deploy paused per deploy-flow) |
| Branch | `fix/staging-deploy-resilience` |
| Surfaces | Stack A — `prompt_matrix/templates/landing.html`, `i18n.py`, `landing.css`, `landing_footer.html` |
| Auditor | agent |
| Standard | `docs/WEBPAGE_AUDIT_STANDARD.md` |
| Preflight | Claims aligned to shipped backend (Z3, Red-Hat/DeepSeek-R1, Substrate Vault, Reporter agent, 3-tab proof) |

## Summary

Repositioned live Flask landing from **The Intellectual Compiler** to **The Deterministic Truth Engine**. Hero, three proof tabs (Coverage Counsel, Public Adjusters, Compliance Officers), why-now, four trust cards, and footer engineer tagline updated in EN plus ES/ZH/FR/DE/JA/TR. Removed legal overclaims and fourth-tab hallucination. Cache bumped (`LANDING_CSS=54`, `LANDING_JS=42`).

## Gates

| Gate | Result | Notes |
|------|--------|-------|
| Composition | Pass | 3 hardcoded role tabs preserved; 4-card trust grid |
| Type & orphans | Pass | i18n keys on all visible strings |
| Empty space / rhythm | Pass | Why section simplified to title + body |
| Alignment / shell | Pass | Existing enterprise landing shell |
| Color / contrast | Pass | No token changes |
| Responsive | Pass | 2×2 trust grid below 1100px; 4-col at wide |
| Content honesty / assets | Pass | Substrate Vault (not Grounding Vault); no federal-judge doctrine claims |
| Forms / analytics | Pass | Waitlist unchanged |
| Cache bust | Pass | `ui_cache.py` + template defaults |

## Failures found → fixes

| Issue | Fix |
|---|---|
| Fourth "Researchers" tab would break UI | Compliance Officers in tab3 |
| "Grounding Vault" marketing drift | Substrate Vault in copy |
| Legal doctrine overclaims | Removed from trust/proof copy |
| 3-card grid for 4 trust cards | `.trust-arch-grid-four` CSS |
| Stale hero test strings | `test_credit_wallet.py` updated |

## Viewports checked

- [ ] 390 (deferred — copy-only change)
- [ ] 768 (deferred)
- [ ] 1080 (deferred)
- [ ] 1440 (deferred)

## Residual risk

Static `landing/` draft site (Stack B) not synced in this pass. Production deploy blocked until GitHub/EC2 path clears per deploy-flow rule.
