"""``services/numeric_recompute`` — figures and arithmetic, no model.

Each case is one of the documented shapes (docs/verification.md). The module
claims nothing beyond them, so there is deliberately no test asserting that an
unlisted shape is caught.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from prompt_matrix.services.numeric_recompute import extract_figures, recompute


def test_figures_are_normalised_by_value_and_kind() -> None:
    figures = extract_figures(
        "POL-2025-00123 covers $1,250.00 at 12 % from 2025-01-15 to January 15, 2026; 3 items; see Section 4."
    )
    assert [(f.kind, str(f.value)) for f in figures] == [
        ("money", "1250.00"),
        ("percent", "12"),
        ("date", "2025-01-15"),
        ("date", "2026-01-15"),
        ("number", "3"),
    ]
    # An identifier and a cross-reference are not figures.
    assert all("00123" not in f.text and f.text != "4" for f in figures)


def test_money_matches_a_plain_number_of_the_same_value() -> None:
    result = recompute("The deductible is $1,250.00.", "Deductible 1250")
    assert result["status"] == "not_applicable"
    assert result["missing"] == []


def test_a_scaled_amount_matches_its_expanded_form() -> None:
    result = recompute("Coverage of $5 million applies.", "Limit: $5,000,000.")
    assert result["missing"] == []


def test_a_figure_absent_from_the_source_is_a_missing_figure_mismatch() -> None:
    result = recompute("The deductible is $25,000 per occurrence.", "Coverage applies each occurrence.")
    assert result["status"] == "mismatch"
    assert result["kind"] == "missing_figure"
    assert result["missing"] == ["$25,000"]
    assert result["stated"] == "$25,000"


def test_the_same_label_with_another_value_is_an_arithmetic_conflict() -> None:
    result = recompute("The deductible is $25,000 per occurrence.", "Deductible: $5,000,000 each occurrence.")
    assert result["status"] == "mismatch"
    assert result["kind"] == "arithmetic"
    assert result["expected"] == "$5,000,000"
    assert result["stated"] == "25000"


def test_qualified_labels_are_different_items() -> None:
    source = "Collision Deductible: $500\nComprehensive Deductible: $250\nLiability Limit: $100,000"
    ok = recompute("The collision deductible is $500 and the comprehensive deductible is $250.", "", source_text=source)
    assert ok["status"] == "not_applicable" and ok["missing"] == []
    conflict = recompute("The collision deductible is $600.", "", source_text=source)
    assert conflict["status"] == "mismatch" and conflict["kind"] == "arithmetic"
    assert "collision deductible as $500" in conflict["detail"]
    # A bare "deductible" is not compared against the qualified ones.
    plain = recompute("The deductible is $600.", "", source_text=source)
    assert plain["kind"] == "missing_figure"


def test_figures_are_searched_in_the_whole_source_not_only_the_window() -> None:
    source = "Policy Number: AP-2025-0001\nPolicy Period: 01/15/2025 to 01/15/2026\nTotal Premium: $1,250.00\nLiability Limit: $100,000"
    result = recompute(
        "Policy AP-2025-0001 runs from 01/15/2025 to 01/15/2026 with a total premium of $1,250 and a liability limit of $100,000.",
        "Policy Number: AP-2025-0001",
        source_text=source,
    )
    assert result["status"] == "not_applicable"
    assert result["missing"] == []


def test_listed_amounts_are_not_summed_without_an_explicit_relation() -> None:
    """Live run 2026-09-27: three unrelated amounts were summed against a 'total
    premium' in the next sentence and reported as a contradiction."""
    source = "Total Premium: $1,250.00\nLiability Limit: $100,000\nCollision Deductible: $500\nComprehensive Deductible: $250"
    listing = recompute(
        "The liability limit is $100,000, the collision deductible is $500 and the comprehensive deductible is $250. "
        "The total premium is $1,250.",
        "",
        source_text=source,
    )
    assert listing["status"] == "not_applicable"
    same_sentence = recompute(
        "The policy has a liability limit of $100,000, a collision deductible of $500 and a total premium of $1,250.",
        "",
        source_text=source,
    )
    assert same_sentence["status"] == "not_applicable"


def test_no_figure_is_not_applicable() -> None:
    result = recompute("Flood is excluded.", "Flood is excluded.")
    assert result["status"] == "not_applicable"
    assert result["expected"] is None and result["stated"] is None


