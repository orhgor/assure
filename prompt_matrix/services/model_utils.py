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


_REASONING_KEYS = ("reasoning_content", "reasoning", "reasoning_text")


def extract_choice_reasoning(choice: Any) -> str:
    """A completion choice's reasoning channel, or ``""`` when it has none.

    Reasoning is not an answer. A provider that exposes it (DeepSeek-R1 and
    others) puts the chain of thought here and the answer in ``content``; the
    two are read apart so a caller can never mistake the first for the second.
    """
    for source in (_field(choice, "message"), _field(choice, "delta"), choice):
        if source is None:
            continue
        for key in _REASONING_KEYS:
            reasoning = _as_str(_field(source, key))
            if reasoning:
                return reasoning
    return ""


def extract_choice_text(choice: Any, *, usage: Any | None = None) -> str:
    """The assistant's answer body from a completion choice — never its reasoning.

    The reasoning channel used to be the fallback here, which meant a
    reasoning model whose answer was cut off at the output ceiling handed its
    scratchpad to the caller as if it were the review (measured 2026-09-19:
    a 31,418-char chain of thought was persisted as a Red-Hat finding). A
    completion with no answer body says so instead.
    """
    for source in (_field(choice, "message"), _field(choice, "delta"), choice):
        if source is None:
            continue
        content = _as_str(_field(source, "content"))
        if content:
            return content

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
