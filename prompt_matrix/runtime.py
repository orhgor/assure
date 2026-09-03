"""Import and smoke-test the OSS stack that sits behind the web UI."""

from __future__ import annotations

from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from io import StringIO
from typing import Any, Callable

DIST_NAMES = {
    "jinja2": "Jinja2",
    "pydantic": "pydantic",
    "flask": "Flask",
    "rich": "rich",
    "instructor": "instructor",
    "litellm": "litellm",
    "dotenv": "python-dotenv",
    "tiktoken": "tiktoken",
    "flask_httpauth": "Flask-HTTPAuth",
}


def _dist_version(dist: str, module: Any) -> str:
    try:
        return version(dist)
    except PackageNotFoundError:
        return str(getattr(module, "__version__", "unknown"))


def _probe_jinja2() -> str:
    from jinja2 import Environment, StrictUndefined

    out = Environment(undefined=StrictUndefined).from_string("{{ x }}").render(x="ok")
    if out != "ok":
        raise RuntimeError(f"unexpected render {out!r}")
    return "rendered a template"


def _probe_pydantic() -> str:
    try:
        from .models import PromptRequest
    except ImportError:
        from models import PromptRequest

    req = PromptRequest(target_ai="kimi", intent="research", task="probe")
    if req.task != "probe":
        raise RuntimeError("validation failed")
    return "validated PromptRequest"


def _probe_flask() -> str:
    from flask import Flask, jsonify

    app = Flask("pem-probe")

    @app.get("/x")
    def ping():
        return jsonify(ok=True)

    payload = app.test_client().get("/x").get_json()
    if not payload or payload.get("ok") is not True:
        raise RuntimeError("test client failed")
    return "answered a test request"


def _probe_rich() -> str:
    from rich.console import Console

    buf = StringIO()
    Console(file=buf, force_terminal=False).print("ok")
    if "ok" not in buf.getvalue():
        raise RuntimeError("console print failed")
    return "printed to a buffer"


def _probe_clipboard() -> str:
    try:
        from .engine import copy_to_clipboard
    except ImportError:
        from engine import copy_to_clipboard

    if not callable(copy_to_clipboard):
        raise RuntimeError("copy_to_clipboard missing")
    return "web UI uses navigator.clipboard; server does not copy"


def _probe_instructor() -> str:
    import instructor

    if not callable(getattr(instructor, "from_litellm", None)):
        raise RuntimeError("from_litellm missing")
    return "from_litellm is callable"


def _probe_litellm() -> str:
    import litellm

    if not callable(getattr(litellm, "completion", None)):
        raise RuntimeError("completion missing")
    model, provider, *_rest = litellm.get_llm_provider("moonshot/kimi-k2.5")
    if provider != "moonshot":
        raise RuntimeError(f"unexpected provider {provider!r}")
    return f"resolved {model} via {provider}"


def _probe_dotenv() -> str:
    from dotenv import load_dotenv, set_key

    if not callable(load_dotenv) or not callable(set_key):
        raise RuntimeError("load_dotenv/set_key missing")
    return "load_dotenv is callable"


def _probe_tiktoken() -> str:
    try:
        from .token_counter import count_tokens
    except ImportError:
        from token_counter import count_tokens

    n = count_tokens("hello world")
    if n < 1:
        raise RuntimeError("cl100k_base returned 0")
    return f"cl100k_base counted {n} tokens"


def _probe_flask_httpauth() -> str:
    from flask_httpauth import HTTPBasicAuth

    if not callable(getattr(HTTPBasicAuth, "verify_password", None)):
        raise RuntimeError("HTTPBasicAuth missing")
    return "HTTPBasicAuth is importable"


PROBES: list[tuple[str, str, str, Callable[[], str]]] = [
    ("jinja2", "Jinja2", "Every Generate call fills the target template.", _probe_jinja2),
    ("pydantic", "pydantic", "Validates config.json, requests, and saved classes.", _probe_pydantic),
    ("flask", "Flask", "Serves this page and /api/*.", _probe_flask),
    ("rich", "rich", "Prints the local URL when pem starts.", _probe_rich),
    ("clipboard", "clipboard", "Browser copy in the web UI.", _probe_clipboard),
    ("instructor", "instructor", "Structured replies when Send is on.", _probe_instructor),
    ("litellm", "LiteLLM", "Send / --direct talks to provider APIs.", _probe_litellm),
    ("dotenv", "python-dotenv", "Loads and writes prompt_matrix/.env.", _probe_dotenv),
    ("tiktoken", "tiktoken", "Counts input, output, and total tokens after each run.", _probe_tiktoken),
    ("flask_httpauth", "Flask-HTTPAuth", "Basic Auth on the local UI except /api/health.", _probe_flask_httpauth),
]

_BUILTIN_PROBE_IDS = frozenset({"clipboard"})


def probe_libraries() -> list[dict[str, Any]]:
    rows = []
    for module_name, label, when, probe in PROBES:
        dist = DIST_NAMES.get(module_name, "builtin")
        row: dict[str, Any] = {
            "id": module_name,
            "label": label,
            "dist": dist,
            "when": when,
            "ok": False,
            "version": None,
            "detail": "",
        }
        try:
            if module_name in _BUILTIN_PROBE_IDS:
                row["version"] = "builtin"
                row["detail"] = probe()
                row["ok"] = True
            else:
                module = import_module(module_name)
                row["version"] = _dist_version(dist, module)
                row["detail"] = probe()
                row["ok"] = True
        except Exception as exc:
            row["detail"] = f"{type(exc).__name__}: {exc}"
        rows.append(row)
    return rows


def library_status() -> dict[str, Any]:
    libraries = probe_libraries()
    try:
        from .route import route_snapshot
    except ImportError:
        from route import route_snapshot
    return {
        "ok": all(item["ok"] for item in libraries),
        "name": "prompt-matrix",
        "libraries": libraries,
        "route": route_snapshot(),
    }


def warm_libraries() -> dict[str, Any]:
    """Import the slow providers once so the first Send is not a cold start."""
    return library_status()