@pytest.mark.parametrize(
    ("claim", "status"),
    [
        ("The premium of $1,250.00 and the fee of $250 total $1,500.", "recomputed_ok"),
        ("$1,500 in total ($1,250.00 + $250).", "recomputed_ok"),
        ("The premium of $1,250.00 and the fee of $250 total $1,600.", "mismatch"),
    ],
)
def test_sums_are_recomputed_from_source_operands(claim: str, status: str) -> None:
    result = recompute(claim, "Premium: $1,250.00. Policy fee: $250.")
    assert result["status"] == status
    if status == "mismatch":
        assert result["expected"] == "1500"
        assert result["stated"] == "1600"


def test_a_correct_total_the_source_does_not_state_is_accounted_for() -> None:
    """The recomputation vouches for the derived figure; it need not be verbatim."""
    result = recompute(
        "The premium of $1,250.00 and the fee of $250 total $1,500.",
        "Premium: $1,250.00. Policy fee: $250.",
    )
    assert result["status"] == "recomputed_ok"
    assert result["missing"] == []


@pytest.mark.parametrize(
    ("claim", "status", "expected"),
    [
        ("Claims of $300 of $1,200 (25%) were paid.", "recomputed_ok", "25%"),
        ("25% of $1,200 is $300.", "recomputed_ok", "25%"),
        ("Claims of $300 of $1,200 (30%) were paid.", "mismatch", "25%"),
        # ±0.5 point tolerance on a percentage.
        ("Claims of $301 of $1,200 (25%) were paid.", "recomputed_ok", "25.08%"),
    ],
)
def test_percent_shares_are_recomputed(claim: str, status: str, expected: str) -> None:
    result = recompute(claim, "Claims paid $300 and $301 against a $1,200 reserve.")
    assert result["status"] == status
    assert result["expected"] == expected


@pytest.mark.parametrize(
    ("claim", "status"),
    [
        ("The premium increased by $200 from $1,000 to $1,200.", "recomputed_ok"),
        ("The premium increased by 20% from $1,000 to $1,200.", "recomputed_ok"),
        ("The premium rose from $1,000 to $1,200, an increase of $200.", "recomputed_ok"),
        ("The premium increased by $300 from $1,000 to $1,200.", "mismatch"),
        # The direction is part of the claim.
        ("The premium decreased by $200 from $1,000 to $1,200.", "mismatch"),
    ],
)
def test_differences_are_recomputed_with_direction(claim: str, status: str) -> None:
    result = recompute(claim, "Prior premium $1,000; renewal premium $1,200.")
    assert result["status"] == status


@pytest.mark.parametrize(
    ("claim", "status"),
    [
        ("The policy runs from 01/15/2025 to 01/15/2026, 12 months.", "recomputed_ok"),
        ("The policy runs from 01/15/2025 to 01/14/2026, 12 months.", "recomputed_ok"),
        ("The policy runs from 01/15/2025 to 01/15/2026, 1 year.", "recomputed_ok"),
        ("The policy runs from 01/15/2025 to 01/15/2026, 365 days.", "recomputed_ok"),
        ("The policy runs from 01/15/2025 to 01/15/2026, 18 months.", "mismatch"),
    ],
)
def test_date_spans_are_recomputed(claim: str, status: str) -> None:
    result = recompute(claim, "Policy period: 01/15/2025 to 01/15/2026 (or 01/14/2026).")
    assert result["status"] == status


def test_operands_absent_from_the_source_cannot_vouch_for_a_total() -> None:
    """A total that checks arithmetically against the draft's own figures is not
    recomputed from the source when those figures are not in it."""
    result = recompute(
        "The premium of $1,250.00 and the fee of $250 total $1,500.",
        "The policy fee is $250.",
    )
    assert result["status"] == "mismatch"
    assert "$1,250.00" in result["missing"]


def test_percent_tolerance_is_half_a_point() -> None:
    ok = recompute("$1 of $3 (33.5%).", "$1 and $3.")
    off = recompute("$1 of $3 (34%).", "$1 and $3.")
    assert ok["status"] == "recomputed_ok"
    assert off["status"] == "mismatch"
    assert Decimal(off["expected"].rstrip("%")) == Decimal("33.33")
