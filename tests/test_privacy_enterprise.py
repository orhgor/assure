"""Tests for enterprise privacy policy content."""

from __future__ import annotations

import unittest

from prompt_matrix.i18n import LOCALES, catalog
from prompt_matrix.privacy_enterprise import privacy_body_html


class PrivacyEnterpriseTests(unittest.TestCase):
    def test_all_locales_have_body_html(self) -> None:
        for loc in LOCALES:
            body = privacy_body_html(loc)
            self.assertIn('id="privacy-s1"', body)
            self.assertIn('id="privacy-s12"', body)
            self.assertIn("privacy@getassureai.com", body)

    def test_catalog_includes_body_html(self) -> None:
        strings = catalog("en")
        self.assertIn("privacy.enterprise.body_html", strings)
        self.assertIn("What Data We Collect", strings["privacy.enterprise.body_html"])

    def test_no_training_claim_en(self) -> None:
        body = privacy_body_html("en").lower()
        self.assertIn("train", body)
        self.assertIn("do not", body)


if __name__ == "__main__":
    unittest.main()
