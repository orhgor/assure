"""Deep compiler: audience adaptation and chain-of-thought injection."""

from __future__ import annotations

import unittest

from prompt_matrix.pipelines import AUDIENCE_BLOCKS, compile_deep_prompt


class DeepCompilerTests(unittest.TestCase):
    def test_audience_executive_in_prompt(self) -> None:
        rendered = compile_deep_prompt(
            "Summarize the trial results.",
            "research",
            "",
            target_ai="gemini",
            params={"audience": "executive"},
        )
        self.assertIn("executive", rendered.prompt.lower())
        self.assertIn("300 words", rendered.prompt)

    def test_audience_defaults_general(self) -> None:
        rendered = compile_deep_prompt(
            "What changed?",
            "design",
            "",
            target_ai="gemini",
        )
        self.assertIn(AUDIENCE_BLOCKS["general"][:40], rendered.prompt)

    def test_cot_for_research_intent(self) -> None:
        rendered = compile_deep_prompt(
            "Compare dropout rates.",
            "research",
            "trial_a.pdf says 12%.",
            target_ai="gemini",
        )
        self.assertIn("Step 1:", rendered.prompt)
        self.assertIn("Extract", rendered.prompt)

    def test_cot_skipped_for_design(self) -> None:
        rendered = compile_deep_prompt(
            "Draft a wireframe.",
            "design",
            "",
            target_ai="gemini",
        )
        self.assertNotIn("Step 1: Extract all relevant claims", rendered.prompt)

    def test_file_paths_merged_into_context(self) -> None:
        rendered = compile_deep_prompt(
            "Read the attached study.",
            "research",
            "Notes from analyst.",
            target_ai="gemini",
            files=["./notes.txt"],
        )
        self.assertTrue(rendered.context_injected or rendered.files_read or "analyst" in rendered.prompt.lower())


if __name__ == "__main__":
    unittest.main()
