"""Provider key helpers. Unittest only. No live API calls."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from prompt_matrix.keys import (
    ORCHESTRATOR_ENV_MAP,
    ORCHESTRATOR_ENV_MAP_KEYS,
    anthropic_workspace_id,
    key_present,
    litellm_kwargs_for,
    provider_slug_for_litellm,
)
from prompt_matrix.llm import orchestrator as orch_mod
from prompt_matrix.web import create_app


class ClaudeKeyTests(unittest.TestCase):
    def test_claude_alias_is_enough(self):
        with patch.dict(
            os.environ, {"ANTHROPIC_API_KEY": "", "CLAUDE_API_KEY": "sk-ant-test"}, clear=False
        ):
            self.assertTrue(key_present("claude"))

    def test_workspace_header_on_claude_kwargs(self):
        with patch.dict(
            os.environ,
            {
                "ANTHROPIC_API_KEY": "sk-ant-test",
                "ANTHROPIC_WORKSPACE_ID": "wrkspc_test",
            },
            clear=False,
        ):
            extra = litellm_kwargs_for("claude")
            self.assertEqual(extra.get("api_key"), "sk-ant-test")
            self.assertEqual(
                extra.get("extra_headers"),
                {"anthropic-workspace-id": "wrkspc_test"},
            )
            self.assertEqual(anthropic_workspace_id(), "wrkspc_test")

    def test_workspace_header_omitted_when_unset(self):
        with patch.dict(
            os.environ,
            {
                "ANTHROPIC_API_KEY": "sk-ant-test",
                "ANTHROPIC_WORKSPACE_ID": "",
                "ANTHROPIC_WORKSPACE": "",
            },
            clear=False,
        ):
            extra = litellm_kwargs_for("claude")
            self.assertNotIn("extra_headers", extra)

    def test_connect_works_when_clerk_is_on(self):
        with patch.dict(
            os.environ,
            {
                "CLERK_PUBLISHABLE_KEY": "pk_test_x",
                "CLERK_SECRET_KEY": "sk_test_x",
                "PEM_HTTP_PASS": "changeme",
            },
            clear=False,
        ):
            app = create_app(require_auth=False)
            client = app.test_client()
            page = client.get("/connect")
            self.assertEqual(page.status_code, 200)
            self.assertIn("Claude", page.get_data(as_text=True))
            status = client.get("/api/status")
            self.assertEqual(status.status_code, 200)
            compose = client.get("/app")
            self.assertEqual(compose.status_code, 302)
            self.assertIn("/signin", compose.headers.get("Location", ""))


class ProviderSlugForLitellmTests(unittest.TestCase):
    def test_openrouter_uses_first_segment_not_vendor(self):
        self.assertEqual(
            provider_slug_for_litellm("openrouter/anthropic/claude-3.5-sonnet"),
            "openrouter",
        )
        self.assertEqual(
            provider_slug_for_litellm("openrouter/nvidia/llama-3.3-70b-instruct:free"),
            "openrouter",
        )

    def test_provider_prefixed_models(self):
        self.assertEqual(
            provider_slug_for_litellm("anthropic/claude-3-5-sonnet"),
            "claude",
        )

    def test_bare_and_unknown(self):
        self.assertIsNone(provider_slug_for_litellm("gpt-4o"))
        # ollama is a known provider since 2026-09-23 (ASSURE_LLM_BACKEND=ollama
        # routes every task to the local container); it needs no key.
        self.assertEqual(provider_slug_for_litellm("ollama/llama3"), "ollama")
        self.assertIsNone(provider_slug_for_litellm("totally-unknown-model"))
        self.assertIsNone(provider_slug_for_litellm(""))
        self.assertIsNone(provider_slug_for_litellm(None))

    def test_returned_slugs_match_orchestrator_env_map(self):
        # Prove orchestrator consumes the shared map (not a divergent local dict).
        self.assertIs(orch_mod.ORCHESTRATOR_ENV_MAP, ORCHESTRATOR_ENV_MAP)
        self.assertEqual(set(ORCHESTRATOR_ENV_MAP), ORCHESTRATOR_ENV_MAP_KEYS)

        samples = (
            "openrouter/anthropic/claude-3.5-sonnet",
            "openrouter/nvidia/llama-3.3-70b-instruct:free",
            "anthropic/claude-3-5-sonnet",
            "gemini/gemini-1.5-flash",
            "google/gemini-1.5-pro",
            "openrouter/qwen/qwen3-next-80b-a3b-instruct",
            "groq/llama-3.3-70b-versatile",
            "claude-3-5-sonnet",
            "gemini-1.5-flash",
        )
        for model in samples:
            slug = provider_slug_for_litellm(model)
            if slug is None:
                continue
            self.assertIn(slug, orch_mod.ORCHESTRATOR_ENV_MAP)
            self.assertTrue(slug in ORCHESTRATOR_ENV_MAP_KEYS or slug == "openrouter")

    def test_api_key_env_attaches_openrouter_and_claude(self):
        with patch.dict(
            os.environ,
            {
                "OPENROUTER_API_KEY": "sk-or-test",
                "ANTHROPIC_API_KEY": "sk-ant-test",
            },
            clear=False,
        ):
            self.assertEqual(
                orch_mod._api_key_env_for_model("openrouter/nvidia/llama-3.1-70b"),
                "sk-or-test",
            )
            self.assertEqual(
                orch_mod._api_key_env_for_model("anthropic/claude-3-5-sonnet"),
                "sk-ant-test",
            )
            params = orch_mod._litellm_params_for("openrouter/nvidia/llama-3.1-70b")
            self.assertEqual(params.get("api_key"), "sk-or-test")
            self.assertIn("extra_headers", params)


if __name__ == "__main__":
    unittest.main()
