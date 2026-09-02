"""Intent heuristic for Compose auto-detect. No live Send."""

from __future__ import annotations

import unittest

from prompt_matrix.intent_detector import FALLBACK, INTENTS, detect_intent


class DetectIntentTests(unittest.TestCase):
    def test_fallback_on_empty(self):
        self.assertEqual(detect_intent(""), FALLBACK)
        self.assertEqual(detect_intent(None), FALLBACK)
        self.assertEqual(detect_intent("   "), FALLBACK)

    def test_ids_stay_catalog(self):
        for name in INTENTS:
            self.assertIn(name, ("research", "design", "comparison", "debug", "analysis"))

    def test_comparison(self):
        self.assertEqual(detect_intent("Compare AWS vs GCP for a healthcare client"), "comparison")

    def test_debug(self):
        self.assertEqual(detect_intent("The traceback says TypeError on line 12"), "debug")

    def test_design(self):
        self.assertEqual(detect_intent("Design a layout for the checkout wireframe"), "design")

    def test_analysis(self):
        self.assertEqual(detect_intent("Analyze the metrics trend in this spreadsheet"), "analysis")

    def test_research_keywords_and_default(self):
        self.assertEqual(detect_intent("What is the literature on this paper"), "research")
        self.assertEqual(detect_intent("Write a marketing email"), FALLBACK)
