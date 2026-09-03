"""Provider key helpers. Unittest only. No live API calls."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from prompt_matrix.keys import anthropic_workspace_id, key_present, litellm_kwargs_for
from prompt_matrix.web import create_app


class ClaudeKeyTests(unittest.TestCase):
    def test_claude_alias_is_enough(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "", "CLAUDE_API_KEY": "sk-ant-test"}, clear=False):
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
            {"ANTHROPIC_API_KEY": "sk-ant-test", "ANTHROPIC_WORKSPACE_ID": "", "ANTHROPIC_WORKSPACE": ""},
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


if __name__ == "__main__":
    unittest.main()
