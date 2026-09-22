"""``/api/compile-system`` shows the system turn the compile actually sends.

The endpoint used to render ``_compile_system(shape, intent)`` — no ICP profile,
and before the per-request language guard ``_stream_model`` appends — so the
shell's left pane could differ from the message the model received for the same
ask. The proof here captures the ``messages`` handed to ``litellm.completion`` by
the real pipeline and compares them, byte for byte, with the endpoint's answer for
the same intent, profile and locale.
"""

from __future__ import annotations

import pytest


class _FakeGovernor:
    class _Acct:
        def count_messages(self, _):
            return 10

        def count(self, text):
            return max(1, len(text) // 4)

    accountant = _Acct()

    def preflight(self, *_a, **_k):
        return None

    def policy_for(self, _task):
        class P:
            litellm_model = "anthropic/claude-3-5-sonnet-20241022"
            max_output_tokens = 256
            model_id = "anthropic/claude-3-5-sonnet-20241022"

        return P()

    def record_usage(self, *_a, **_k):
        return None


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "parity.db"))
    monkeypatch.setenv("PEM_OMP_CACHE", "0")
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db
    from prompt_matrix.web import create_app

    init_db()
    return create_app(require_auth=False).test_client()


SOURCE = "The policy liability limit is set at $5,000,000 for combined single limit."
ASK = "What is the policy liability limit for the combined single limit?"


def _system_turn_sent(monkeypatch, *, icp_profile: str, locale: str) -> str:
    """Run the compile pipeline up to the model call and return the system turn."""
    import litellm

    from prompt_matrix.routers.draft import run_draft_pipeline
    from prompt_matrix.services.language_guard import set_request_locale

    captured: dict = {}

    def fake_completion(**kwargs):
        captured["messages"] = kwargs["messages"]
        raise RuntimeError("captured; no model call in this test")

    monkeypatch.setattr(litellm, "completion", fake_completion)
    # The key preflight in _stream_model runs before the call; a key is "present".
    monkeypatch.setattr("prompt_matrix.keys.api_key_for", lambda _slug: "test-key")
    monkeypatch.setattr(
        "prompt_matrix.routers.draft.fetch_substrate_entries_by_ids",
        lambda _pid, _ids: [{"id": "sub-1", "filename": "policy.pdf", "extracted_text": SOURCE}],
    )
    set_request_locale(locale)
    try:
        list(
            run_draft_pipeline(
                "default",
                intent=ASK,
                substrate_file_ids=["sub-1"],
                governor=_FakeGovernor(),
                icp_profile=icp_profile,
            )
        )
    finally:
        set_request_locale(None)
    assert captured.get("messages"), "the pipeline never reached the model call"
    system = [m for m in captured["messages"] if m.get("role") == "system"]
    assert len(system) == 1
    return str(system[0]["content"])


def test_compile_system_endpoint_is_byte_equal_to_the_system_turn_sent(client, monkeypatch):
    from prompt_matrix.services.icp_profiles import PROFILES

    profile = sorted(PROFILES)[-1]
    sent = _system_turn_sent(monkeypatch, icp_profile=profile, locale="tr")

    res = client.post(
        "/api/compile-system",
        json={"intent": ASK, "icp_profile": profile, "locale": "tr", "project_id": "default"},
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["prompt"] == sent
    assert body["icp_profile"] == profile and body["locale"] == "tr"
    # The parts the old endpoint left out are what the model reads.
    assert "HARD LANGUAGE RULE" in body["prompt"]
    assert ASK in body["prompt"]

    # The same inputs through the alias the compile payload accepts, as a GET.
    res = client.get(
        "/api/compile-system",
        query_string={"intent": ASK, "icpProfile": profile, "lang": "tr"},
    )
    assert res.get_json()["prompt"] == sent


def test_compile_system_endpoint_differs_when_profile_or_locale_differ(client):
    """A different audience or language is a different system turn — the pane must move."""
    from prompt_matrix.services.icp_profiles import PROFILES

    profiles = sorted(PROFILES)
    base = client.post("/api/compile-system", json={"intent": ASK, "locale": "en"}).get_json()
    other_locale = client.post("/api/compile-system", json={"intent": ASK, "locale": "de"}).get_json()
    assert base["prompt"] != other_locale["prompt"]
    if len(profiles) > 1:
        a = client.post(
            "/api/compile-system", json={"intent": ASK, "icp_profile": profiles[0]}
        ).get_json()
        b = client.post(
            "/api/compile-system", json={"intent": ASK, "icp_profile": profiles[-1]}
        ).get_json()
        assert a["prompt"] != b["prompt"]

    # No ask: the static message, unchanged (the shell's empty-pane state).
    from prompt_matrix.routers.draft import _COMPILE_SYSTEM

    assert client.post("/api/compile-system", json={}).get_json() == {"prompt": _COMPILE_SYSTEM}
