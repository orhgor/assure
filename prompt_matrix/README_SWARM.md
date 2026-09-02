# Assure development swarm

The swarm is an orchestration layer on top of PEM. It runs five agents through `run_workflow()` in `pipelines.py` so a natural-language feature request becomes a spec, code, review, tests, docs, and a quality report.

Assure is a local-first multi-model validation product. Tagline: "A trusted answer starts with the right question." The product loop is Restructure, Validate, Deliver. Compose workflows are Quick Answer (single), Compare & Validate (ensemble), and Refine & Verify (redhat). The audience is non-technical professionals.

Copy stays on this machine. Send goes to the provider you connected. If you need the local vs API distinction, use closed to the internet / open to the internet.

PEM itself stays domain-agnostic. The swarm passes your `--task` string and attached files into `run_workflow()`. It does not invent an Assure product case inside those prompts.

This tool is how you can later build remaining product work (feedback copy, spinner polish, i18n, the improve loop). Several of those files already exist in the tree. Do not treat this README as a claim that they are missing.

## Agents

Default models are pricing-table ids. `run_workflow` talks PEM targets (`gemini`, `deepseek`, `claude`, `kimi`, `ollama`). If Claude is down, the swarm falls back to the next live target. Gemini 1.5 ids are retired; the swarm Sends `gemini-3.5-flash` (architect) and `gemini-3.5-flash-lite` (tester).

| Role | Default model | Intent | Send id |
|---|---|---|---|
| Architect | gemini-1.5-pro | design | gemini/gemini-3.5-flash |
| Developer | deepseek-chat | debug | deepseek/deepseek-chat (output cap 16384) |
| Reviewer | claude-3-5-sonnet-20240620 | analysis | anthropic/claude-sonnet-4-5 |
| Tester | gemini-1.5-flash | debug | gemini/gemini-3.5-flash-lite |
| Documenter | claude-3-haiku-20240307 | research | anthropic/claude-3-haiku-20240307 |

Red-hat revisions use `workflow='redhat'` and persona `security`. Free edition clamps that persona to `redhat` inside `run_workflow`.

## Usage

Run from the repo root (`/Users/og/Untitled`), not from inside `prompt_matrix/`.

```bash
cd /Users/og/Untitled
source prompt_matrix/.venv/bin/activate
python -m prompt_matrix.swarm \
  --task "Add feedback buttons below each answer" \
  --context-files static/index.html web.py history.py \
  --pr
```

`--context PATH` is a repeatable alias for `--context-files`. `--create-pr` is an alias for `--pr`. `--max-iterations` caps red-hat rounds (default 3). `--min-confidence` is the merge gate used only when `--pr` tries `gh` (default 0.8). `--copy-only` compiles prompts and does not call provider APIs. `--skip-tests` and `--skip-docs` skip those phases.

## Cursor MCP

Call `swarm_start` (or `swarm_develop`) then poll `swarm_status` until `done` or `error`. Do not wait on one MCP call for architect through documenter — Cursor times that out (`-32001`).

`pem_apply_diff` lands a leftover unified diff. Cursor hides the tool name `apply_patch`; use `pem_apply_diff`. Reload the `pem` server after a code change. There is no `pem mcp swarm_develop` CLI. MCP tools are stdio-only (`pem mcp` or `python -m prompt_matrix.mcp_server`).

For a second repo, copy `prompt_matrix/mcp.example.json` and set `command`, `cwd`, and `PYTHONPATH` to that checkout. Keys load from `prompt_matrix/.env` (`GEMINI_API_KEY`, `DEEPSEEK_API_KEY`, `ANTHROPIC_API_KEY`, `MOONSHOT_API_KEY` or `KIMI_API_KEY`). Do not put key values in the JSON. Do not `alwaysAllow` this tool.

Example agent call:

```
tool: swarm_start
task: Add feedback buttons below each answer
context_files: ["prompt_matrix/templates/index.html", "prompt_matrix/web.py", "prompt_matrix/history.py"]
edition: team
apply_workspace: true

tool: swarm_status
```

CLI remains:

```bash
python -m prompt_matrix.swarm --task "..." --context-files ...
```

The MCP result starts with a short header (verdict, self-test, file sizes) then the quality report. Truncated dumps are continued or dropped. Apply complete diffs from the report, `logs/swarm.patch`, or MCP `pem_apply_diff`. Do not paste a truncated dump into `index.html`. If tests were skipped, the header says `self-test: skipped` and must not be read as a pass.

From Python:

