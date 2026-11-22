# Brief — Content/copy revision on assureproof.com (for OMP)

You are revising marketing copy (hero, section copy, page copy). This is
exactly the job class where the 2026-11-21 OMP port failed. Before touching
anything, read `AGENTS.md`, `marketing/README.md`, and
`docs/omp-port-postmortem.md`. The rules below are the contract — violating
any one of them voids the work.

## Task

<!-- Fill in here: what to revise — pages/sections, tone, claims to add or
remove, and any constraints. Do not start until this section is filled. -->

## Ground rules (non-negotiable)

1. Branch off `origin/main`: `content/<short-slug>`. Never push to `main`.
   Never force-push.
2. Open a PR → CI (`website-checks`) gates it → the owner merges. Only the
   owner merges.
3. Never run `marketing/deploy.sh` by hand — CI deploys (`--dry-run` is OK).

## Where copy lives

- Page structure and inline copy: `marketing/templates/*.html`
  (`landing.html`, `about.html`, `pricing.html`, `solutions.html`,
  `use_case_*.html`, `terms.html`, `privacy.html`, ...)
- Shared/i18n strings: `marketing/locales/*.json` (`en.json` is the source
  of truth; the other 7 are translations)
- Enterprise privacy strings: `marketing/privacy_enterprise.py`
- i18n machinery: `marketing/i18n.py`; rendering: `marketing/render.py`
  and `marketing/app.py`

## The locale rule (this is what killed the site last time)

- `en.json` is authoritative for keys. If you change an English anchor:
  update `en.json` for that key, then update the 7 locale files **only for
  those keys**, either deliberately or by flagging them as needing
  translation (see `marketing/scripts/fill_missing.py`).
- Never regenerate, reformat, re-serialize, or "normalize" a whole locale
  file. Never let a tool rewrite the JSON wholesale.
- Never overwrite a translation with the English value. Never drop a valid
  translation for a key you did not intend to touch.
- Check: `git diff --stat` must show locale-file churn only for the keys you
  actually changed. Anything more = stop and revert those files.

## Verify before you push (all three)

1. `bash marketing/build.sh` (the sole producer of `dist/`)
2. `pytest tests/ -v` (the deploy gate runs exactly this suite)
3. Re-read the full diff: `git diff origin/main...HEAD` — every hunk
   intentional, locale churn confined to your keys, zero formatting noise?

## Acceptance

- PR description lists each copy change and which keys/templates it touched.
- Diff confined to: the templates in scope, `marketing/locales/*` (changed
  keys only), and — deliberately — `tests/test_marketing_revision_copy.py`
  if it asserts the old copy. Never delete or weaken the tests.
- If `landing.css` changed: bump `LANDING_CSS` in
  `marketing/cache_versions.py`. New static asset: add it to the rsync
  include list in `marketing/build.sh`.
- Content rules: brand is "Assure", never "Assure AI"; a person decides,
  never the product; no banned claims (pilot/beta/trial/free/guaranteed/...);
  no secrets committed.

## When unsure

Stop and ask in the PR description instead of guessing. An unanswered
question is recoverable; a silently rewritten locale file is not.
