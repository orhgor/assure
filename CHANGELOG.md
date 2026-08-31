# Changelog

All notable product changes in this tree. Dates are calendar dates from the swarm log. No invented stats.

## Unreleased

- `pem eval --dataset` batch evaluation (compile-only by default; `--direct` Sends).
- `--ci` JSON for GitHub Actions. `--redteam` local injection and PII-shaped checks.
- Class version save / restore / diff. `GET /api/prompts` and `GET /api/prompts/<id>`.
- `pem monitor` usage table from `history.sqlite`. Latency is not stored.

## 0.1.0 — 2026-08-31

Not published to PyPI yet. Package name will be `prompt-matrix`. Pro is $5 per month on the Pricing page in this tree.

### Added

- Compose UI with Copy and Send. Compare & Validate, Refine & Verify, Quick Answer.
- Trust strip after Send: draft overlap and citation claims stripped from attached files.
- Thumbs on an answer (`👍 Yes` / `👎 No`) update `prompt_variations.performance_score` on this machine. The bandit picks the next format from those scores.
- Free tier locks on Pro-only critic personas and full history export, with `upgrade.unlock` tooltip.
- Cmd/Ctrl+Enter sends from the question field.
- History export on Pro: Markdown, HTML, Prompty, and a simple PDF. Free copies plain text.
- Landing trust row: local-first, you pick the provider, no vendor lock-in.
- Seven UI languages: en, es, zh, fr, de, ja, tr.

### Fixed

- Trust scores now attach to a Send even when the bandit loop is off, so the answer panel can still show overlap and stripped claims.
- Audit page export note points at `GET /api/history/<id>/export`.

### Notes

- Copy stays on this computer. A Send goes only to the provider you connected.
- `pip install prompt-matrix` is not on PyPI yet. Install from this repo with `pip install -e .`
