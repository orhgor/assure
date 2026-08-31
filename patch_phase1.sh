#!/bin/bash
# Phase 1: tiktoken counts, opt-in full prompt storage, HTTP Basic Auth.
# Run from the repo root (this file's directory). Idempotent.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
PM="$ROOT/prompt_matrix"

if [[ ! -d "$PM" ]]; then
  echo "prompt_matrix/ not found next to this script" >&2
  exit 1
fi

# Copy token_counter.py
cat > "$PM/token_counter.py" << 'EOF'
"""Token counts via tiktoken. Replaces the chars/4 heuristic."""

from __future__ import annotations

from typing import Optional

import tiktoken


def count_tokens(text: str, model: Optional[str] = None) -> int:
    """
    Count tokens using tiktoken. Falls back to cl100k_base if model unknown.
    Replaces the chars/4 heuristic entirely.
    """
    try:
        if model:
            encoding = tiktoken.encoding_for_model(_model_name(model))
        else:
            encoding = tiktoken.get_encoding("cl100k_base")
    except (KeyError, ValueError):
        encoding = tiktoken.get_encoding("cl100k_base")
    return len(encoding.encode(text or ""))


def _model_name(model: str) -> str:
    raw = (model or "").strip()
    if "/" in raw:
        return raw.split("/", 1)[1]
    return raw
EOF

# Append history migration (skip if already present)
if grep -q "def migrate_to_full_storage" "$PM/history.py"; then
  echo "history.py already has migrate_to_full_storage"
else
  cat >> "$PM/history.py" << 'EOF'


def store_prompts_enabled() -> bool:
    return bool(os.environ.get("PEM_STORE_PROMPTS", "").strip())


def migrate_to_full_storage() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS prompt_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_hash TEXT NOT NULL,
                compiled_prompt TEXT,
                final_response TEXT,
                model TEXT,
                intent TEXT,
                workflow TEXT,
                input_tokens INTEGER,
                output_tokens INTEGER,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(run_hash)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def store_full_run(
    run_hash: str,
    compiled_prompt: str,
    final_response: str,
    model: str,
    intent: str,
    workflow: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    if not store_prompts_enabled():
        return
    migrate_to_full_storage()
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO prompt_versions
            (run_hash, compiled_prompt, final_response, model, intent, workflow, input_tokens, output_tokens)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_hash,
                compiled_prompt,
                final_response,
                model,
                intent,
                workflow,
                input_tokens,
                output_tokens,
            ),
        )
        conn.commit()
    finally:
        conn.close()
EOF
fi

# Patch web_ui.py for auth
cat > "$PM/web_ui.py" << 'EOF'
"""HTTP Basic Auth for the PEM Flask UI.

Credentials: PEM_HTTP_USER / PEM_HTTP_PASS in prompt_matrix/.env, or --auth-user / --auth-pass.
Defaults are admin / changeme. Change them before binding to a LAN address.
"""

from __future__ import annotations

import os

from flask import Flask, request
from flask_httpauth import HTTPBasicAuth
from werkzeug.security import check_password_hash, generate_password_hash

auth = HTTPBasicAuth()

_users: dict[str, str] = {}

PUBLIC_PATHS = frozenset({"/api/health"})


def refresh_http_users(*, user: str | None = None, password: str | None = None) -> None:
    if user:
        os.environ["PEM_HTTP_USER"] = user
    if password:
        os.environ["PEM_HTTP_PASS"] = password
    name = os.getenv("PEM_HTTP_USER", "admin")
    secret = os.getenv("PEM_HTTP_PASS", "changeme")
    global _users
    _users = {name: generate_password_hash(secret)}


@auth.verify_password
def verify_password(username: str, password: str) -> str | None:
    hashed = _users.get(username)
    if hashed and check_password_hash(hashed, password):
        return username
    return None


