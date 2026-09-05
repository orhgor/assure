"""Tests for LiteLLM response text extraction."""

from __future__ import annotations

import unittest

from prompt_matrix.services.model_utils import extract_choice_text, extract_litellm_response_text


class ModelUtilsTests(unittest.TestCase):
    def test_standard_content(self):
        choice = {"message": {"content": "  Critique here.  "}}
        self.assertEqual(extract_choice_text(choice), "Critique here.")

    def test_reasoning_content_fallback(self):
        choice = {
            "message": {
                "content": "",
                "reasoning_content": "Step 1: check claims.",
            }
        }
        self.assertEqual(extract_choice_text(choice), "Step 1: check claims.")

    def test_delta_stream_shape(self):
        choice = {"delta": {"reasoning_content": "partial"}}
        self.assertEqual(extract_choice_text(choice), "partial")

    def test_finish_reason_fallback(self):
        choice = {"message": {"content": ""}, "finish_reason": "length"}
        text = extract_choice_text(choice, usage={"prompt_tokens": 490})
        self.assertIn("length", text)
        self.assertIn("490", text)

    def test_extract_litellm_response_text(self):
        resp = {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 10},
        }
        self.assertEqual(extract_litellm_response_text(resp), "ok")


if __name__ == "__main__":
    unittest.main()
