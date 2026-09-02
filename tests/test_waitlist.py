"""Waitlist API tests. Unittest only. No live network."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from prompt_matrix.waitlist import DuplicateWaitlistError, WaitlistUnavailableError


class WaitlistRouteTests(unittest.TestCase):
    def setUp(self):
        self._clerk = patch.dict(
            "os.environ",
            {
                "CLERK_PUBLISHABLE_KEY": "",
                "CLERK_SECRET_KEY": "",
                "PEM_HTTP_PASS": "changeme",
                "PEM_SECRET_KEY": "test-secret",
                "SUPABASE_URL": "",
                "SUPABASE_ANON_KEY": "",
                "SUPABASE_SERVICE_ROLE_KEY": "",
            },
            clear=False,
        )
        self._clerk.start()
        from prompt_matrix.web import create_app

        self.app = create_app(require_auth=False)
        self.client = self.app.test_client()

    def tearDown(self):
        self._clerk.stop()

    def _post_waitlist(self, payload):
        return self.client.post(
            "/api/waitlist",
            data=json.dumps(payload) if payload is not None else "",
            content_type="application/json",
        )

    def _test_bad_input(self, payload, error_fragment):
        res = self._post_waitlist(payload)
        self.assertEqual(res.status_code, 400)
        body = res.get_json()
        self.assertIn("error", body)
        self.assertIn(error_fragment, body["error"])

    def test_empty_name_is_400(self):
        self._test_bad_input({"name": "   ", "email": "a@b.com"}, "name")

    def test_missing_name_is_400(self):
        self._test_bad_input({"email": "a@b.com"}, "name")

    def test_bad_email_is_400(self):
        self._test_bad_input({"name": "Ada", "email": "not-an-email"}, "email")

    def test_missing_email_is_400(self):
        self._test_bad_input({"name": "Ada"}, "email")

    def test_ok_inserts_row(self):
        with patch("prompt_matrix.web.insert_waitlist") as mock_insert:
            mock_insert.return_value = True
            res = self._post_waitlist({"name": "Ada", "email": "ada@example.com"})
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.get_json(), {"status": "ok"})
            mock_insert.assert_called_once_with("Ada", "ada@example.com")

    def test_duplicate_is_ok(self):
        with patch("prompt_matrix.web.insert_waitlist") as mock_insert:
            mock_insert.side_effect = DuplicateWaitlistError()
            res = self._post_waitlist({"name": "Ada", "email": "ada@example.com"})
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.get_json(), {"status": "ok"})

    def test_supabase_off_is_503(self):
        with patch("prompt_matrix.web.insert_waitlist") as mock_insert:
            mock_insert.side_effect = WaitlistUnavailableError()
            res = self._post_waitlist({"name": "Ada", "email": "ada@example.com"})
            self.assertEqual(res.status_code, 503)
            body = res.get_json()
            self.assertIn("error", body)
            self.assertIn("try again", body["error"].lower())

    def test_options_cors_from_landing(self):
        res = self.client.options(
            "/api/waitlist",
            headers={"Origin": "https://getassureai.com"},
        )
        self.assertEqual(res.status_code, 204)
        self.assertEqual(res.headers.get("Access-Control-Allow-Origin"), "https://getassureai.com")
        self.assertIn("POST", res.headers.get("Access-Control-Allow-Methods", ""))

    def test_post_cors_from_local_preview(self):
        with patch("prompt_matrix.web.insert_waitlist") as mock_insert:
            mock_insert.return_value = True
            res = self.client.post(
                "/api/waitlist",
                data=json.dumps({"name": "Ada", "email": "ada@example.com"}),
                content_type="application/json",
                headers={"Origin": "http://127.0.0.1:5500"},
            )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers.get("Access-Control-Allow-Origin"), "http://127.0.0.1:5500")

    def test_waitlist_is_public_when_clerk_is_on(self):
        with patch.dict(
            "os.environ",
            {
                "CLERK_PUBLISHABLE_KEY": "pk_test_x",
                "CLERK_SECRET_KEY": "sk_test_x",
                "PEM_HTTP_PASS": "changeme",
            },
            clear=False,
        ):
            from prompt_matrix.web import create_app

            client = create_app(require_auth=False).test_client()
            with patch("prompt_matrix.web.insert_waitlist") as mock_insert:
                mock_insert.return_value = True
                res = client.post(
                    "/api/waitlist",
                    data=json.dumps({"name": "Ada", "email": "ada@example.com"}),
                    content_type="application/json",
                )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json(), {"status": "ok"})


class WaitlistSupabaseKeyTests(unittest.TestCase):
    def test_secret_key_alias_is_enough(self):
        from prompt_matrix.cloud_billing import supabase_configured, supabase_key

        with patch.dict(
            "os.environ",
            {
                "SUPABASE_URL": "https://example.supabase.co",
                "SUPABASE_SECRET_KEY": "sb_secret_test",
                "SUPABASE_SERVICE_ROLE_KEY": "",
                "SUPABASE_PUBLISHABLE_KEY": "",
                "SUPABASE_ANON_KEY": "",
            },
            clear=False,
        ):
            self.assertEqual(supabase_key(), "sb_secret_test")
            self.assertTrue(supabase_configured())


class WaitlistHelpersTests(unittest.TestCase):
    def test_email_validation_accepts_basic(self):
        from prompt_matrix.web import _is_valid_email

        self.assertTrue(_is_valid_email("a@b.co"))
        self.assertTrue(_is_valid_email("ada.lovelace@example-domain.com"))
        self.assertTrue(_is_valid_email("user+tag@sub.example.net"))

    def test_email_validation_rejects_basic(self):
        from prompt_matrix.web import _is_valid_email

        self.assertFalse(_is_valid_email(""))
        self.assertFalse(_is_valid_email("plainaddress"))
        self.assertFalse(_is_valid_email("@missing-local.com"))
        self.assertFalse(_is_valid_email("missing-at.com"))
        self.assertFalse(_is_valid_email("spaces in@email.com"))
        self.assertFalse(_is_valid_email("a@b"))


if __name__ == "__main__":
    unittest.main()
