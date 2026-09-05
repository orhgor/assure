"""Helpers for parsing LiteLLM / OpenAI-compatible completion responses."""

from __future__ import annotations

from typing import Any


def _field(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def extract_choice_text(choice: Any, *, usage: Any | None = None) -> str:
    """Extract assistant text from a completion choice (incl. DeepSeek Reasoner)."""
    message = _field(choice, "message")
    delta = _field(choice, "delta")

    for source in (message, delta, choice):
        if source is None:
            continue
        content = _as_str(_field(source, "content"))
        if content:
            return content
        for key in ("reasoning_content", "reasoning", "reasoning_text"):
            reasoning = _as_str(_field(source, key))
            if reasoning:
                return reasoning

    prompt_tokens = 0
    if usage is not None:
        prompt_tokens = int(_field(usage, "prompt_tokens") or _field(usage, "input_tokens") or 0)
    finish = _as_str(_field(choice, "finish_reason"))
    if finish:
        return (
            f"Red-hat model finished with {finish!r} but returned no text body. "
            f"Input tokens: {prompt_tokens}."
        )
    return ""


def extract_litellm_response_text(response: Any) -> str:
    """Best-effort text from a LiteLLM completion response object."""
    choices = _field(response, "choices") or []
    if not choices:
        return ""
    usage = _field(response, "usage")
    return extract_choice_text(choices[0], usage=usage)
