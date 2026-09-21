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
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}

PROVIDER_LABEL = {
    "claude": "Anthropic",
    "gemini": "Google Gemini",
    "deepseek": "DeepSeek",
    "kimi": "Kimi",
    "groq": "Groq",
    "openrouter": "OpenRouter",
}


def provider_slug_for_litellm(model: str) -> str:
    """Map a LiteLLM model id (provider/model) to keys.py provider slug."""
    prefix = str(model or "").split("/")[0].lower()
    return {
        "anthropic": "claude",
        "deepseek": "deepseek",
        "gemini": "gemini",
        "groq": "groq",
        "openrouter": "openrouter",
    }.get(prefix, prefix)


def load_keys() -> None:
    if load_dotenv is not None:
        load_dotenv(ENV_PATH, override=False)
        repo_root = Path(__file__).resolve().parent.parent
        env_profile = (os.environ.get("ASSURE_ENV") or "").strip().lower()
        # Precedence: shell env > .env.staging > .env.local > .env. Capture the
        # shell env, apply files with override=True so the later file wins in file
        # order, then re-apply the shell env on top so the shell always wins and
        # file-loaded values never override a caller's pre-set environment.
        _shell_env = dict(os.environ)
        for name in (".env", ".env.local"):
            f = repo_root / name
            if f.exists():
                load_dotenv(f, override=True)
        if env_profile == "staging":
            f = repo_root / ".env.staging"
            if f.exists():
                load_dotenv(f, override=True)
        os.environ.update(_shell_env)
        if Path.cwd() != PACKAGE_DIR and (Path.cwd() / ".env").exists():
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


def _browser_env(name: str) -> str | None:
    """In-browser BYOK keys from request headers. Never written to disk."""
    try:
        from flask import g, has_request_context
    except ImportError:
        return None
    if not has_request_context():
        return None
    blob = getattr(g, "browser_api_keys", None)
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
    if target == "claude":
        return bool(
            _cloud_env("ANTHROPIC_API_KEY")
            or _cloud_env("CLAUDE_API_KEY")
            or os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("CLAUDE_API_KEY")
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
    host = (
        os.environ.get("OLLAMA_HOST")
        or os.environ.get("OLLAMA_API_BASE")
        or "http://127.0.0.1:11434"
    )
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
            _browser_env("GEMINI_API_KEY")
            or _browser_env("GOOGLE_API_KEY")
            or _cloud_env("GEMINI_API_KEY")
            or _cloud_env("GOOGLE_API_KEY")
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
        )
    if target == "claude":
        return (
            _browser_env("ANTHROPIC_API_KEY")
            or _browser_env("CLAUDE_API_KEY")
            or _cloud_env("ANTHROPIC_API_KEY")
            or _cloud_env("CLAUDE_API_KEY")
            or os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("CLAUDE_API_KEY")
        )
    if target == "kimi":
        return (
            _browser_env("MOONSHOT_API_KEY")
            or _browser_env("KIMI_API_KEY")
            or _cloud_env("MOONSHOT_API_KEY")
            or _cloud_env("KIMI_API_KEY")
            or os.environ.get("MOONSHOT_API_KEY")
            or os.environ.get("KIMI_API_KEY")
        )
    env_name = PROVIDER_ENV.get(target)
    if not env_name:
        return None
    return _browser_env(env_name) or _cloud_env(env_name) or os.environ.get(env_name)


def anthropic_workspace_id() -> str:
    return (os.environ.get("ANTHROPIC_WORKSPACE_ID") or "").strip() or (
        os.environ.get("ANTHROPIC_WORKSPACE") or ""
    ).strip()


def save_anthropic_workspace_id(workspace_id: str) -> None:
    value = (workspace_id or "").strip()
    if not value:
        raise ValueError("Paste a workspace id first.")
    os.environ["ANTHROPIC_WORKSPACE_ID"] = value
    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    if set_key is not None:
        set_key(str(ENV_PATH), "ANTHROPIC_WORKSPACE_ID", value)
    else:
        _append_env("ANTHROPIC_WORKSPACE_ID", value)


