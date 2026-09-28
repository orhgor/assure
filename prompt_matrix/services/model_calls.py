"""The model-call ledger: one row per request the application makes to a model.

Why this exists (customer report, 2026-09-28): "requests are not reaching
OpenRouter and I cannot tell whether the pipeline ran". Every stage already
writes its own summary (``report.execution`` for Parsure, the draft's
``meta`` for the compile), but none of them records the request itself —
whether it left this server, to which model, what came back, how long it
took. Without that a green stage summary and a stage whose request never
went out look the same. This module records the request.

How: litellm runs every callback in ``litellm.callbacks`` after each
completion (streamed or not) and after each failure, with the kwargs it sent
and the response or exception it got. ``ModelCallLogger`` turns that into a
row in ``model_calls`` (``db/model_calls_repository``). The Bedrock Converse
path in ``cost_governance._default_executor`` bypasses litellm and records
through ``record()`` directly. The stage a call belongs to is set by the
caller with ``stage_context()``; when a caller did not (legacy paths), it is
inferred from the model's stage role, and the row says so in ``stage_source``.

Nothing here changes an answer: a ledger write that fails is logged and
dropped, the completion result is returned untouched. The ledger is a record
of what happened, never a gate.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Iterator

log = logging.getLogger(__name__)

try:
    from ..db import model_calls_repository as repo
except ImportError:  # pragma: no cover - flat-import fallback
    from db import model_calls_repository as repo  # type: ignore

#: Pipeline stages in the order the UI lists them. ``source`` names where the
#: activity route reads the stage's evidence from.
STAGES: tuple[dict[str, str], ...] = (
    {"stage": "parse", "label": "Parse", "source": "ingest_job"},
    {"stage": "intake", "label": "Intake & routing (LAYA)", "source": "execution"},
    {"stage": "z3", "label": "Z3 verification", "source": "execution"},
    {"stage": "llm_grounding", "label": "Grounded field pass", "source": "model_calls+execution"},
    {"stage": "discovery", "label": "Field discovery", "source": "model_calls+execution"},
    {"stage": "vision", "label": "Vision", "source": "model_calls+execution"},
    {"stage": "redhat_graph", "label": "Red-Hat graph critique", "source": "model_calls+execution"},
    {"stage": "redhat_targeted", "label": "Red-Hat targeted re-read", "source": "execution"},
    {"stage": "compile_draft", "label": "Compile draft", "source": "model_calls"},
    {"stage": "anchor", "label": "Claim anchoring", "source": "model_calls"},
    {"stage": "entailment", "label": "Entailment", "source": "model_calls"},
    {"stage": "edit", "label": "Surgical edit", "source": "model_calls"},
    {"stage": "redhat_multipass", "label": "Red-Hat audit", "source": "model_calls"},
    {"stage": "compare", "label": "Compare", "source": "model_calls"},
)
STAGE_NAMES = tuple(s["stage"] for s in STAGES)

#: A model's stage *role* (the ``ASSURE_<BACKEND>_MODEL_<ROLE>`` variable it
#: came from) → the pipeline stage a call with no context most likely is.
_ROLE_TO_STAGE = {
    "parse": "llm_grounding", "draft": "compile_draft", "anchor": "anchor", "evidence": "entailment",
    "edit": "edit", "redhat": "redhat_multipass", "compare": "compare", "vision": "vision",
}

_ctx: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("model_call_ctx", default=None)
_installed = False
_install_lock = threading.Lock()


@contextlib.contextmanager
def stage_context(stage: str, *, project_id: str | None = None, task: str | None = None) -> Iterator[None]:
    """Name the stage (and project / task) the model calls made inside belong to.

    Nested contexts keep the outer project id when the inner one gives none, so
    ``discovery`` inside a Parsure run still lands on the run's project."""
    outer = _ctx.get() or {}
    value = {
        "stage": stage if stage in STAGE_NAMES else outer.get("stage"),
        "project_id": project_id or outer.get("project_id"),
        "task": task or outer.get("task"),
    }
    token = _ctx.set(value)
    try:
        yield
    finally:
        _ctx.reset(token)


