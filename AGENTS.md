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