def protect_app(app: Flask) -> None:
    """Require Basic Auth on every route except GET /api/health."""
    refresh_http_users()

    @app.before_request
    def _require_login():
        if request.path in PUBLIC_PATHS:
            return None
        return auth.login_required(lambda: None)()
EOF

# Wire create_app, run_workflow, and package deps if a clean tree is missing them.
python3 - "$PM" "$ROOT" << 'PY'
from pathlib import Path
import sys

pm = Path(sys.argv[1])
root = Path(sys.argv[2])

web = (pm / "web.py").read_text()
if "protect_app" not in web:
    needle = '    app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")\n'
    insert = needle + """    try:
        from .web_ui import protect_app
    except ImportError:
        from web_ui import protect_app
    protect_app(app)
"""
    if needle not in web:
        raise SystemExit("web.py: could not find Flask() line to wrap with protect_app")
    (pm / "web.py").write_text(web.replace(needle, insert, 1))

pipe = (pm / "pipelines.py").read_text()
old_hist = "from .history import history_enabled, record_run"
new_hist = "from .history import history_enabled, record_run, run_hash, store_full_run"
if "store_full_run" not in pipe:
    if old_hist not in pipe:
        raise SystemExit("pipelines.py: history import not found")
    pipe = pipe.replace(old_hist, new_hist, 1)
    pipe = pipe.replace(
        "from history import history_enabled, record_run",
        "from history import history_enabled, record_run, run_hash, store_full_run",
        1,
    )
    marker = "            total_tokens=total_tokens,\n        )\n    return result\n"
    block = """            total_tokens=total_tokens,
        )
    if direct and result.reply:
        store_full_run(
            run_hash=run_hash(compiled_prompt),
            compiled_prompt=compiled_prompt,
            final_response=final_text,
            model=str(target_model),
            intent=result.intent,
            workflow=result.workflow,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    return result
"""
    if "count_tokens" not in pipe:
        count_block = """    try:
        from .token_counter import count_tokens
    except ImportError:
        from token_counter import count_tokens
    compiled_prompt = result.prompt or ""
    final_text = result.reply or ""
    target_model = _resolve_model(result.target_ai, load_matrix()) or result.target_ai
    input_tokens = count_tokens(compiled_prompt, model=target_model)
    output_tokens = count_tokens(final_text, model=target_model)
    total_tokens = input_tokens + output_tokens
    result.input_tokens = input_tokens
    result.output_tokens = output_tokens
    result.total_tokens = total_tokens
    result.tokens = {
        "input": input_tokens,
        "output": output_tokens,
        "total": total_tokens,
    }
    if history_enabled(history):
"""
        if "if history_enabled(history):" not in pipe:
            raise SystemExit("pipelines.py: history_enabled block not found")
        pipe = pipe.replace("    if history_enabled(history):\n", count_block, 1)
    if marker not in pipe:
        raise SystemExit("pipelines.py: could not insert store_full_run before return")
    pipe = pipe.replace(marker, block, 1)
    (pm / "pipelines.py").write_text(pipe)

req = pm / "requirements.txt"
if req.exists():
    text = req.read_text()
    extra = []
    if "tiktoken" not in text:
        extra.append("tiktoken>=0.8.0")
    if "flask-httpauth" not in text:
        extra.append("flask-httpauth>=4.8.0")
    if extra:
        req.write_text(text.rstrip() + "\n" + "\n".join(extra) + "\n")

pyproject = root / "pyproject.toml"
if pyproject.exists():
    text = pyproject.read_text()
    for dep in ('"tiktoken>=0.8.0"', '"flask-httpauth>=4.8.0"'):
        if dep not in text:
            text = text.replace(
                '"python-dotenv>=1.0.0",\n',
                '"python-dotenv>=1.0.0",\n    ' + dep + ",\n",
                1,
            )
    pyproject.write_text(text)

print("phase 1 files written")
PY

chmod +x "$ROOT/patch_phase1.sh"
echo "ok: $ROOT/patch_phase1.sh"