# First path segment of a LiteLLM model id → slug used by orchestrator env_map /
# api_key_for / save_provider_key vocabulary (where overlapping).
_LITELLM_SLUG_ALIASES: dict[str, str] = {
    "anthropic": "claude",
    "gemini": "gemini",
    "google": "gemini",
    "deepseek": "deepseek",
    "moonshot": "kimi",
    "openrouter": "openrouter",
    "groq": "groq",
}

# Bare model ids (no slash). Longer / more specific prefixes first.
# slug may be None when the vendor is not in the keys/orchestrator vocabulary.
_BARE_MODEL_PREFIXES: tuple[tuple[str, str | None], ...] = (
    ("claude", "claude"),
    ("gemini", "gemini"),
    ("deepseek", "deepseek"),
    ("moonshot", "kimi"),
    ("kimi", "kimi"),
    ("gpt-", None),
)

# Single source of truth for orchestrator._api_key_env_for_model.
# keys.py and llm/orchestrator.py are a coupled deploy unit — ship together.
ORCHESTRATOR_ENV_MAP: dict[str, str] = {
    "claude": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}
ORCHESTRATOR_ENV_MAP_KEYS = frozenset(ORCHESTRATOR_ENV_MAP)


#: The upstream provider OpenRouter must use, named. Every call that produces a
#: document or checks one pins this: at temperature 0 the *provider* is what makes
#: a call reproducible, since an unpinned request is routed per call and OpenRouter
#: still samples across them. Measured on the compile path: Alibaba 8/8 identical,
#: Novita 8/8 identical, unpinned 8/8 distinct — and Alibaba and Novita do not
#: agree with each other (sha 25bbbc92… vs 585f6e9d…), which is why fallbacks stay
#: off: a provider outage must read as a failed call, never as a different result
#: under the same inputs. ``extra_body`` carries it, because litellm passes a
#: caller's extra_body through to the OpenRouter request body and a named kwarg
#: has no route to that field.
PROVIDER_PIN: dict = {"order": ["Alibaba"], "allow_fallbacks": False}


def provider_slug_for_litellm(model: str | None) -> str | None:
    """Map a LiteLLM model id to the slug recognized by api_key_for / save_provider_key
    (and by orchestrator's provider env_map). Returns None for unknown models — never
    invent a default slug."""
    if not model:
        return None
    head = str(model).split("/", 1)[0].strip().lower()
    if "/" in str(model):
        return _LITELLM_SLUG_ALIASES.get(head)  # no default
    for prefix, slug in _BARE_MODEL_PREFIXES:
        if head.startswith(prefix):
            return slug
    return None


def litellm_kwargs_for(target: str) -> dict:
    """API key plus Claude workspace header. Never log the values."""
    extra: dict = {}
    api_key = api_key_for(target)
    if api_key:
        extra["api_key"] = api_key
    if target == "kimi":
        extra["api_base"] = os.environ.get("MOONSHOT_API_BASE", "https://api.moonshot.ai/v1")
    if target == "openrouter":
        extra["api_base"] = os.environ.get("OPENROUTER_API_BASE", "https://openrouter.ai/api/v1")
        referer = os.environ.get("OPENROUTER_HTTP_REFERER", "https://staging.getassureai.com")
        title = os.environ.get("OPENROUTER_APP_TITLE", "Assure AI")
        extra["extra_headers"] = {"HTTP-Referer": referer, "X-Title": title}
    if target == "claude":
        workspace = anthropic_workspace_id()
        if workspace:
            extra["extra_headers"] = {"anthropic-workspace-id": workspace}
    return extra


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


def send_ready() -> bool:
    """True if a live Send can go to an API key or to Ollama. Cursor is copy-only."""
    status = provider_status()
    for item in status.get("providers", {}).values():
        if item.get("id") == "cursor":
            continue
        if item.get("connected"):
            return True
    return False


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
    if target == "claude":
        os.environ["CLAUDE_API_KEY"] = key
    if target == "kimi":
        os.environ["KIMI_API_KEY"] = key
    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    if set_key is not None:
        set_key(str(ENV_PATH), env_name, key)
        if target == "gemini":
            set_key(str(ENV_PATH), "GOOGLE_API_KEY", key)
        if target == "claude":
            set_key(str(ENV_PATH), "CLAUDE_API_KEY", key)
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
