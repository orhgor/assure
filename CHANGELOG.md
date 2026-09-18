# Changelog

All notable product changes in this tree. Dates are calendar dates from the swarm log. No invented stats.

## Unreleased

- 2026-09-18 shell (`be53244`, `824b923`, `c7c1c00` on `prototype/shell-skeleton`): a compile with no source attached is refused before its first stage (`no_source_attached`, "Upload a source first. Assure grounds every claim against the source you provide.") instead of drafting first — measured before the fix, 139 token frames and 3755 characters reached the document column over 11.1 s, then the provenance refusal fired at 11.7 s; after it, the refusal is 3 frames in 0.017 s with no model call and nothing persisted. The dock no longer offers that run (Submit disabled with "Add a source to enable the compile"), and the empty column says the prerequisite. A compile that stops without a terminal frame (server death, parse error, dropped connection) now clears the streamed draft for a halt card — "This compile stopped before the document was verified. Nothing was saved." — verified live by restarting the app unit mid-stream. In-band refusals keep the one refusal card that already replaced the draft (1.37 s from the last token to the card).

- 2026-09-04 production `1b21f8b`: Projects CRUD, unsaved-change confirms, Audit Manifest on the command deck (tooltip, modal, JSON download). UI `assure-64` / `assure-55`.
- 2026-09-04 local (uncommitted): canvas right-click menu — Edit, Revise, Re-prompt, Send for Revision — on existing `/inquire/stream`. UI `assure-65` / `assure-56`.
- Compiler routing: `anthropic/claude-sonnet-4-5`, `gemini/gemini-3.6-flash`; BYOK keys on the workbench draft path.
- P4 account wallet: Clerk TEXT user ids, Fernet settings sync, 100 signup credits, atomic `spend_credit` after a successful Send, `/account/usage`. Source-install without Clerk stays unlimited. No official `supabase` Python SDK.
- Swarm for Cursor: `swarm_start` + `swarm_status` so the full pipeline is not one MCP round-trip (that was `-32001` timeout). `pem_apply_diff` is the listed patch tool; Cursor hides `apply_patch`. MCP writes accepted files after lint. Context files resolve under the repo root. Per-role workflow deadline matches developer 180s Sends.
- Canonical public URL is https://getassureai.com/ (HTTPS 200 on 2026-09-01). Check outputs is live on that host. `www` did not resolve. Live HTML still lists canonical getassure.com until `webpage` is synced.
- P0 launch follow-up: Check outputs page live on the Worker (`webpage` `9d48c07`). Team Compare & Validate Send returned `run_hash` and quality scores. Flask restart; seven-language Compose first paint. `getassure.com` still pending Namecheap → Cloudflare NS.
- First-run usage: `assure --web` opens the browser. No password prompt on this computer unless you set one. LAN (`--host 0.0.0.0`) requires `--http-pass` and refuses the old default. Connect copy is “Choose who answers.”
- Pre-launch testing checklist: `docs/launch-checklist.md`. Public launch is no-go until `getassure.com` nameservers move. Check outputs is live on the Worker. Team Compare & Validate Send was probed.
- `prompt_matrix/PEM.md` current status: source-install workbench, Worker Check outputs live, `getassure.com` pending NS, no public installer, Team Send probed, public launch no-go. App CSS `?v=assure-28`, landing `?v=20`.
- Blue Ocean follow-up after swarm `b648448f3a13c0e4` (Revise, truncated HTML): ICP kicker, Check outputs page, Pro stays $5 self-serve. No TAM stats. Cache `site.css?v=20`.
- Landing download buttons stay off until `data-download-macos` (and Windows / Linux) have a real file URL. There is no public installer. Cache `site.css?v=19`.
- PEM MCP tool `swarm_develop` on the existing stdio server. Calls `run_swarm()`. Cursor: Settings → MCP, reload `pem`. No `pem mcp swarm_develop` CLI. Apply complete diffs; HTML dumps have truncated.
- Landing visual pass: accent `#FF6B35` on primary CTAs, light hero wash, orchestration SVG, industry tiles (not customer logos), scroll fade and a short typed last hero line. Trust blue stays for headers and kickers. Cache `site.css?v=18`.
- Landing and Compose now lead with the compiled prompt. The reply is a first pass. Hero: you ask vaguely, Assure writes the prompt for the AI you chose, then checks the answer. FAQ: not a chatbot. Research intent copy matches. Send button stays Get my answer / Yanıtla. Did not claim a perfect prompt.
- Landing ICPs: hero shows three personas (Sovereign Analyst, Privacy-First Researcher, Prompt Reluctant Professional). Who Assure is for lists six jobs with pain, why, and where they look. Copy does not claim Send stays on the machine. Compliance Officer is not a regulator product.
- Desktop packager: `scripts/build-desktop.sh` (PyInstaller). Landing lists macOS / Windows / Linux first. No public download URL yet. `pip install prompt-matrix` is still not on PyPI. No 2-minute claim.
- Cloudflare Worker `assure` (GitHub `webpage`) is an assets-only deploy. `wrangler.jsonc` uses `assets.directory`. First Git build failed as a Pages config. Terms now list user responsibility, prohibited uses, no warranty, and provider policies on the landing page and at `/terms`. No automated violence filter.
- User install: `./scripts/install.sh` (Windows `scripts\install.ps1`). Landing primary CTA is Quick start, not localhost. First `assure --web` opens Connect if no provider is pasted, prints HTTP Basic, and warns on `--host 0.0.0.0` with the default password. Webpage sync is `scripts/sync-webpage.sh`. Do not clone `orhgor/assure` for the app.
- Public site URL is getassureai.com. Terms of use on the landing page and at `/terms` in the app.
- ICP: privacy-conscious consultants, researchers, analysts (`landing/ICP.md`). Use-case pages and launch drafts. No fake customers.
- Landing hero: you ask vaguely, Assure writes the prompt, then checks the answer. Who Assure is for: six jobs.
- Public pricing page: Free, Pro $5, Team as unlimited Sends on this machine. Not shared workspaces. No Contact Sales.
- Compose tour step 1 is "Choose your AI." Example chip is a healthcare client. Intent ids unchanged.
- Compose example chips fill the question box (Compare AWS vs GCP, Summarize a paper, Write a marketing email). All seven languages.
- Quick start (Install → Run → Ask) in the root README and on the landing page.
- Landing use cases: six jobs (consultant, researcher, policy analyst, technical writer, marketing strategist, compliance officer).
- `.github/workflows/ci.yml` runs `python -m unittest`, `pem --ci` (compile only, no clipboard), and `pem eval`.
- First-visit 3-step Compose tour with dots. Shown once.
- Step 1 first screen is model pills. Closed/open, cheap, class, and Add another sit behind Advanced options.
- Intent labels are named benefit copy (Research, Design, Comparison, Debug, Analysis) in all seven languages.
- Send and get my answer is the default. Copy the prompt is the other radio. No offline wording.
- Empty answer panel shows agreement / disagreement / files. No invented stats.
- Free shows a locked Security critic card, extra models, and a Pro pricing CTA. Pro is $5/mo.
- History cards show an answer preview (Pro), model chips, a ready/copy/ground badge, and a time.
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
