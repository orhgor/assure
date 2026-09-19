"""Tests for LiteLLM response text extraction."""

from __future__ import annotations

import unittest

from prompt_matrix.services.model_utils import (
    extract_choice_reasoning,
    extract_choice_text,
    extract_litellm_response_text,
)


class ModelUtilsTests(unittest.TestCase):
    def test_standard_content(self):
        choice = {"message": {"content": "  Critique here.  "}}
        self.assertEqual(extract_choice_text(choice), "Critique here.")

    def test_reasoning_is_read_apart_from_the_answer(self):
        """A reasoning model's chain of thought is not its answer. Reading it as
        one persisted a 31,418-char scratchpad as a Red-Hat finding (measured
        2026-09-19), so the answer body comes back empty here and says why."""
        choice = {
            "message": {
                "content": "",
                "reasoning_content": "We need answer user asks: check the claim.",
            },
            "finish_reason": "length",
        }
        text = extract_choice_text(choice, usage={"prompt_tokens": 732})
        self.assertNotIn("We need answer", text)
        self.assertIn("length", text)
        # The reasoning is still reachable, just never as the answer.
        self.assertEqual(
            extract_choice_reasoning(choice), "We need answer user asks: check the claim."
        )

    def test_reasoning_alone_is_not_a_body(self):
        """Mid-stream, a reasoning-only delta has no answer body yet."""
        self.assertEqual(extract_choice_text({"delta": {"reasoning_content": "partial"}}), "")
        self.assertEqual(extract_choice_text({"delta": {"content": "partial answer"}}), "partial answer")

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

    def test_extract_litellm_response_text_never_returns_reasoning(self):
        resp = {
            "choices": [
                {
                    "message": {"content": "", "reasoning_content": "hmm, let me think"},
                    "finish_reason": "length",
                }
            ],
            "usage": {"prompt_tokens": 10},
        }
        self.assertNotIn("let me think", extract_litellm_response_text(resp))


if __name__ == "__main__":
    unittest.main()
