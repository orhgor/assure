# Assure (PEM engine)

Local app: ask once, get one verified answer. Default UI: `http://127.0.0.1:8765`. The compiler underneath is still PEM (`prompt_matrix`).

```bash
assure --web
assure --web --edition pro
```

Use `assure` for normal usage. `pem` is the same entry point.

Free edition (default) caps Combine at two models and 10 Sends per UTC day. Set `ASSURE_EDITION=pro` (or `team` / `self-hosted`) or pass `--edition`. Pro is $5 per month on the Pricing page in this tree. `pip install prompt-matrix` is not on PyPI yet.

## What you get

- Write a question. Assure rewrites it for Gemini, DeepSeek, Claude, Kimi, or a runner closed to the internet.
- Compare & Validate sends the same question to more than one model and shows overlap.
- After Send, the answer panel shows draft overlap and citation claims stripped from your files.
- Thumbs on an answer update a local score. The next format pick uses that score.
- Copy stays on this computer. A Send goes only to the provider you chose.

## Install

Python 3.10 or newer. On many Macs `python3` is still 3.9; use `python3.13` if that is what you have.

From the repo root:

```bash
cd prompt_matrix
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e ..
```

On Linux, clipboard copy needs `xclip` or `xsel`. macOS and Windows work with `pyperclip` as installed.

## Run

With the project venv active:

```bash
pem
pem --web --host 0.0.0.0 --auth-user admin --auth-pass PASS
```

`pem` starts the server and prints `http://127.0.0.1:8765`. `--host 0.0.0.0` also serves it on your LAN. `--auth-user` / `--auth-pass` set HTTP Basic Auth (same as `PEM_HTTP_USER` / `PEM_HTTP_PASS`). Do not commit the password. Change it from the default before binding to a LAN address.

One-shot CLI:

```bash
pem --cli
pem claude research "Compare AWS vs GCP" --copy
pem cursor debug "the login form 500s" -c ./src/app.py --copy
pem gemini comparison "AWS vs GCP for a 4-person startup" --print
cat notes.md | pem claude research "Summarize this" --direct
pem --export mdc --class-id comparison
pem claude analysis "Check XML" --lint --print
PEM_ENABLE_HISTORY=1 pem claude analysis "Log this run" --direct
```

Development swarm (architect → developer → review → test → docs):

```bash
python -m prompt_matrix.swarm --task "Add a Was this helpful? section with thumbs up/down"
python -m prompt_matrix.swarm --task "Add feedback buttons" --context web.py --context static/index.html
python -m prompt_matrix.swarm --task "Add feedback buttons" --model developer=deepseek-chat --local
```

Logs go to `logs/swarm.log`. `--copy-only` compiles prompts without calling APIs.

Draft → critique → final, with a persona:

```bash
pem claude analysis "Why did p95 jump after deploy 184?" --workflow redhat --persona security --direct
pem debug "fix auth.py" --local --workflow redhat --persona code --direct
pem --local --workflow redhat --critic rule --direct
pem gemini analysis "Summarize the notes" --direct --store-prompts --max-tokens 4096 --timeout 60
pem gemini debug "the login 500s" --direct --cheap
```

`--local` prefers a local runner. Ollama is the personal-dev default and PEM will start `ollama serve` if the CLI is installed. It is not the production serving winner. If vLLM (`:8000`), SGLang (`:30000`), Llamafile (`:8080`), or LM Studio (`:1234`) is already up, PEM uses that OpenAI-compatible server instead. Weights (Qwen, DeepSeek, …) are independent of the runner. If nothing local is up, Send hops to Gemini/DeepSeek/Claude/Kimi.

Send a single compiled prompt to a provider API:

```bash
export ANTHROPIC_API_KEY=...
python cli.py claude analysis "Why did p95 latency jump after deploy 184?" --direct --save reply.json
```

`--save` writes JSON. With `--direct`, the tool asks Instructor (via LiteLLM) for a structured reply matching `StructuredAIResponse`.

