"""A compile with no provider key is refused before the first token, by name.

Observed on the local stack 2026-09-22 (OPENROUTER_API_KEY empty): the stream
announced "Drafting with openrouter/…" and then surfaced litellm's raw
"AuthenticationError: OpenrouterException - No cookie auth credentials found".
``_stream_model`` now checks the key first and yields one error frame that names
the variable to set (424, reason ``provider_key_missing``).
"""

from __future__ import annotations

import json

import pytest

from prompt_matrix.routers import draft as draft_mod


def _frames(gen):
    out = []
    for item in gen:
        out.append(item)
    return out


@pytest.fixture
def no_keys(monkeypatch):
    for name in (
        "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "CLAUDE_API_KEY",
        "GEMINI_API_KEY", "GOOGLE_API_KEY", "GROQ_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("prompt_matrix.keys._browser_env", lambda _n: None)
    monkeypatch.setattr("prompt_matrix.keys._cloud_env", lambda _n: None)


class _Policy:
    max_output_tokens = 256


class _Gov:
    def policy_for(self, *_a, **_k):
        return _Policy()

    def preflight(self, *_a, **_k):
        return None


def test_missing_provider_key_is_one_named_error_frame(no_keys):
    frames = _frames(
        draft_mod._stream_model(
            _Gov(),
            [{"role": "user", "content": "hi"}],
            target_ai="openrouter/qwen/qwen3-next-80b-a3b-instruct",
        )
    )
    assert len(frames) == 1, frames
    assert frames[0].startswith("event: error")
    payload = json.loads(frames[0].split("data: ", 1)[1].strip())
    assert payload["ok"] is False
    assert payload["http_status"] == 424
    assert payload["reason"] == "provider_key_missing"
    assert payload["provider"] == "openrouter"
    assert "OPENROUTER_API_KEY" in payload["error"]


def test_present_key_reaches_the_model_call(no_keys, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

    class _Boom(Exception):
        pass

    def fake_completion(**_kwargs):
        raise _Boom("reached litellm.completion")

    import litellm

    monkeypatch.setattr(litellm, "completion", fake_completion)
    frames = _frames(
        draft_mod._stream_model(
            _Gov(),
            [{"role": "user", "content": "hi"}],
            target_ai="openrouter/qwen/qwen3-next-80b-a3b-instruct",
        )
    )
    # The key check passed and the call was attempted; the fake failure is
    # reported as the stream's own error frame, not swallowed.
    assert any('"type": "error"' in f for f in frames if isinstance(f, str))
    assert not any("provider_key_missing" in f for f in frames if isinstance(f, str))
