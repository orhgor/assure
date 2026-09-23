"""Surgical revision: node-scoped patch, computed diff, and the grounding gate.

Each test defends a property the feature promises. Three of them correspond to
failure modes that are easy to introduce and invisible in a passing build: a
diff that turns a quote change into a whole-paragraph rewrite, a replacement
that eats the whitespace between paragraphs, and a proposal that reports itself
verified when nothing was checked.
"""

from __future__ import annotations

import json

import pytest

try:
    from prompt_matrix.services.refine_diff import (
        build_surgical_prompt,
        compute_word_diff,
        diff_summary,
        format_drift,
        normalize_for_diff,
        parse_surgical_response,
        restore_edge_whitespace,
    )
    from prompt_matrix.services.refine_node import verify_surgical_patch
except ImportError:
    from services.refine_diff import (
        build_surgical_prompt,
        compute_word_diff,
        diff_summary,
        format_drift,
        normalize_for_diff,
        parse_surgical_response,
        restore_edge_whitespace,
    )
    from services.refine_node import verify_surgical_patch


# ── the model's answer is validated, not trusted ─────────────────────────────


def test_a_structured_answer_is_unwrapped() -> None:
    """A model honouring the schema returns an object; its text is the proposal."""
    raw = json.dumps({"rationale": "only the figure changed", "proposed_text": "The limit is 7."})
    text, rationale = parse_surgical_response(raw)
    assert text == "The limit is 7."
    assert rationale == "only the figure changed"


def test_a_fenced_answer_is_unwrapped() -> None:
    raw = '```json\n{"rationale": "r", "proposed_text": "Fenced text."}\n```'
    assert parse_surgical_response(raw)[0] == "Fenced text."


def test_a_bare_answer_is_the_proposal() -> None:
    """The previous prompt produced plain text; those models must keep working."""
    assert parse_surgical_response("The limit is 7.")[0] == "The limit is 7."


def test_an_answer_with_extra_keys_is_refused() -> None:
    with pytest.raises(ValueError):
        parse_surgical_response(json.dumps({"proposed_text": "x", "confidence": 0.9}))


def test_an_empty_proposal_is_refused() -> None:
    for bad in (json.dumps({"proposed_text": "   "}), "   ", ""):
        with pytest.raises(ValueError):
            parse_surgical_response(bad)


# ── the diff describes the change, not the formatting ────────────────────────


def test_quote_and_space_normalisation_does_not_inflate_the_diff() -> None:
    """A straight-to-curly quote change is not a paragraph rewrite.

    Compared literally, the whole paragraph differs; a reader sees a wall of red
    and green around changes nobody made.
    """
    original = 'The patient recovered.  The deductible is "25,000".'
    proposed = "The patient fully recovered. The deductible is \u201c25,000\u201d."
    summary = diff_summary(compute_word_diff(original, proposed))
    assert summary["inserted_words"] <= 2, summary
    assert summary["deleted_words"] == 0, summary


def test_a_changed_figure_is_visible_on_both_sides() -> None:
    ops = compute_word_diff("The limit is 5,000,000 USD.", "The limit is 7,000,000 USD.")
    assert any(o["op"] == "delete" and "5,000,000" in o["text"] for o in ops)
    assert any(o["op"] == "insert" and "7,000,000" in o["text"] for o in ops)


def test_an_unchanged_node_produces_no_changes() -> None:
    assert diff_summary(compute_word_diff("Same text.", "Same text."))["changed"] is False


def test_normalisation_is_not_returned_as_document_text() -> None:
    """The comparison form folds characters; the committed text must not."""
    raw = "\u201cquoted\u201d  text"
    assert normalize_for_diff(raw) == '"quoted" text'
    assert raw == "\u201cquoted\u201d  text"


# ── whitespace belongs to the document, not the sentence ─────────────────────


def test_trailing_whitespace_survives_a_replacement() -> None:
    """Repeated edits must not eat the space between paragraphs."""
    original = "The patient recovered. "
    proposed = "The patient fully recovered."
    assert restore_edge_whitespace(original, proposed) == "The patient fully recovered. "


def test_leading_whitespace_survives_a_replacement() -> None:
    assert restore_edge_whitespace("  Indented.", "Indented again.") == "  Indented again."


# ── drift is checked, not requested ──────────────────────────────────────────


def test_structure_added_without_being_asked_is_drift() -> None:
    drifted, reasons = format_drift(
        "The limit is 5,000,000.", "## Coverage\nThe limit is 5,000,000.\n- bullet", "fix the figure"
    )
    assert drifted is True
    assert any("headings" in r for r in reasons)


def test_structure_asked_for_is_not_drift() -> None:
    drifted, _ = format_drift(
        "The limit is 5,000,000.", "## Coverage\nThe limit is 5,000,000.", "add a heading"
    )
    assert drifted is False


def test_an_ordinary_edit_is_not_drift() -> None:
    drifted, _ = format_drift("The limit is 5,000,000.", "The limit is 7,000,000.", "change the figure")
    assert drifted is False


# ── the gate reports what it actually did ────────────────────────────────────


def test_a_node_with_no_cited_source_is_not_reported_as_verified() -> None:
    """An edit with nothing to check against has not been verified.

    Reporting that as a pass is the one outcome that makes the gate worse than
    having none, because it launders an unchecked edit into a checked one.
    """
    result = verify_surgical_patch({"provenance": []}, "The limit is 7,000,000.")
    assert result["status"] == "Error"
    assert result["status"] != "Pass"


def test_an_empty_proposal_is_not_reported_as_verified() -> None:
    node = {"provenance": [{"extracted_quote": "The limit is 5,000,000."}]}
    assert verify_surgical_patch(node, "   ")["status"] == "Error"


# ── the prompt carries what the model needs ──────────────────────────────────


def test_the_prompt_fences_the_target_and_its_neighbours() -> None:
    """Without the neighbours a pronoun in the target has no referent."""
    messages = build_surgical_prompt(
        original_text="It grew by 14%.",
        user_instruction="make it Q3",
        preceding="Q2 revenue was 1M.",
        following="Q4 was flat.",
        source_context="Q3 revenue grew 14%.",
    )
    user = messages[1]["content"]
    for block in (
        "[ORIGINAL_TEXT]",
        "[USER_INSTRUCTION]",
        "[PRECEDING_NODE_TEXT]",
        "[FOLLOWING_NODE_TEXT]",
        "[SOURCE_CONTEXT]",
    ):
        assert block in user, block
    assert "MINIMAL INTERVENTION" in messages[0]["content"]
    assert "FORBIDDEN BEHAVIORS" in messages[0]["content"]
