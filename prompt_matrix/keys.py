"""Provider API keys for --direct / Send through the model API."""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv, set_key
except ImportError:  # pragma: no cover
    load_dotenv = None
    set_key = None

try:
    from .local_runners import local_is_up
except ImportError:
    from local_runners import local_is_up

try:
    from .paths import user_data_dir
except ImportError:
    from paths import user_data_dir

PACKAGE_DIR = user_data_dir()
ENV_PATH = PACKAGE_DIR / ".env"

PROVIDER_ENV = {
    "claude": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "kimi": "MOONSHOT_API_KEY",
}

PROVIDER_LABEL = {
    "claude": "Anthropic",
    "gemini": "Google Gemini",
    "deepseek": "DeepSeek",
    "kimi": "Kimi",
}


def load_keys() -> None:
    if load_dotenv is not None:
        load_dotenv(ENV_PATH, override=False)
        if Path.cwd() != PACKAGE_DIR:
            load_dotenv(Path.cwd() / ".env", override=False)


def _cloud_env(name: str) -> str | None:
    """Request-local decrypted cloud keys. Never written to disk."""
    try:
        from flask import g, has_request_context
    except ImportError:
        return None
    if not has_request_context():
        return None
    blob = getattr(g, "cloud_api_keys", None)
    if not isinstance(blob, dict):
        return None
    val = blob.get(name)
    if isinstance(val, str) and val.strip():
        return val.strip()
    return None


def key_present(target: str) -> bool:
    if target == "gemini":
        return bool(
            _cloud_env("GEMINI_API_KEY")
            or _cloud_env("GOOGLE_API_KEY")
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
        )
    if target == "kimi":
        return bool(
            _cloud_env("MOONSHOT_API_KEY")
            or _cloud_env("KIMI_API_KEY")
            or os.environ.get("MOONSHOT_API_KEY")
            or os.environ.get("KIMI_API_KEY")
        )
    if target == "ollama":
        return local_is_up(wake_ollama=False)
    env_name = PROVIDER_ENV.get(target)
    return bool(env_name and (_cloud_env(env_name) or os.environ.get(env_name)))


def ollama_up() -> bool:
    host = os.environ.get("OLLAMA_HOST") or os.environ.get("OLLAMA_API_BASE") or "http://127.0.0.1:11434"
    url = host.rstrip("/") + "/api/tags"
    try:
        from urllib.request import urlopen

        with urlopen(url, timeout=0.6) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except Exception:
        return False


def api_key_for(target: str) -> str | None:
    if target == "gemini":
        return (
            _cloud_env("GEMINI_API_KEY")
            or _cloud_env("GOOGLE_API_KEY")
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
        )
    if target == "kimi":
        return (
            _cloud_env("MOONSHOT_API_KEY")
            or _cloud_env("KIMI_API_KEY")
            or os.environ.get("MOONSHOT_API_KEY")
            or os.environ.get("KIMI_API_KEY")
        )
    env_name = PROVIDER_ENV.get(target)
    if not env_name:
        return None
    return _cloud_env(env_name) or os.environ.get(env_name)


def missing_key_message(target: str) -> str | None:
    if target == "cursor":
        return None
    if target == "ollama":
        if local_is_up(wake_ollama=False):
            return None
        return (
            "No local runner is up. Install Ollama for a personal loop (PEM can start it), "
            "or start vLLM / SGLang / Llamafile / LM Studio on their usual ports, "
            "or leave Send on and PEM hops to Gemini/DeepSeek/Claude/Kimi."
        )
    if key_present(target):
        return None
    env_name = PROVIDER_ENV.get(target, "the provider API key")
    label = PROVIDER_LABEL.get(target, target)
    return (
        f"{label} is not connected. Paste your {env_name} on the Connect page, "
        "or export it in your shell and restart pem."
    )


def send_ready() -> bool:
    """True if a live Send can go to an API key or to Ollama. Cursor is copy-only."""
    status = provider_status()
    for item in status.get("providers", {}).values():
        if item.get("id") == "cursor":
            continue
        if item.get("connected"):
            return True
    return False


def provider_status() -> dict:
    load_keys()
    providers = {}
    for target, env_name in PROVIDER_ENV.items():
        providers[target] = {
            "id": target,
            "label": PROVIDER_LABEL[target],
            "env": env_name,
            "connected": key_present(target),
        }
    ollama = local_is_up(wake_ollama=False)
    try:
        from .route import route_snapshot
    except ImportError:
        from route import route_snapshot
    snap = route_snapshot()
    providers["ollama"] = {
        "id": "ollama",
        "label": "Ollama",
        "env": None,
        "connected": ollama,
        "note": snap.get("note"),
    }
    providers["cursor"] = {
        "id": "cursor",
        "label": "Cursor",
        "env": None,
        "connected": False,
        "note": "No public chat API. Copy the prompt into Cursor.",
    }
    return {"providers": providers, "env_file": str(ENV_PATH), "route": snap}


def save_provider_key(target: str, key: str) -> dict:
    target = (target or "").strip().lower()
    env_name = PROVIDER_ENV.get(target)
    if not env_name:
        raise ValueError(f"No API key is used for '{target}'.")
    key = (key or "").strip()
    if not key:
        raise ValueError("Paste an API key first.")
    os.environ[env_name] = key
    if target == "gemini":
        os.environ["GOOGLE_API_KEY"] = key
    if target == "kimi":
        os.environ["KIMI_API_KEY"] = key
    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    if set_key is not None:
        set_key(str(ENV_PATH), env_name, key)
        if target == "gemini":
            set_key(str(ENV_PATH), "GOOGLE_API_KEY", key)
        if target == "kimi":
            set_key(str(ENV_PATH), "KIMI_API_KEY", key)
    else:
        _append_env(env_name, key)
    return provider_status()


def _append_env(name: str, value: str) -> None:
    existing = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.is_file() else ""
    lines = [line for line in existing.splitlines() if not line.startswith(f"{name}=")]
    lines.append(f"{name}={value}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
