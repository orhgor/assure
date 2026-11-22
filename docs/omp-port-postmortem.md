# Post-mortem: the OMP port (2026-11-21 → 2026-11-22)

Written so this class of mistake is never made again — by OMP or any agent.
Read it before touching `marketing/`.

## Timeline (all verified from git history)

| Commit | Event |
| --- | --- |
| `9ac69ae` (11-21) | OMP force-pushed a full replacement of the line: its own design overhaul plus the entire omp app tree (`prompt_matrix/`, `prototype/`, `worker/`, `landing/`, `openuser/`, `supabase/`, `infra/`, packaging, docker files, app test suites). This erased the live premium site and the whole marketing deploy system (`deploy.sh`, `app.py`, `i18n.py`, `_headers`, worker config, `requirements.txt`, `make_favicon.py`). |
| `bd0b6dd` (11-21) | Fallout: the omp line's `tests/conftest.py` shipped a sqlite3 shim that crashed pytest at session start (INTERNALERROR, run 44906068570), taking down the deploy gate. The premium site tests had to be restored. |
| `6f16d2f` (11-21) | Recovery: stripped the repo back to the marketing site only; app code survives only in git history. |
| `9d856f6` (11-21) | Recovery: rebuilt `marketing/` and `.gitignore` to the merged PR state (`39a2973`) the omp force-push had destroyed; dropped omp-line extras (`icons.js`, `sentry.bundle.js`, government/privacy/why-not-llm templates, logos_band include). |
| `e112739` (11-22) | The actual content port (PR #146): single hero, new title, 6-FAQ, de-branded pipeline, terms update. Clean merge — but the locale JSONs were regenerated along the way (see mistake 2). |
| `3130fa7` → `9cc2fc8` (11-22) | Recovery: PR #147 restored the 146 translation values the port had silently wiped; merged to `main` and deployed. |

## The three mistakes

1. **Force-pushing a replacement instead of porting.** The omp force-push
   (`9ac69ae`) replaced the site and its deploy system wholesale and dragged
   an entire application tree into a marketing-only repository. Cost: two
   full recovery commits (`6f16d2f`, `9d856f6`), a CI shim cleanup
   (`bd0b6dd`), and backup branches just to keep the app code findable.

2. **Regenerating locale files instead of editing keys.** The port
   (`e112739`) rewrote `marketing/locales/*.json` wholesale. Result: 146
   correct translations were silently wiped across 7 locale files
   (es/de/ja/tr/zh/ar: 21 values each; fr: 20 of 21) for 21 keys whose
   **English anchors had not changed at all** (appendix). No test caught it,
   no diff was flagged, and the degraded site deployed. Cost: a forensic
   restore session (diff against the pre-port tree `6f16d2f`, byte-match
   verification 146/146) and PR #147.

3. **Leaving the repo shape destroyed.** After the omp era the repo carried
   93 extra branches on origin (cursor/*, backup/*, staging trees,
   near-duplicates of everything), and the GitHub default branch was left on
   a stale `staging` branch, so `origin/HEAD` pointed at a dead line. Cost:
   a full audit plus mass erase on 2026-11-22; the repo is single-line
   `main` again.

## Hard rules (enforced in AGENTS.md)

- `marketing/locales/*.json` are hand-curated translations, not generated
  output. Edit only the keys whose source copy you changed, in the same
  change. Never regenerate, reformat, re-serialize, or "normalize" a whole
  locale file.
- A diff inside a locale file must correspond exactly to the copy change you
  intended — key for key. If it shows anything else, stop and fix before
  pushing.
- Never overwrite a translation with the English value. Never delete a valid
  translation for a key you did not intend to touch. If a translation is
  missing, flag it — do not paper over it.
- Never force-push. Git history is the only recovery mechanism (it is how
  these 146 values came back).
- Keep the repo single-line: default branch `main`, work lands via PR, no
  backup or stale branches left behind.
- Before pushing: `bash marketing/build.sh && pytest tests/ -v` — the deploy
  gate runs exactly this.
- Never run `marketing/deploy.sh` by hand — CI deploys.

## Appendix: the 21 keys whose translations were wiped

English anchors unchanged by the port — the port never needed to touch these:

- `arch.title`, `flow.aria`, `landing.pilot.cta_primary`
- `landing.pilot.workflow.{select,fuel,gov,ins,invoice,other}`
- `privacy.enterprise.s{3,8,10}.title`
- `terms.{as_is,files,law,third}`
- `terms.prohibit.{deceive,hate,illegal,rights,violence}`

Per-locale loss: es/de/ja/tr/zh/ar lost all 21 values; fr lost 20 of 21
(one already matched). The restore (PR #147) matched every value
byte-for-byte against the pre-port tree (`6f16d2f`) before merging.
