"""Groundrails integration — text normalization and ClaimVerifier."""

from __future__ import annotations

from prompt_matrix.services.text_normalizer import normalize_text
from prompt_matrix.services.verifier import ClaimVerifier, GROUNDRAILS_AVAILABLE


def test_text_normalization():
    raw = "  Confidential \n\n Page 1 \n Test content with   extra spaces. "
    normalized = normalize_text(raw)
    assert "Page 1" not in normalized
    assert "Confidential" not in normalized
    assert "Test content with extra spaces." in normalized


def test_verifier_fallback_or_execution():
    verifier = ClaimVerifier(confidence_threshold=0.85)
    claims = ["The company revenue grew by 15% in Q3."]
    source = "During Q3, the company revenue grew by 15% across all sectors."

    results = verifier.verify_claims(claims, source)
    assert len(results) == 1
    assert results[0]["claim"] == claims[0]
    assert "engine" in results[0]


def test_groundrails_optional_import_behavior():
    verifier = ClaimVerifier()
    assert isinstance(verifier, ClaimVerifier)
    assert isinstance(GROUNDRAILS_AVAILABLE, bool)
