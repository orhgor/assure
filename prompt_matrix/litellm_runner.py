"""LiteLLM completion wrapper. Caps tokens and time. No streaming."""

from __future__ import annotations

import os
import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

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

T = TypeVar("T")
RATE_LIMIT_MAX_RETRIES = 5
RATE_LIMIT_BASE_BACKOFF_S = 1.0


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


def _is_rate_limit_error(exc: BaseException) -> bool:
    name = exc.__class__.__name__.lower()
    if "ratelimit" in name or name == "serviceunavailableerror":
        return True
    text = str(exc).lower()
    return "429" in text or "rate limit" in text or "too many requests" in text


def call_with_retry(
    fn: Callable[[], T],
    *,
    max_retries: int = RATE_LIMIT_MAX_RETRIES,
    base_backoff_s: float = RATE_LIMIT_BASE_BACKOFF_S,
) -> T:
    """Retry callable on HTTP 429 / rate-limit errors with exponential backoff."""
    last_exc: BaseException | None = None
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if not _is_rate_limit_error(exc) or attempt >= max_retries - 1:
                raise
            time.sleep(base_backoff_s * (2**attempt))
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("call_with_retry exhausted without result")


def completion_limits(
    max_tokens: int | None = None,
    timeout: int | None = None,
    intent: str | None = None,
    model: str | None = None,
) -> tuple[int, int]:
    tokens = (
        max_tokens if max_tokens is not None else _int_env("PEM_MAX_TOKENS", DEFAULT_MAX_TOKENS)
    )
    seconds = (
        timeout if timeout is not None else _int_env("PEM_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
    )
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
    locale: str | None = None,
    skip_language_guard: bool = False,
    **kwargs: Any,
) -> str:
    _max_tokens, _timeout = completion_limits(max_tokens, timeout, intent=intent, model=model)
    extra = {key: value for key, value in kwargs.items() if key != "stream"}
    try:
        try:
            from .services.language_guard import (
                ensure_response_language,
                guard_messages,
                is_json_response_mode,
                resolve_request_locale,
            )
        except ImportError:
            from services.language_guard import (
                ensure_response_language,
                guard_messages,
                is_json_response_mode,
                resolve_request_locale,
            )

        skip_guard = skip_language_guard or is_json_response_mode(extra)
        payload = guard_messages(messages, locale=locale, skip=skip_guard)

        def _complete() -> Any:
            return litellm.completion(
                model=model,
                messages=payload,
                max_tokens=_max_tokens,
                timeout=_timeout,
                stream=False,
                **extra,
            )

        response = call_with_retry(_complete)
        choice = response.choices[0]
        try:
            from .services.model_utils import extract_litellm_response_text
        except ImportError:
            from services.model_utils import extract_litellm_response_text

        finish = getattr(choice, "finish_reason", None)
        finish_s = str(finish).strip() if finish is not None else None
        hit = (finish_s or "").lower().replace(" ", "_") in _LENGTH_REASONS
        _last_meta.set(
            CompletionMeta(finish_reason=finish_s, max_tokens=_max_tokens, hit_length=hit)
        )
        text = extract_litellm_response_text(response)
        if not skip_guard:
            effective = locale if locale is not None else resolve_request_locale()
            text = ensure_response_language(text, effective)
        return text
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
