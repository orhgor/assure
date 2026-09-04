"""Account sync, encrypted settings, and credit wallet. No live Send."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from prompt_matrix.i18n import CATALOGS, LOCALES, friendly_error

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase" / "migrations" / "20260901160000_credit_wallet.sql"
USAGE_KEYS = (
    "nav.usage",
    "usage.title",
    "usage.lead",
    "usage.balance",
    "usage.tier",
    "usage.transactions",
    "usage.none",
    "usage.date",
    "usage.type",
    "usage.amount",
    "usage.local",
    "error.credits",
)


class MigrationTests(unittest.TestCase):
    def test_clerk_text_ids_not_auth_users(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        self.assertIn("user_id TEXT PRIMARY KEY", sql)
        self.assertIn("spend_credit", sql)
        self.assertIn("add_wallet_credits", sql)
        self.assertNotIn("REFERENCES auth.users", sql)


class CatalogTests(unittest.TestCase):
    def test_usage_keys_in_every_locale(self):
        for locale in LOCALES:
            cat = CATALOGS[locale]
            for key in USAGE_KEYS:
                self.assertIn(key, cat, msg=f"{locale} {key}")
                self.assertTrue(str(cat[key]).strip(), msg=f"{locale} {key}")
        self.assertIn("Soru", CATALOGS["tr"]["usage.lead"])
        self.assertIn("Subscribe to Pro", CATALOGS["en"]["error.credits"])

    def test_friendly_credit_error(self):
        self.assertEqual(
            friendly_error("You have 0 credits. Subscribe to Pro to continue."),
            CATALOGS["en"]["error.credits"],
        )


class EncryptionTests(unittest.TestCase):
    def test_roundtrip(self):
        try:
            from cryptography.fernet import Fernet
        except ImportError:
            self.skipTest("cryptography is not installed")
        key = Fernet.generate_key().decode()
        with patch.dict(os.environ, {"ENCRYPTION_KEY": key}, clear=False):
            from prompt_matrix.encryption import decrypt_text, encrypt_text

            self.assertEqual(decrypt_text(encrypt_text("sk-test")), "sk-test")


class CreditGuardTests(unittest.TestCase):
    def test_skip_without_clerk(self):
        from prompt_matrix.credit_guard import assert_has_credit, check_and_deduct

        with patch.dict(os.environ, {"CLERK_PUBLISHABLE_KEY": "", "CLERK_SECRET_KEY": ""}, clear=False):
            assert_has_credit("user_abc")
            check_and_deduct("user_abc")

    def test_exhausted_message(self):
        from prompt_matrix.credit_guard import CREDIT_EXHAUSTED, CreditExhaustedError

        err = CreditExhaustedError()
        self.assertEqual(str(err), CREDIT_EXHAUSTED)
        self.assertIn("Subscribe to Pro", str(err))

    def test_assert_raises_when_balance_zero(self):
        from prompt_matrix.credit_guard import CreditExhaustedError, assert_has_credit

        wallet = {"user_id": "user_1", "balance": 0, "tier": "free"}
        sb = MagicMock()
        sb.request.return_value = [wallet]
        with patch("prompt_matrix.credit_guard.credits_enforced", return_value=True):
            with patch("prompt_matrix.credit_guard.active_user_id", return_value="user_1"):
                with patch("prompt_matrix.credit_guard._client", return_value=sb):
                    with patch("prompt_matrix.credit_guard.ensure_wallet", return_value=wallet):
                        with patch("prompt_matrix.credit_guard.get_wallet", return_value=wallet):
                            with self.assertRaises(CreditExhaustedError):
                                assert_has_credit("user_1")

    def test_deduct_calls_rpc(self):
        from prompt_matrix.credit_guard import check_and_deduct

        sb = MagicMock()
        sb.rpc.return_value = True
        with patch("prompt_matrix.credit_guard.credits_enforced", return_value=True):
            with patch("prompt_matrix.credit_guard.active_user_id", return_value="user_1"):
                with patch("prompt_matrix.credit_guard._client", return_value=sb):
                    check_and_deduct("user_1")
        sb.rpc.assert_called_once_with("spend_credit", {"p_user_id": "user_1", "p_cost": 1})

    def test_deduct_false_raises(self):
        from prompt_matrix.credit_guard import CreditExhaustedError, check_and_deduct

        sb = MagicMock()
        sb.rpc.return_value = False
        with patch("prompt_matrix.credit_guard.credits_enforced", return_value=True):
            with patch("prompt_matrix.credit_guard.active_user_id", return_value="user_1"):
                with patch("prompt_matrix.credit_guard._client", return_value=sb):
                    with self.assertRaises(CreditExhaustedError):
                        check_and_deduct("user_1")


class StripeWalletTests(unittest.TestCase):
    def test_checkout_adds_credits(self):
        from prompt_matrix.cloud_billing import apply_stripe_event

        with patch("prompt_matrix.cloud_billing.set_tier") as set_tier:
            with patch("prompt_matrix.credit_guard.apply_subscription") as add:
                apply_stripe_event(
                    {
                        "type": "checkout.session.completed",
                        "data": {
                            "object": {
                                "client_reference_id": "user_1",
                                "customer": "cus_1",
                            }
                        },
                    }
                )
        set_tier.assert_called_once()
        add.assert_called_once_with("user_1")


class WebUsageTests(unittest.TestCase):
    def test_compose_open_without_clerk(self):
        from prompt_matrix.web import create_app

        with patch.dict(
            os.environ,
            {"CLERK_PUBLISHABLE_KEY": "", "CLERK_SECRET_KEY": "", "PEM_HTTP_PASS": ""},
            clear=False,
        ):
            app = create_app(require_auth=False)
            with app.test_client() as client:
                res = client.get("/app")
        self.assertEqual(res.status_code, 200)
        self.assertNotIn("/signin", (res.headers.get("Location") or ""))

    def test_landing_public_without_clerk(self):
        from prompt_matrix.web import create_app

        with patch.dict(
            os.environ,
            {"CLERK_PUBLISHABLE_KEY": "", "CLERK_SECRET_KEY": "", "PEM_HTTP_PASS": ""},
            clear=False,
        ):
            app = create_app(require_auth=False)
            with app.test_client() as client:
                res = client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Intellectual Compiler", res.data)
        self.assertIn(b"Deterministic Document Execution", res.data)
        self.assertNotIn(b"logo-tagline", res.data)

    def test_landing_turkish_brand_option2(self):
        from prompt_matrix.web import create_app

        with patch.dict(
            os.environ,
            {"CLERK_PUBLISHABLE_KEY": "", "CLERK_SECRET_KEY": "", "PEM_HTTP_PASS": ""},
            clear=False,
        ):
            app = create_app(require_auth=False)
            with app.test_client() as client:
                res = client.get("/?lang=tr")
        self.assertEqual(res.status_code, 200)
        self.assertIn("Zihinsel Derleyici".encode(), res.data)
        self.assertIn("Deterministik Belge Yürütme".encode(), res.data)
        self.assertNotIn(b"The Intellectual Compiler", res.data)
        self.assertNotIn(b"logo-tagline", res.data)

    def test_workbench_header_assure_only(self):
        from prompt_matrix.web import create_app

        with patch.dict(
            os.environ,
            {"CLERK_PUBLISHABLE_KEY": "", "CLERK_SECRET_KEY": "", "PEM_HTTP_PASS": ""},
            clear=False,
        ):
            app = create_app(require_auth=False)
            with app.test_client() as client:
                res = client.get("/app?lang=tr")
        self.assertEqual(res.status_code, 200)
        self.assertNotIn(b"app-logo-tagline", res.data)
        self.assertNotIn(b"brand-tagline", res.data)

    def test_compose_login_wall_with_clerk(self):
        from prompt_matrix.web import create_app

        with patch.dict(
            os.environ,
            {
                "CLERK_PUBLISHABLE_KEY": "pk_test_assure",
                "CLERK_SECRET_KEY": "sk_test_assure",
                "PEM_HTTP_PASS": "",
            },
            clear=False,
        ):
            app = create_app(require_auth=False)
            with app.test_client() as client:
                res = client.get("/app", follow_redirects=False)
        self.assertEqual(res.status_code, 302)
        self.assertIn("/signin", res.headers.get("Location") or "")

    def test_usage_open_without_clerk(self):
        from prompt_matrix.web import create_app

        with patch.dict(
            os.environ,
            {"CLERK_PUBLISHABLE_KEY": "", "CLERK_SECRET_KEY": "", "PEM_HTTP_PASS": ""},
            clear=False,
        ):
            app = create_app(require_auth=False)
            with app.test_client() as client:
                res = client.get("/account/usage")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"data-i18n=\"usage.title\"", res.data)

    def test_settings_get_without_clerk(self):
        from prompt_matrix.web import create_app

        with patch.dict(
            os.environ,
            {"CLERK_PUBLISHABLE_KEY": "", "CLERK_SECRET_KEY": "", "PEM_HTTP_PASS": ""},
            clear=False,
        ):
            app = create_app(require_auth=False)
            with app.test_client() as client:
                res = client.get("/api/settings")
        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertEqual(body.get("api_keys"), {})
        self.assertEqual(body.get("preferences"), {})

    def test_health_stays_public(self):
        from prompt_matrix.web import create_app

        app = create_app(require_auth=False)
        with app.test_client() as client:
            res = client.get("/api/health")
        self.assertEqual(res.status_code, 200)

    def test_nav_usage_only_when_signed_in(self):
        html = (ROOT / "prompt_matrix" / "templates" / "base.html").read_text(encoding="utf-8")
        self.assertIn('data-i18n="nav.usage"', html)
        self.assertIn("auth.signed_in", html)
        self.assertIn("/account/usage", html)

    def test_no_official_supabase_sdk(self):
        client_src = (ROOT / "prompt_matrix" / "supabase_client.py").read_text(encoding="utf-8")
        self.assertNotIn("from supabase import", client_src)
        self.assertNotIn("create_client", client_src)


if __name__ == "__main__":
    unittest.main()