def current_context() -> dict[str, Any]:
    return dict(_ctx.get() or {})


def iter_in_stage(iterator: Any, stage: str, *, project_id: str | None = None, task: str | None = None) -> Iterator[Any]:
    """Drive ``iterator`` (a model stream) inside ``stage_context`` — for
    generator pipelines whose stream call the tests replace by name, so the
    stage wraps the iteration rather than the (patched) function."""
    with stage_context(stage, project_id=project_id, task=task):
        yield from iterator


def litellm_metadata(existing: dict[str, Any] | None = None) -> dict[str, Any]:
    """The ``metadata`` kwarg for a litellm call: the current stage context under
    ``assure``. litellm runs its success handler on a helper thread, where the
    contextvar is gone, but hands ``metadata`` back to the callback — so the
    stage travels inside the call instead of beside it."""
    meta = dict(existing or {})
    meta["assure"] = current_context()
    return meta


def _context_from_kwargs(kwargs: dict) -> dict[str, Any]:
    params = kwargs.get("litellm_params") or {}
    meta = (params.get("metadata") if isinstance(params, dict) else None) or kwargs.get("metadata") or {}
    assure = meta.get("assure") if isinstance(meta, dict) else None
    return dict(assure) if isinstance(assure, dict) else current_context()


def _model_from_kwargs(kwargs: dict) -> str:
    """The model as the application named it: litellm hands the callback the
    bare id (``meta-llama/llama-3.3-70b-instruct``) with the provider apart in
    ``litellm_params.custom_llm_provider`` — the row keeps ``openrouter/…`` so
    the provider column and the configured stage models read the same."""
    model = str(kwargs.get("model") or "")
    params = kwargs.get("litellm_params") or {}
    provider = str((params.get("custom_llm_provider") if isinstance(params, dict) else None) or "").strip().lower()
    if provider and provider not in ("", "bedrock") and not model.startswith(provider + "/"):
        return f"{provider}/{model}"
    if provider == "bedrock" and not model.startswith("bedrock/"):
        return f"bedrock/{model}"
    return model


def provider_of(model: str | None) -> str:
    m = str(model or "")
    if m.startswith("openrouter/"):
        return "openrouter"
    if m.startswith("ollama/") or m.startswith("ollama_chat/"):
        return "ollama"
    if m.startswith("bedrock/") or m.startswith("anthropic.") or re.match(r"^(us|eu|apac|global)\.anthropic\.", m):
        return "bedrock"
    if m.startswith("anthropic/") or m.startswith("claude-"):
        return "anthropic"
    return m.split("/", 1)[0] if "/" in m else "unknown"


def backend_name() -> str:
    try:
        try:
            from ..cost_governance import llm_backend
        except ImportError:
            from cost_governance import llm_backend  # type: ignore
        return llm_backend() or "cloud"
    except Exception:  # noqa: BLE001
        return "unknown"


def infer_stage(model: str | None) -> str | None:
    """The stage whose configured model this is, or ``None`` when several stages
    share it (then the row keeps ``stage`` null rather than guessing)."""
    bare = str(model or "").split("/", 1)[-1] if str(model or "").count("/") >= 1 else str(model or "")
    try:
        try:
            from .. import cost_governance as cg
        except ImportError:
            import cost_governance as cg  # type: ignore
        backend = cg.llm_backend()
        by_stage = cg.openrouter_models_by_stage() if backend == "openrouter" else cg.local_models_by_stage() if backend == "ollama" else {}
    except Exception:  # noqa: BLE001
        return None
    roles = [role for role, m in by_stage.items() if m == bare or f"openrouter/{m}" == model or f"ollama/{m}" == model]
    if len(roles) == 1:
        return _ROLE_TO_STAGE.get(roles[0])
    return None


def _prompt_chars(messages: Any) -> int:
    total = 0
    for msg in messages or []:
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    total += len(part["text"])
    return total