Cursor has no public chat API. `--direct` on Cursor is rejected. Compile with `target_ai=cursor` and paste `/ask @workspace`, or use the PEM MCP tools from any Cursor agent after MCP is loaded.

## MCP (Cursor, Claude Desktop, Windsurf)

`pem mcp` speaks MCP over stdio. Tools:

- `pem_compile` — compile a task into a target dialect. No model call.
- `pem_combine` — two or more models draft, then one merge (Combine).
- `pem_critique_rewrite` — run attempt → persona critique → final rewrite.
- `pem_dialect_lint` — compile then statically check the dialect. No model call.
- `pem_export` — export a saved class as `cursorrules`, `mdc`, `fabric`, or `dspy`.

This machine's Cursor config is `~/.cursor/mcp.json`, so every Cursor chat can see PEM. `pem` is not on PATH; the config points at the venv. Reload MCP in Cursor Settings after changing the file. Keys stay in `prompt_matrix/.env`.

```json
{
  "mcpServers": {
    "pem": {
      "command": "/Users/og/Untitled/prompt_matrix/.venv/bin/python",
      "args": ["-u", "-m", "prompt_matrix", "mcp"],
      "cwd": "/Users/og/Untitled",
      "env": {
        "PYTHONPATH": "/Users/og/Untitled",
        "PYTHONUNBUFFERED": "1"
      }
    }
  }
}
```

Copy `prompt_matrix/mcp.example.json` if you need the same snippet elsewhere.

## What it does

1. You pick a target AI and an intent (`research`, `design`, `comparison`, `debug`, `analysis`). PEM does not assume a product or domain; the case is whatever you upload and ask about. The research 4-part layout (Thesis / Verified / Inferred / Open questions) applies only to the `research` intent.
2. `config.json` supplies that AI's Jinja2 template and the intent's default role plus output format.
3. `render_prompt()` fills `role`, `format`, `task`, and `context`.
4. If context or the task contains a real file path, glob, or `@file`, the file contents are injected.
5. Default action copies the result. `--direct` calls LiteLLM. Send and `--direct` fail closed if the dialect linter finds errors (unbalanced Claude XML, missing DeepSeek headings, missing Cursor `/ask`).
6. Workflow **Draft → critique → final** runs three calls: attempt, a selectable critic persona, then a rewrite. The UI shows a line diff and tiktoken input / output / total counts.
7. `--export` writes a static file for a saved class. No Fabric or DSPy SDK is installed. Opt-in SQLite history (`PEM_ENABLE_HISTORY=1` or `--history`) stores hashes, char counts, and tiktoken input / output / total, not full prompt bodies. `PEM_STORE_PROMPTS=1` adds a second table, `prompt_versions`, with the compiled prompt and final reply after a successful Send. That flag is off by default.

Target-specific rules live in `config.json`, not in Python `replace()` calls:

- Claude: `<role>`, `<instructions>`, `<thinking>` XML. No expert preamble.
- Gemini: anti-hallucination line. Standalone compile appends `config/system_prompt.py` (no live search; `[Verified from Context]` / `[Logical Inference]`).
- DeepSeek: `## System Prompt` / `## User Request`, plus a self-correction checklist.
- Kimi: Moonshot long-context style, bilingual, tables over skipped details.
- Local: Ollama for a personal loop (PEM can start it). vLLM / SGLang / Llamafile / LM Studio if those servers are already listening. Override model with `PEM_LOCAL_MODEL` or `PEM_OLLAMA_MODEL`.
- Cursor: `/ask @workspace`, code-only output.

Edit those strings to change behavior. `python cli.py --list` prints the loaded targets and intents.

## Critic personas

Used only by the red-hat workflow (`--persona` or the Compose dropdown):

| Id | What it optimizes |
| --- | --- |
| `redhat` | Unsupported claims, missing caveats, keep / revise / reject |
| `security` | Ambiguity, injection, edge cases, hallucination risk |
| `tokens` | Cut repetition, keep constraints |
| `schema` | Exact JSON / XML / regex shape, no preamble |
| `code` | Logic, bounds, first command or test to try |

