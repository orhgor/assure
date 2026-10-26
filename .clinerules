# Rules for AI agents (Cursor, Cline, Oh My Pi, Claude, any bot)
## Website vs workbench
- The getassureai.com website lives ONLY in marketing/. All website work (copy, i18n, templates, CSS, JS, build, Worker) happens in marketing/ only.
- Do NOT edit, move, rename, format or delete anything under prompt_matrix/ for website work. That includes prompt_matrix/templates/landing*.html, prompt_matrix/i18n.py, prompt_matrix/static/ and prompt_matrix/ui_cache.py. Those old marketing copies stay as they are on purpose.
- The workbench is built in a separate environment. Never link the website to the workbench or to app.getassureai.com.
- A PR that touches both marketing/ and prompt_matrix/ fails CI (isolation job). Do not try to get around it.
## Safety
- Never run marketing/deploy.sh, sync_r2.py or wrangler deploy (wrangler --dry-run only). Merging to main deploys production; only the owner merges.
- Never delete files without explicit owner approval.
- Never commit secrets, tokens or passwords.