def _response_text(response_obj: Any) -> str:
    try:
        choices = getattr(response_obj, "choices", None) or (response_obj.get("choices") if isinstance(response_obj, dict) else None) or []
        parts = []
        for ch in choices:
            msg = getattr(ch, "message", None) or (ch.get("message") if isinstance(ch, dict) else None)
            content = getattr(msg, "content", None) if msg is not None and not isinstance(msg, dict) else (msg or {}).get("content")
            if isinstance(content, str):
                parts.append(content)
        return "".join(parts)
    except Exception:  # noqa: BLE001
        return ""


def _usage(response_obj: Any) -> tuple[int | None, int | None]:
    usage = getattr(response_obj, "usage", None) or (response_obj.get("usage") if isinstance(response_obj, dict) else None)
    if usage is None:
        return None, None
    get = (lambda k: getattr(usage, k, None)) if not isinstance(usage, dict) else usage.get
    try:
        return (int(get("prompt_tokens")) if get("prompt_tokens") is not None else None,
                int(get("completion_tokens")) if get("completion_tokens") is not None else None)
    except (TypeError, ValueError):
        return None, None


def _http_status_of(exc: BaseException | None) -> int | None:
    if exc is None:
        return None
    for attr in ("status_code", "http_status", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and 100 <= value <= 599:
            return value
    m = re.search(r"\b([45]\d{2})\b", str(exc))
    return int(m.group(1)) if m else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def record(
    *,
    model: str | None,
    status: str,
    ms: float | None,
    prompt_chars: int | None = None,
    completion_chars: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    http_status: int | None = None,
    error: str | None = None,
    stage: str | None = None,
    project_id: str | None = None,
    task: str | None = None,
    stream: bool = False,
    path: str = "litellm",
) -> dict[str, Any] | None:
    """Write one ledger row. Never raises; returns the row or ``None``."""
    ctx = current_context()
    stage_source = "context"
    stage = stage or ctx.get("stage")
    if not stage:
        stage = infer_stage(model)
        stage_source = "inferred" if stage else "unknown"
    row = {
        "id": f"mc-{uuid.uuid4().hex[:16]}",
        "project_id": project_id or ctx.get("project_id"),
        "stage": stage,
        "stage_source": stage_source,
        "task": task or ctx.get("task"),
        "backend": backend_name(),
        "provider": provider_of(model),
        "model": str(model or ""),
        "status": status,
        "http_status": http_status,
        "ms": int(ms) if ms is not None else None,
        "prompt_chars": prompt_chars,
        "completion_chars": completion_chars,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "error": (str(error)[:2000] if error else None),
        "stream": 1 if stream else 0,
        "path": path,
        "worker": f"{os.uname().nodename}:{os.getpid()}" if hasattr(os, "uname") else str(os.getpid()),
        "created_at": _now(),
    }
    try:
        repo.insert(row)
    except Exception as exc:  # noqa: BLE001 — the ledger never fails the call
        log.warning("model_calls: ledger write failed (%s: %s); call: %s %s", type(exc).__name__, exc, row["model"], status)
        return None
    return row


class ModelCallLogger:
    """litellm ``CustomLogger`` subclass built lazily (litellm import is slow and
    optional at import time); see ``install()``."""


def _build_logger_class():
    from litellm.integrations.custom_logger import CustomLogger  # type: ignore

    class _Logger(CustomLogger):  # type: ignore[misc]
        def _ms(self, start_time: Any, end_time: Any) -> float | None:
            try:
                return max(0.0, (end_time - start_time).total_seconds() * 1000.0)
            except Exception:  # noqa: BLE001
                return None

        def log_success_event(self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any) -> None:
            try:
                in_tok, out_tok = _usage(response_obj)
                text = _response_text(response_obj)
                ctx = _context_from_kwargs(kwargs)
                record(
                    model=_model_from_kwargs(kwargs), status="ok", ms=self._ms(start_time, end_time),
                    prompt_chars=_prompt_chars(kwargs.get("messages")), completion_chars=len(text) if text else None,
                    input_tokens=in_tok, output_tokens=out_tok, http_status=200,
                    stage=ctx.get("stage"), project_id=ctx.get("project_id"), task=ctx.get("task"),
                    stream=bool(kwargs.get("stream")), path="litellm",
                )
            except Exception:  # noqa: BLE001
                log.debug("model_calls: success callback failed", exc_info=True)

        def log_failure_event(self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any) -> None:
            try:
                exc = kwargs.get("exception")
                ctx = _context_from_kwargs(kwargs)
                record(
                    model=_model_from_kwargs(kwargs), status="error", ms=self._ms(start_time, end_time),
                    prompt_chars=_prompt_chars(kwargs.get("messages")), http_status=_http_status_of(exc),
                    error=f"{type(exc).__name__}: {exc}" if exc is not None else "unknown error",
                    stage=ctx.get("stage"), project_id=ctx.get("project_id"), task=ctx.get("task"),
                    stream=bool(kwargs.get("stream")), path="litellm",
                )
            except Exception:  # noqa: BLE001
                log.debug("model_calls: failure callback failed", exc_info=True)

        # Async variants: litellm calls these for acompletion; same rows.
        async def async_log_success_event(self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any) -> None:
            self.log_success_event(kwargs, response_obj, start_time, end_time)

        async def async_log_failure_event(self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any) -> None:
            self.log_failure_event(kwargs, response_obj, start_time, end_time)

    return _Logger


def install() -> bool:
    """Register the logger with litellm once per process (web and worker both
    call this at start-up). Returns whether the logger is installed."""
    global _installed
    with _install_lock:
        if _installed:
            return True
        try:
            import litellm  # type: ignore

            logger = _build_logger_class()()
            callbacks = getattr(litellm, "callbacks", None)
            if isinstance(callbacks, list):
                if not any(type(cb).__name__ == "_Logger" and getattr(cb, "__module__", "") == __name__ for cb in callbacks):
                    callbacks.append(logger)
            else:
                litellm.callbacks = [logger]
            _installed = True
            log.info("model_calls: litellm ledger callback installed")
        except Exception as exc:  # noqa: BLE001
            log.warning("model_calls: litellm callback not installed (%s: %s)", type(exc).__name__, exc)
            _installed = False
        return _installed


def installed() -> bool:
    return _installed


# --------------------------------------------------------------------------- #
# Backend probe: can a request leave this server at all?
# --------------------------------------------------------------------------- #

_PROBE_TTL_S = 30.0
_probe_cache: dict[str, Any] = {"at": 0.0, "value": None}
_probe_lock = threading.Lock()


def key_hint(value: str | None) -> str | None:
    if not value:
        return None
    v = str(value).strip()
    if len(v) <= 8:
        return "…" + v[-2:]
    return f"{v[:6]}…{v[-4:]}"


def _probe_openrouter(timeout_s: float) -> dict[str, Any]:
    import urllib.error
    import urllib.request

    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        return {"status": "no_key", "detail": "OPENROUTER_API_KEY is empty", "ms": None}
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/auth/key",
        headers={"Authorization": f"Bearer {key}", "User-Agent": "assure-health/1.0"},
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 - fixed https URL
            body = resp.read(4096).decode("utf-8", "replace")
            ms = (time.monotonic() - started) * 1000
            detail = f"HTTP {resp.status}"
            try:
                import json

                data = (json.loads(body) or {}).get("data") or {}
                label = data.get("label")
                usage = data.get("usage")
                limit = data.get("limit")
                if label:
                    detail += f" · key '{label}'"
                if usage is not None:
                    detail += f" · usage ${usage}" + (f" of ${limit}" if limit is not None else "")
            except Exception:  # noqa: BLE001
                pass
            return {"status": "reachable", "detail": detail, "ms": int(ms)}
    except urllib.error.HTTPError as exc:
        ms = (time.monotonic() - started) * 1000
        status = "unauthorized" if exc.code in (401, 403) else "unreachable"
        return {"status": status, "detail": f"HTTP {exc.code} {exc.reason}", "ms": int(ms)}
    except Exception as exc:  # noqa: BLE001
        ms = (time.monotonic() - started) * 1000
        return {"status": "unreachable", "detail": f"{type(exc).__name__}: {exc}"[:300], "ms": int(ms)}


def _probe_ollama(timeout_s: float) -> dict[str, Any]:
    import urllib.request

    base = os.environ.get("OLLAMA_HOST", "").strip() or os.environ.get("OLLAMA_API_BASE", "").strip() or "http://localhost:11434"
    if not base.startswith("http"):
        base = "http://" + base
    started = time.monotonic()
    try:
        with urllib.request.urlopen(base.rstrip("/") + "/api/tags", timeout=timeout_s) as resp:  # noqa: S310
            resp.read(2048)
            return {"status": "reachable", "detail": f"HTTP {resp.status} {base}", "ms": int((time.monotonic() - started) * 1000)}
    except Exception as exc:  # noqa: BLE001
        return {"status": "unreachable", "detail": f"{type(exc).__name__}: {exc}"[:300], "ms": int((time.monotonic() - started) * 1000)}


def probe_backend(timeout_s: float = 4.0, *, force: bool = False) -> dict[str, Any]:
    """Live check of the configured backend: OpenRouter's key endpoint (``GET
    /api/v1/auth/key`` — a 200 proves the key is valid and the network path
    open; 401 a bad key), Ollama's ``/api/tags``. Bedrock is not probed (an
    STS call costs credentials the web tier may not hold): ``not_probed``.
    Cached for 30 s so the UI's polling does not itself become traffic."""
    backend = backend_name()
    cache_key = (backend, key_hint(os.environ.get("OPENROUTER_API_KEY", "").strip()) if backend == "openrouter" else None)
    with _probe_lock:
        now = time.monotonic()
        if (not force and _probe_cache["value"] is not None and _probe_cache.get("key") == cache_key
                and now - _probe_cache["at"] < _PROBE_TTL_S):
            return dict(_probe_cache["value"])
    if backend == "openrouter":
        result = _probe_openrouter(timeout_s)
    elif backend == "ollama":
        result = _probe_ollama(timeout_s)
    elif backend == "bedrock":
        result = {"status": "not_probed", "detail": "Bedrock is not probed; see the ledger for real calls", "ms": None}
    else:
        result = {"status": "not_probed", "detail": f"backend '{backend}' has no probe", "ms": None}
    result["checked_at"] = _now()
    result["backend"] = backend
    with _probe_lock:
        _probe_cache["at"] = time.monotonic()
        _probe_cache["key"] = cache_key
        _probe_cache["value"] = dict(result)
    return result


def llm_status(*, probe: bool = True, timeout_s: float = 4.0) -> dict[str, Any]:
    """The ``llm`` block the activity route and /health share."""
    backend = backend_name()
    key_env = {"openrouter": "OPENROUTER_API_KEY"}.get(backend)
    key = os.environ.get(key_env, "").strip() if key_env else ""
    summary = {}
    try:
        summary = repo.summary(hours=24)
    except Exception as exc:  # noqa: BLE001
        summary = {"calls": None, "failed": None, "last_call_at": None, "error": f"{type(exc).__name__}: {exc}"[:200]}
    return {
        "backend": backend,
        "provider": backend if backend in ("openrouter", "ollama", "bedrock") else "unknown",
        "key_present": bool(key) if key_env else None,
        "key_hint": key_hint(key) if key else None,
        "probe": probe_backend(timeout_s) if probe else {"status": "not_probed", "detail": "probe skipped", "ms": None},
        "ledger_installed": installed(),
        "last_call_at": summary.get("last_call_at"),
        "calls_24h": summary.get("calls"),
        "failed_24h": summary.get("failed"),
    }
