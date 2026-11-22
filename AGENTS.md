# Rules for AI agents (Cline, Cursor, Claude, any bot)

This repository is the **assureproof.com marketing site only**. The site lives
entirely in `marketing/` (see `marketing/README.md`).

- Deploy = merge to `main`. `.github/workflows/webpage-deploy.yml` runs the
  website checks, builds `dist/`, and deploys the Worker with `dist/` as
  static assets. **Only the owner merges.**
- Never run `marketing/deploy.sh` by hand — CI deploys. `--dry-run` is fine.
- The deploy is gated on `pytest tests/` passing (the `website-checks` job).
  Keep it green: `bash marketing/build.sh && pytest tests/ -v` before pushing.
- New static assets must be added to the rsync include list in
  `marketing/build.sh` (an asset missed there 404s in production).
- Bump `LANDING_CSS` in `marketing/cache_versions.py` when `landing.css` changes.
- Never commit secrets, tokens or passwords.
- Content rules: brand is "Assure", never "Assure AI"; a person decides, never
  the product; no banned claims (pilot/beta/trial/free/guaranteed/...).
- Locale files (`marketing/locales/*.json`) are hand-curated translations,
  not generated output. Edit only the keys whose source copy changed, in the
  same change; never regenerate, reformat or re-serialize a whole locale
  file. A locale diff must match the intended copy change key for key.
- Never overwrite a translation with English text, and never drop a valid
  translation for a key you did not intend to touch. If a translation is
  missing, flag it — do not paper over it. This cost 146 values across 7
  locales once (2026-11-22); see `docs/omp-port-postmortem.md`.
- Never force-push; git history is the only recovery mechanism.
- Keep the repo single-line: default branch is `main`, work lands via PR,
  and no backup or stale branches are left behind.
