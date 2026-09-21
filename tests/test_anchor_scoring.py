"""The lexical anchor's denominator, and the citation a row keeps.

Two things the matcher and the JDF model own, both of which fail silently:

* ``models.jdf.attach_substrate_provenance_to_tree`` scores a window by
  ``inter / min(len(content), len(window))``. The denominator is the *shorter* of
  the two, so a window that carries the whole claim scores 1.0 however long it is,
  and the tie-break picks the narrowest one. A denominator of the window alone
  rewards short windows instead: a sentence that shares four of the claim's tokens
  outscores the sentence that shares all of them, and the claim is anchored to
  evidence that does not state it.
* A cited row (``[S<N>]`` resolved against the sentence map) carries ``cited_id``
  and ``page``. Neither was declared on the JDF provenance model, and
  ``extra="ignore"`` is what an undeclared field gets, so a persisted row read back
  as quote + filename with no id and no page: the export could not say which
  sentence of which page a claim rested on.
"""

from __future__ import annotations

from prompt_matrix.models.jdf import (
    attach_substrate_provenance_to_tree,
    parse_document,
)

CLAIM = "The deductible for direct physical loss applies to every covered occurrence of damage"
#: Carries every token of the claim, and a lot of prose the claim does not use.
FULL_SENTENCE = (
    "The deductible for direct physical loss applies to every covered occurrence of damage "
    "and the liability under this contract is limited to the scheduled amount stated in the "
    "declarations page together with all endorsements attached to this contract"
)
#: Shares four of the claim's tokens out of six, so it scores 4/6 = 0.67 under either
#: denominator — against 1.0 for the sentence above, and 9/15 = 0.60 for it when the
#: denominator is the window alone.
PARTIAL_SENTENCE = "The deductible for physical loss applies to scheduled property"


def _tree(content: str = CLAIM) -> dict:
    return {
        "document_id": "doc-anchor",
        "meta": {},
        "truth_ledger": {},
        "body": [
            {
                "type": "section",
                "id": "sec-1",
                "title": "Coverage",
                "children": [
                    {
                        "type": "paragraph",
                        "id": "para-1",
                        "content": content,
                        "entities_referenced": [],
                        "provenance": [],
                        "meta": {},
                    }
                ],
                "meta": {},
            }
        ],
    }


def _rows(text: str) -> list[dict]:
    return [
        {"id": "sub-1", "filename": "policy.pdf", "page_number": 1, "extracted_text": text}
    ]


def _anchor(tree: dict, text: str) -> dict:
    out = attach_substrate_provenance_to_tree(tree, [], _rows(text))
    return out["body"][0]["children"][0]["provenance"][0]


def test_the_window_that_carries_the_whole_claim_wins_over_a_shorter_partial_one():
    """The denominator is the shorter text, so carrying every token scores 1.0.

    Under ``inter / len(window)`` the partial sentence above outscores the complete
    one (0.67 against 0.60) and the claim is anchored to a sentence that does not
    state it — the quote the Evidence pane shows, and the evidence the entailment
    check judges, would be the wrong sentence.
    """
    row = _anchor(_tree(), f"{PARTIAL_SENTENCE}. {FULL_SENTENCE}.")
    assert row["extracted_quote"] == FULL_SENTENCE[:280]
    assert row["anchor_window"] == FULL_SENTENCE


def test_a_short_window_is_still_preferred_when_the_scores_tie():
    """The tie-break is separate from the denominator and stays: narrowest wins.

    A claim one sentence already covers keeps that sentence as its window instead of
    dragging its neighbours into the entailment prompt.
    """
    filler = "Coverage excludes flood damage to any building on the scheduled premises"
    row = _anchor(_tree(), f"{FULL_SENTENCE}. {filler}.")
    assert row["anchor_window"] == FULL_SENTENCE


def test_a_cited_row_keeps_its_sentence_id_and_page_through_the_model():
    """``cited_id`` and ``page`` survive validation, which is where they were lost.

    ``extra="ignore"`` drops an undeclared field, so a row read back carried the
    quote and the filename and neither the id nor the page: the export could not say
    which sentence of which page a claim rested on.
    """
    tree = _tree()
    tree["body"][0]["children"][0]["provenance"] = [
        {
            "source_type": "internal_doc",
            "source_name": "policy.pdf",
            "source_id": "sub-1",
            "page_number": "3",
            "extracted_quote": "The deductible is twenty five thousand dollars.",
            "cited_id": "S12",
            "page": 3,
            "a_key_no_one_declared": "dropped",
        }
    ]
    row = parse_document(tree).model_dump(mode="json")["body"][0]["children"][0]["provenance"][0]
    assert row["cited_id"] == "S12"
    assert row["page"] == 3
    assert "a_key_no_one_declared" not in row


def test_a_citation_stamped_on_the_tree_survives_being_parsed():
    """End to end: resolve ``[S3]``, then read the document back as the app serves it.

    This is the path a compile takes (``attach_citations_to_tree`` writes raw rows,
    the repository parses them into the model to save), so an undeclared field is
    lost exactly here — after the compile widget has counted it and before the
    export reads it.
    """
    from prompt_matrix.routers.draft import attach_citations_to_tree

    sentences = (
        "The policy liability limit is five million dollars per occurrence. "
        "Coverage excludes flood damage to any building on the premises. "
        "The deductible for direct physical loss is twenty five thousand dollars."
    )
    tree = _tree("The deductible is twenty five thousand dollars. [S3]")
    stamped = attach_citations_to_tree(
        tree, [{"id": "sub-1", "filename": "policy.pdf", "extracted_text": sentences, "page_number": 7}]
    )
    stamped_row = stamped["body"][0]["children"][0]["provenance"][0]
    assert stamped_row["cited_id"] == "S3"
    assert stamped_row["page"] == 7

    parsed = parse_document(stamped).model_dump(mode="json")
    row = parsed["body"][0]["children"][0]["provenance"][0]
    assert row["cited_id"] == "S3"
    assert row["page"] == 7
    assert row["extracted_quote"] == "The deductible for direct physical loss is twenty five thousand dollars"
