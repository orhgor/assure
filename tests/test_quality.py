"""confidence_text is never empty."""

from __future__ import annotations

import unittest

from prompt_matrix.quality import PENDING, audit_spans, confidence_text, models_from_steps


class ConfidenceTextTests(unittest.TestCase):
    def test_pending_when_quality_missing(self):
        self.assertEqual(confidence_text(quality=None, models=["gemini"]), PENDING)
        self.assertEqual(confidence_text(quality={}, models=["gemini"]), PENDING)

    def test_ensemble_sentence(self):
        text = confidence_text(
            quality={"consensus_score": 0.8, "flagged_count": 2, "draft_count": 2},
            models=["gemini", "claude"],
        )
        self.assertEqual(
            text,
            "Gemini and Claude agree on 80%. 2 claims were flagged as unsupported.",
        )

    def test_single_model_sentence(self):
        text = confidence_text(
            quality={"consensus_score": None, "flagged_count": 1, "draft_count": 1},
            models=["claude"],
        )
        self.assertEqual(
            text,
            "Answer from Claude. 1 claims were checked against your files.",
        )

    def test_never_empty(self):
        self.assertTrue(confidence_text(quality=None).strip())
        self.assertTrue(confidence_text(quality={"flagged_count": 0}, models=[]).strip())

    def test_models_from_steps(self):
        class Step:
            def __init__(self, name, target_ai):
                self.name = name
                self.target_ai = target_ai

        names = models_from_steps(
            [Step("draft:gemini", "gemini"), Step("draft:kimi", "kimi")],
            "gemini",
        )
        self.assertEqual(names, ["gemini", "kimi"])


class AuditSpanTests(unittest.TestCase):
    def test_empty_without_context(self):
        spans = audit_spans("The dropout rate is 12%.", "")
        self.assertEqual(spans["grounded_spans"], [])
        self.assertEqual(spans["inferred_spans"], [])

    def test_verified_section_is_grounded(self):
        reply = (
            "**VERIFIED FINDINGS (Directly from uploaded context):**\n"
            "- The dropout rate is 12%.\n"
            "\n"
            "**INFERRED GAPS (Logical inference; requires manual validation):**\n"
            "- The trial may need a larger sample.\n"
        )
        context = "Clinical trial A dropout rate is 12% versus 8% for B."
        spans = audit_spans(reply, context)
        grounded_text = [reply[s["start"] : s["end"]] for s in spans["grounded_spans"]]
        inferred_text = [reply[s["start"] : s["end"]] for s in spans["inferred_spans"]]
        self.assertTrue(any("12%" in line for line in grounded_text))
        self.assertTrue(any("larger sample" in line for line in inferred_text))
        heading_hits = [
            reply[s["start"] : s["end"]] for s in spans["grounded_spans"] + spans["inferred_spans"]
        ]
        self.assertFalse(any("VERIFIED FINDINGS" in line for line in heading_hits))
        self.assertFalse(any("INFERRED GAPS" in line for line in heading_hits))

    def test_verified_heading_does_not_force_unrelated(self):
        reply = "**VERIFIED FINDINGS:**\n" "- Quantum widgets will triple next quarter.\n"
        context = "Clinical trial A dropout rate is 12% versus 8% for B."
        spans = audit_spans(reply, context)
        grounded_text = [reply[s["start"] : s["end"]] for s in spans["grounded_spans"]]
        inferred_text = [reply[s["start"] : s["end"]] for s in spans["inferred_spans"]]
        self.assertFalse(any("Quantum" in line for line in grounded_text))
        self.assertTrue(any("Quantum" in line for line in inferred_text))

    def test_json_answer_is_highlighted(self):
        reply = '{"headline": "Trial", "answer": "The dropout rate is 12%.\\nThis is a guess about next year."}'
        context = "The dropout rate is 12% in trial A."
        spans = audit_spans(reply, context)
        body = "The dropout rate is 12%.\nThis is a guess about next year."
        grounded_text = [body[s["start"] : s["end"]] for s in spans["grounded_spans"]]
        inferred_text = [body[s["start"] : s["end"]] for s in spans["inferred_spans"]]
        self.assertTrue(any("12%" in line for line in grounded_text))
        self.assertTrue(any("guess" in line for line in inferred_text))

    def test_overlap_without_headings(self):
        reply = "The dropout rate is 12%.\nThis is a guess about next year."
        context = "The dropout rate is 12% in trial A."
        spans = audit_spans(reply, context)
        grounded_text = [reply[s["start"] : s["end"]] for s in spans["grounded_spans"]]
        self.assertTrue(any("12%" in line for line in grounded_text))


if __name__ == "__main__":
    unittest.main()
