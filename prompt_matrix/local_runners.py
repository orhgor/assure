"""Local LLM runners. Ollama is the personal-dev default; it is not the production winner.

vLLM, SGLang, and Llamafile are better for high concurrency or tight resources.
LM Studio is another OpenAI-compatible desktop server. Model quality is the
weights (Qwen, DeepSeek, …), not the runner. PEM talks to whichever process
is already listening, and will only auto-start Ollama.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from typing import Any
from urllib.request import urlopen

# Ollama first: one-command local loop for a person at a laptop.
# Then production-style OpenAI-compatible servers if they are already up.
RUNNERS: list[dict[str, Any]] = [
    {
        "id": "ollama",
        "kind": "ollama",
        "label": "Ollama",
        "fit": "local dev / personal",
        "probe": os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/") + "/api/tags",
        "api_base": os.environ.get("OLLAMA_HOST", os.environ.get("OLLAMA_API_BASE", "http://127.0.0.1:11434")),
        "startable": True,
    },
    {
        "id": "vllm",
        "kind": "openai",
        "label": "vLLM",
        "fit": "production / high concurrency",
        "probe": os.environ.get("PEM_VLLM_BASE", "http://127.0.0.1:8000/v1").rstrip("/") + "/models",
        "api_base": os.environ.get("PEM_VLLM_BASE", "http://127.0.0.1:8000/v1"),
        "startable": False,
    },
    {
        "id": "sglang",
        "kind": "openai",
        "label": "SGLang",
        "fit": "production / high concurrency",
        "probe": os.environ.get("PEM_SGLANG_BASE", "http://127.0.0.1:30000/v1").rstrip("/") + "/models",
        "api_base": os.environ.get("PEM_SGLANG_BASE", "http://127.0.0.1:30000/v1"),
        "startable": False,
    },
    {
        "id": "llamafile",
        "kind": "openai",
        "label": "Llamafile",
        "fit": "resource-constrained / single binary",
        "probe": os.environ.get("PEM_LLAMAFILE_BASE", "http://127.0.0.1:8080/v1").rstrip("/") + "/models",
        "api_base": os.environ.get("PEM_LLAMAFILE_BASE", "http://127.0.0.1:8080/v1"),
        "startable": False,
    },
    {
        "id": "lmstudio",
        "kind": "openai",
        "label": "LM Studio",
        "fit": "local desktop",
        "probe": os.environ.get("PEM_LMSTUDIO_BASE", "http://127.0.0.1:1234/v1").rstrip("/") + "/models",
        "api_base": os.environ.get("PEM_LMSTUDIO_BASE", "http://127.0.0.1:1234/v1"),
        "startable": False,
    },
]

_OLLAMA_STARTED = False
_ALIVE_CACHE: dict[str, Any] = {"t": 0.0, "rows": []}


def ollama_bin() -> str | None:
    return shutil.which("ollama")


def ollama_up() -> bool:
    runner = next(item for item in RUNNERS if item["id"] == "ollama")
    return _probe(runner) is not None


def ensure_ollama(wait_s: float = 8.0) -> dict[str, Any]:
    global _OLLAMA_STARTED
    if ollama_up():
        return {"ok": True, "up": True, "action": "already-running", "binary": ollama_bin()}
    binary = ollama_bin()
    if not binary:
        return {
            "ok": False,
            "up": False,
            "action": "missing",
            "binary": None,
            "note": "Ollama is not installed. If vLLM, SGLang, Llamafile, or LM Studio is already up, PEM uses that. Else cloud.",
        }
    if not _OLLAMA_STARTED:
        try:
            subprocess.Popen(
                [binary, "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            _OLLAMA_STARTED = True
        except OSError as exc:
            return {
                "ok": False,
                "up": False,
                "action": "spawn-failed",
                "binary": binary,
                "note": f"Could not start Ollama: {exc}",
            }
    deadline = time.time() + wait_s
    while time.time() < deadline:
        if ollama_up():
            _ALIVE_CACHE["t"] = 0
            return {"ok": True, "up": True, "action": "started", "binary": binary, "note": "Started Ollama."}
        time.sleep(0.25)
    return {
        "ok": False,
        "up": False,
        "action": "timeout",
        "binary": binary,
        "note": "Ollama did not come up. Checking other local runners, then cloud.",
    }


def alive_runners(*, refresh: bool = False) -> list[dict[str, Any]]:
    now = time.time()
    if not refresh and now - float(_ALIVE_CACHE["t"]) < 2:
        return list(_ALIVE_CACHE["rows"])
    rows = []
    for runner in RUNNERS:
        model = _probe(runner)
        if model is None:
            continue
        item = dict(runner)
        item["model"] = model
        rows.append(item)
    _ALIVE_CACHE["t"] = now
    _ALIVE_CACHE["rows"] = rows
    return rows


def pick_local(*, wake_ollama: bool = False) -> dict[str, Any] | None:
    """Ollama if it is up or we can start it. Else any already-running OpenAI-compatible server."""
    if wake_ollama:
        ensure_ollama()
        alive_runners(refresh=True)
    alive = alive_runners()
    for row in alive:
        if row["id"] == "ollama":
            return row
    return alive[0] if alive else None


def local_is_up(*, wake_ollama: bool = False) -> bool:
    return pick_local(wake_ollama=wake_ollama) is not None


def _probe(runner: dict[str, Any]) -> str | None:
    override = os.environ.get("PEM_LOCAL_MODEL")
    try:
        with urlopen(runner["probe"], timeout=0.45) as resp:
            if getattr(resp, "status", 200) >= 400:
                return None
            payload = json.loads(resp.read().decode("utf-8") or "{}")
    except Exception:
        return None
    if runner["kind"] == "ollama":
        names = [item.get("name") for item in payload.get("models") or [] if item.get("name")]
        if override:
            return override
        return names[0] if names else os.environ.get("PEM_OLLAMA_MODEL", "llama3.2")
    models = payload.get("data") or []
    ids = [item.get("id") for item in models if item.get("id")]
    if override:
        return override
    return ids[0] if ids else "local"


def litellm_kwargs(runner: dict[str, Any]) -> tuple[str, dict[str, str]]:
    """Model id and extra kwargs for litellm.completion."""
    if runner["kind"] == "ollama":
        raw = runner.get("model") or "llama3.2"
        model = raw if str(raw).startswith("ollama/") else f"ollama/{raw}"
        extra = {"api_base": runner["api_base"], "api_key": "ollama"}
        return model, extra
    model_name = runner.get("model") or "local"
    extra = {"api_base": runner["api_base"], "api_key": os.environ.get("PEM_LOCAL_API_KEY", "sk-local")}
    return f"openai/{model_name}", extra