`--critic rule` or `PEM_CRITIC_MODE=rule` skips the LLM critic and runs a local static check (conflicting roles, tone, word limits vs examples). No second model and no API key. Attempt and final still need a reachable model if Send is on.

Same-model critic is allowed only when both sides are Ollama.

## Classes and learning

The web UI has three tabs.

**Compose.** Generate as before. After a prompt is built, save it into a class (research, debug, or a name you type). Selecting a class on the left uses that class's role and output shape. A learned class can also replace the whole template.

**Learn structure.** Paste any prompt you already like. The tool extracts role, output format, and a Jinja template (`{{ task }}`, `{{ context }}`) without calling an API. Save that as a new class.

**Classes.** Browse saved prompts by class, copy them again, or delete custom classes. Built-in classes come from `config.json` intents.

Saved data lives in `prompt_matrix/library.json` on this machine.

## API keys for `--direct`

| Target   | Environment variable              |
|----------|-----------------------------------|
| Claude   | `ANTHROPIC_API_KEY`               |
| Gemini   | `GEMINI_API_KEY` or `GOOGLE_API_KEY` |
| DeepSeek | `DEEPSEEK_API_KEY`                |
| Kimi     | `MOONSHOT_API_KEY` or `KIMI_API_KEY` |
| Ollama / local | none. Ollama on `11434` (PEM can start it). vLLM `8000`, SGLang `30000`, Llamafile `8080`, LM Studio `1234` if already up. |

Override the model with `--model` or `PEM_MODEL`. `--cheap` / `PEM_COST_ROUTE=1` picks a cheaper live cloud model from `cost_router.py` (the USD table in that file is a static estimate, not a live vendor quote). `--model` wins over `--cheap`. Local Send is free in that table. For a local runner, `PEM_LOCAL_MODEL` or `PEM_OLLAMA_MODEL`. Send calls cap at `PEM_MAX_TOKENS` (default 4096) and `PEM_TIMEOUT_SECONDS` (default 60), then `cap_output_tokens` may tighten that by intent. The full red-hat Send loop also caps at `PEM_WORKFLOW_TIMEOUT` (default 120). Timeouts and context-window overflows return an error instead of hanging.

`GET /api/health` imports and smoke-tests Jinja2, pydantic, Flask, rich, pyperclip, instructor, LiteLLM, python-dotenv, tiktoken, and Flask-HTTPAuth. The rest of the web UI uses HTTP Basic Auth (`PEM_HTTP_USER` / `PEM_HTTP_PASS`, or `pem --web --auth-user` / `--auth-pass`). Change that password before `--host 0.0.0.0`.

## Layout

```
prompt_matrix/
  cli.py           interactive menu and argparse
  mcp_server.py    stdio MCP (compile, combine, critique-rewrite, lint, export)
  linter.py        pre-dispatch dialect checks
  history.py       opt-in SQLite run log
  cost_router.py   USD estimate table + --cheap live model pick
  token_counter.py tiktoken counts
  web.py           Flask UI
  web_ui.py        HTTP Basic Auth (all routes except /api/health)
  exporters.py     .cursorrules / .mdc / Fabric / DSPy text
  swarm.py         architect → developer → review → test → docs
  agents/          citation critique + hardcoded FINAL sections
  personas.py      critic templates
  local_runners.py Ollama / vLLM / SGLang / Llamafile / LM Studio probes
  route.py         live send order
  engine.py        load, render, clipboard, LiteLLM
  models.py        Pydantic schemas
  pem_runner.py    preflight: live search disabled, firewall inject
  workflow_cap.py  red-hat Send loop alarm (PEM_WORKFLOW_TIMEOUT)
  litellm_runner.py LiteLLM max_tokens / timeout wrapper
  token_counter.py tiktoken input / output / total after each run
  config.json      targets, intents, Jinja2 structures
  config/          standalone system instruction (no live search)
  mcp.example.json Cursor / Claude Desktop snippet
  requirements.txt
```

Call `load_matrix()`, `render_prompt()`, `execute()`, `run_workflow()`, and `run_swarm()` from other Python code without going through the CLI.

