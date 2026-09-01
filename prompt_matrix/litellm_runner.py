"""LiteLLM completion wrapper. Caps tokens and time. No streaming."""

from __future__ import annotations

import os
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

import litellm

try:
    from litellm import Timeout
except ImportError:  # pragma: no cover
    Timeout = TimeoutError  # type: ignore[misc,assignment]

try:
    from litellm.exceptions import ContextWindowExceededError
except ImportError:  # pragma: no cover
    ContextWindowExceededError = type("ContextWindowExceededError", (Exception,), {})  # type: ignore[misc,assignment]

DEFAULT_MAX_TOKENS = 4096
DEFAULT_TIMEOUT_SECONDS = 60

_LENGTH_REASONS = frozenset(
    {
        "length",
        "max_tokens",
        "max_output_tokens",
        "max_output_length",
        "token_limit",
        "max_tokens_reached",
    }
)


@dataclass(frozen=True)
class CompletionMeta:
    finish_reason: str | None = None
    max_tokens: int = 0
    hit_length: bool = False


_last_meta: ContextVar[CompletionMeta | None] = ContextVar("pem_last_completion", default=None)


def last_completion_meta() -> CompletionMeta:
    return _last_meta.get() or CompletionMeta()


def reset_completion_meta() -> None:
    _last_meta.set(None)


def completion_limits(
    max_tokens: int | None = None,
    timeout: int | None = None,
    intent: str | None = None,
    model: str | None = None,
) -> tuple[int, int]:
    tokens = max_tokens if max_tokens is not None else _int_env("PEM_MAX_TOKENS", DEFAULT_MAX_TOKENS)
    seconds = timeout if timeout is not None else _int_env("PEM_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
    if intent:
        try:
            from .cost_router import cap_output_tokens
        except ImportError:
            from cost_router import cap_output_tokens
        tokens = min(tokens, cap_output_tokens(intent, model or ""))
    try:
        from .cost_router import current_role_limits
    except ImportError:
        from cost_router import current_role_limits
    floor_tok, floor_sec = current_role_limits()
    if floor_tok:
        tokens = max(tokens, floor_tok)
    if floor_sec:
        seconds = max(seconds, floor_sec)
    return tokens, seconds


def call_model(
    model: str,
    messages: list,
    max_tokens: int | None = None,
    timeout: int | None = None,
    intent: str | None = None,
    **kwargs: Any,
) -> str:
    _max_tokens, _timeout = completion_limits(max_tokens, timeout, intent=intent, model=model)
    extra = {key: value for key, value in kwargs.items() if key != "stream"}
    try:
        response = litellm.completion(
            model=model,
            messages=messages,
            max_tokens=_max_tokens,
            timeout=_timeout,
            stream=False,
            **extra,
        )
        choice = response.choices[0]
        content = choice.message.content
        finish = getattr(choice, "finish_reason", None)
        finish_s = str(finish).strip() if finish is not None else None
        hit = (finish_s or "").lower().replace(" ", "_") in _LENGTH_REASONS
        _last_meta.set(CompletionMeta(finish_reason=finish_s, max_tokens=_max_tokens, hit_length=hit))
        return str(content) if content is not None else ""
    except Timeout:
        return (
            f"ERROR: Run aborted due to timeout ({_timeout}s). "
            "Please increase PEM_TIMEOUT_SECONDS or split the task."
        )
    except TimeoutError:
        return (
            f"ERROR: Run aborted due to timeout ({_timeout}s). "
            "Please increase PEM_TIMEOUT_SECONDS or split the task."
        )
    except ContextWindowExceededError:
        return (
            "ERROR: Prompt exceeds model's context window. "
            "Use PEM_STORE_PROMPTS to debug and truncate."
        )


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default
