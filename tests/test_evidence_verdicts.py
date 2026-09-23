"""Evidence verdicts follow the source, not the word count.

The rules these defend are the product's promise: Assure must never be more
certain than the source allows. Each case below was a real misclassification
before the classifier was weighted — a claim a source denied was reported
supported, and an explicit contradiction was reported as agreement.

The tests assert what a reader of the evidence drawer observes: the verdict and
its reason for a claim against a source. They do not assert the classifier's
internals, so the lexical path can be replaced by the semantic one without
rewriting them.
"""

from __future__ import annotations

import pytest

try:
    from prompt_matrix.services.evidence_assembly import assemble_evidence
except ImportError:
    from services.evidence_assembly import assemble_evidence


def _verdict(claim: str, source: str) -> str:
    rows = [{"id": "f1", "filename": "policy.pdf", "extracted_text": source}]
    return assemble_evidence(claim, rows).verdict.verdict


def _reason(claim: str, source: str) -> str:
    rows = [{"id": "f1", "filename": "policy.pdf", "extracted_text": source}]
    return assemble_evidence(claim, rows).verdict.reason


# ── contradiction is explicit, and it is detected ────────────────────────────


@pytest.mark.parametrize(
    "claim, source, why",
    [
        (
            "Coverage includes flood damage",
            "Coverage excludes flood damage.",
            "include/exclude is the ordinary way a policy denies a peril, and the "
            "antonym table was financial before this: the pair never fired and the "
            "claim fell through to supported",
        ),
        (
            "The deductible is 25,000 USD",
            "The deductible is 50,000 USD.",
            "a figure the source states differently is the source denying the claim, "
            "not silence; every word around the number matches",
        ),
        (
            "Coverage is provided for water damage",
            "Coverage is not provided for water damage.",
            "explicit negation",
        ),
    ],
)
def test_explicit_opposition_is_contradicted(claim: str, source: str, why: str) -> None:
    assert _verdict(claim, source) == "contradicted", why


def test_a_sentence_does_not_contradict_itself() -> None:
    """The antonym pair ("cover", "exclude") fires on "Coverage excludes …".

    "cover" is a substring of "coverage", so matching on substrings reported an
    ordinary excludes-a-peril sentence contradicted by itself.
    """
    sentence = "Coverage excludes flood damage"
    assert _verdict(sentence, sentence + ".") == "supported"


# ── silence is not contradiction, and not support ────────────────────────────


def test_a_silent_source_is_not_supported() -> None:
    assert _verdict(
        "The policy covers flood damage", "The policy covers fire damage."
    ) == "partial", (
        "the sentence shape matches and the subject does not: partial is the honest "
        "state, and supported is the one it wrongly took before the ratio was weighted"
    )


def test_a_source_silent_on_the_claim_is_not_supported() -> None:
    assert _verdict(
        "Completely unrelated claim about penguins",
        "The deductible is 25,000 USD.",
    ) == "not_supported"


def test_a_source_with_no_text_is_unanchored() -> None:
    rows = [{"id": "f1", "filename": "empty.pdf", "extracted_text": ""}]
    assert assemble_evidence("the deductible is 25,000", rows).verdict.verdict == "unanchored"


# ── support is stated, not inferred ──────────────────────────────────────────


@pytest.mark.parametrize(
    "claim",
    [
        "The liability limit is 5,000,000 USD",
        "The deductible for Suffolk is 25,000 USD",
        "This is a Commercial Property policy",
    ],
)
def test_a_verbatim_claim_is_supported(claim: str) -> None:
    assert _verdict(claim, claim + ".") == "supported"


def test_a_supported_verdict_carries_a_reason() -> None:
    """The reader has to be able to answer "why did it say that"."""
    assert _reason("The deductible is 25,000 USD", "The deductible is 25,000 USD.")


# ── determinism ──────────────────────────────────────────────────────────────


def test_the_same_inputs_produce_the_same_verdict() -> None:
    rows = [
        {
            "id": "f1",
            "filename": "policy.pdf",
            "extracted_text": "The deductible is 25,000 USD. Coverage excludes flood damage.",
        }
    ]
    verdicts = {assemble_evidence("the deductible is 25,000", rows).verdict.verdict for _ in range(5)}
    assert len(verdicts) == 1, "a verdict a reader sees twice must not change between reads"