```python
from prompt_matrix.swarm import run_swarm

result = run_swarm(
    task="Add a 'Was this helpful?' feedback section with thumbs up/down buttons.",
    context_files=["static/index.html", "web.py", "history.py"],
    create_pr=False,
)
print(result.quality_report)
```

`task_description` is an alias for `task`. `summary` is the same text as `quality_report`.

## Pipeline

1. Architect writes a spec (design) and ends with `## Files to write`.
2. Developer writes **one file per completion** (debug). `max_tokens` for that role is 16384 so HTML dumps are not cut at the debug-intent 512 cap. If a reply is truncated (unclosed fence, `finish_reason=length`, cut HTML), the swarm asks the developer to continue before treating the dump as complete. Truncated HTML is never copied into templates.
3. Multi-file tasks spawn sequential per-path developer subtasks inside `run_swarm` and merge into `logs/swarm.patch`.
4. Reviewer ends with Keep, Revise, or Reject and a confidence number (analysis).
5. If the verdict is not Keep, a red-hat rewrite runs, up to 3 times. After 3 Rejects, tests and docs still run. The report must not say the review was approved.
6. Phase 3 runs two lanes at once (`asyncio.to_thread`, because `run_workflow` is blocking). One lane is tester then unittest. The other is documenter. `--skip-tests` and `--skip-docs` skip those agents.
7. Self-test runs `python -m unittest discover -s tests` on extracted test files in a temp directory. It does not overwrite the live tree. On failure the developer gets the unittest output, up to 3 extra fixes. Those unittest rounds overlap with the documenter when both lanes run.
8. Before the reviewer Send, local lint runs on the patched files: `python -m py_compile` on `.py`, parenthesis check on `.sql`, third-party imports vs `requirements.txt` / `pyproject.toml`, and patch integrity (planned paths present, diffs non-empty, no truncated dump). A lint failure is **Revise** with no reviewer model call. It does not run `pem --direct`, `pem eval`, `/api/render`, or live Supabase/Stripe.
9. Quality report and `logs/swarm.log` record the task, spec, code, diffs or new files, local lint, verdict, self-test, coverage (only if a TOTAL line was printed), and overall confidence.
10. `--pr` writes `logs/swarm.patch`. If overall confidence is at least `--min-confidence` and lint passed, the swarm may try `gh pr create`. It does not `git add`, commit, push, or change git config. If `gh` is missing or there is no branch with commits, you still have the patch. MCP `pem_apply_diff` applies that unified diff to the workspace without live APIs. It rejects path traversal and truncated dumps.

## Quality gates

- Local lint must pass before Keep. Syntax, SQL parens, undeclared imports, and empty/truncated patches force Revise. No live Sends.
- Unittest must pass, or the report says it failed after 3 fix loops, or that tests were skipped.
- Reviewer Keep (via red-hat) is approval. Reject after 3 is a failed gate.
- Docs always run.
- Overall confidence is 0 to 1. Formula: Keep contributes 0.3 plus 0.1 times reviewer confidence; a passing self-test contributes 0.4; a size heuristic on extracted files contributes up to 0.2. The score is not raised to 0.8. 0.8 is the merge gate for attempting `gh`, not a target to fake.

Coverage percentages are included only when a coverage TOTAL line was printed. Otherwise the report says: Data not available in current context.

## Output

- `result.quality_report` / `result.summary`
- `result.files` (also `result.code`): filename to content
- `result.diffs`: unified diffs, including new files from `/dev/null`
- `result.review_verdict`, `result.review_confidence`
- `result.test_results`
- `result.confidence` / `result.confidence_score`
- `result.patch_path` when a patch was written
- `logs/swarm.log` for the full step log

## Acceptance checklist

- `python -m prompt_matrix.swarm --task "..."` runs without a traceback when keys and a live target exist, or exits 1 with a `run_workflow` error.
- `--context-files` and `--pr` are accepted.
- Final code is in `result.files` and as diffs or new-file content in the report.
- Tester output and documenter output are in the report.
- Red-hat stops at 3 rewrites.
- Self-test failure retries the developer at most 3 times.
- All step outputs are appended to `logs/swarm.log`.
- `--pr` writes a patch. It does not promise a GitHub PR.
- Developer output cap is at least 16384 tokens. Architect lists `## Files to write`; the developer writes one path per completion.
- Truncated dumps continue (up to 4) or are dropped. MCP `pem_apply_diff` rejects traversal and cut diffs.
- Local lint (`py_compile`, SQL parens, imports, patch integrity) runs before the reviewer Send. Lint failure is Revise and does not call the reviewer model.
