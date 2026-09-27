"""Evidence honesty (2026-09-27): no invented page, quote, proof or score.

Each test pins one rule from ``docs/evidence-honesty.md``:

* a page is the page the quote was found on, or null;
* an excerpt is verbatim source text located by search, or null;
* a proof is a real Z3 run's output, or ``{"status": "not_run", "reason"}``;
* numeric checks answer with a status word (``matches_lock`` / ``no_lock`` /
  ``contradicts_lock``), never a number a UI could read as confidence;
* the legacy verifiers report ``INSUFFICIENT_EVIDENCE`` / ``unverified`` unless
  the claim is found verbatim; nothing is ``grounded`` or ``stamped`` on word
  overlap; the compile task's Z3 gate says ``not_run``.
"""

from __future__ import annotations

from unittest.mock import patch

from prompt_matrix.ledger.truth_engine import run_z3_verification
from prompt_matrix.ledger.z3_ledger import check_claim
from prompt_matrix.services.confidence_spans import build_confidence_spans, build_macro_appendix
from prompt_matrix.services.lock_evidence import _excerpt_from_text, _z3_proof_for_lock
from prompt_matrix.services.lock_metadata import enrich_extracted_locks
from prompt_matrix.services.orchestrator import _locks_from_verdicts
from prompt_matrix.services.provenance_meta import build_node_provenance_meta
from prompt_matrix.services.verifier import (
    INSUFFICIENT_EVIDENCE,
    _verdict_to_lock,
    native_heuristic_verify,
)
from prompt_matrix.tasks import compile_tasks
from prompt_matrix.verification.grounding import GroundingVerdict

# --- pages ------------------------------------------------------------------


def test_enrich_extracted_locks_does_not_default_the_page() -> None:
    locks = enrich_extracted_locks(
        [{"canonical_key": "Revenue", "value": 12_000_000, "metric": "Revenue"}],
        [{"id": "s1", "name": "brief.pdf"}],
    )
    assert locks[0]["page_coordinates"] is None
    assert locks[0]["lock_hash"]


def test_enrich_extracted_locks_keeps_a_located_page() -> None:
    located = {"page": 4, "x": 10, "y": 20, "width": 100, "height": 24}
    locks = enrich_extracted_locks(
        [{"canonical_key": "Revenue", "value": 1, "page_coordinates": located}],
        [{"id": "s1", "name": "brief.pdf"}],
    )
    assert locks[0]["page_coordinates"] == located


def test_lock_excerpt_page_is_the_page_the_hit_sits_under() -> None:
    text = "--- Page 1 ---\nCover letter.\n--- Page 3 ---\nRevenue reached $12M in Q3."
    excerpt, page = _excerpt_from_text(text, {"metric": "Revenue", "value": 12_000_000})
    assert excerpt and "Revenue reached $12M" in excerpt
    assert page == 3


def test_lock_excerpt_without_markers_has_no_page() -> None:
    excerpt, page = _excerpt_from_text("Revenue reached $12M.", {"metric": "Revenue", "value": 12_000_000})
    assert excerpt
    assert page is None


def test_check_claim_does_not_guess_a_page_from_a_stray_word() -> None:
    # Only "the" overlaps: the old _page_from_context answered page 2 for it.
    result = check_claim(
        "The deductible is 500",
        context="--- Page 2 ---\nThe cover letter thanks the broker.",
        ledger={},
        source_label="policy.pdf",
    )
    prov = result["provenance"]
    assert prov["page_number"] is None
    assert prov["excerpt"] is None


# --- excerpts ---------------------------------------------------------------


def test_lock_excerpt_has_no_first_240_chars_fallback() -> None:
    text = "Cover letter text that never mentions the figure or the metric name."
    excerpt, page = _excerpt_from_text(text, {"metric": "Headcount", "value": 42})
    assert excerpt is None
    assert page is None


def test_provenance_meta_excerpt_is_never_the_claim_itself() -> None:
    node = {
        "type": "paragraph",
        "id": "p1",
        "content": "The team grew quickly this year.",
        "provenance": [{"source_id": "s1", "source_name": "a.pdf"}],
    }
    summary = build_node_provenance_meta(node, ledger={})
    assert summary is not None
    assert summary["excerpt"] is None
    assert summary["page_number"] is None


def test_provenance_meta_excerpt_is_the_anchored_quote() -> None:
    node = {
        "type": "paragraph",
        "id": "p1",
        "content": "Revenue is 100 this quarter.",
        "provenance": [
            {
                "source_id": "s1",
                "source_name": "a.pdf",
                "page_number": 3,
                "extracted_quote": "Revenue is 100 for Q3.",
            }
        ],
    }
    summary = build_node_provenance_meta(node, ledger={"revenue": 100})
    assert summary["excerpt"] == "Revenue is 100 for Q3."
    assert summary["page_number"] == 3


# --- proofs -----------------------------------------------------------------


def test_lock_proof_is_not_run_when_no_solver_ran() -> None:
    proof = _z3_proof_for_lock(
        {"canonical_key": "Revenue", "value": 12_000_000},
        {"content": {"truth_ledger": {"Revenue": 12_000_000}}},
    )
    assert isinstance(proof, dict)
    assert proof["status"] == "not_run"
    assert proof["reason"]
    assert "SAT" not in str(proof)


def test_lock_proof_keeps_a_real_run_output() -> None:
    proof = _z3_proof_for_lock({"z3_proof": "(check-sat)\nunsat"}, {})
    assert proof == "(check-sat)\nunsat"


# --- scores → status words --------------------------------------------------


def test_run_z3_verification_answers_with_status_words_not_scores() -> None:
    matched = run_z3_verification("Revenue is 100", ledger={"revenue": 100})
    assert matched["numeric_consistency"] == "matches_lock"
    assert matched["score"] is None

    contradicted = run_z3_verification("Revenue is 200", ledger={"revenue": 100})
    assert contradicted["numeric_consistency"] == "contradicts_lock"
    assert contradicted["score"] is None

    unrelated = run_z3_verification("Headcount is 50", ledger={"revenue": 100})
    assert unrelated["numeric_consistency"] == "no_lock"

    prose = run_z3_verification("The moon is cheese.", ledger={"revenue": 100})
    assert prose["numeric_consistency"] == "no_lock"
    assert prose["score"] is None


def test_scaled_figures_match_their_lock() -> None:
    # "12 million" / "$12M" are 12,000,000 to the ledger; reading them as 12
    # called an agreeing claim contradicts_lock.
    assert run_z3_verification("Revenue reached 12 million.", ledger={"Revenue": 12_000_000})[
        "numeric_consistency"
    ] == "matches_lock"
    assert run_z3_verification("Revenue was $12M.", ledger={"Revenue": 12_000_000})[
        "numeric_consistency"
    ] == "matches_lock"
    assert run_z3_verification("Revenue was $13M.", ledger={"Revenue": 12_000_000})[
        "numeric_consistency"
    ] == "contradicts_lock"


def test_check_claim_has_no_confidence_number() -> None:
    result = check_claim("Revenue is 100", context="revenue is 100", ledger={"revenue": 100})
    assert result["confidence"] is None
    assert result["score"] is None
    assert result["numeric_consistency"] == "matches_lock"
    assert result["provenance"]["confidence"] is None
    assert result["provenance"]["verified_at"]  # a figure WAS checked


def test_check_claim_verified_at_is_null_without_a_check() -> None:
    result = check_claim("The moon is cheese.", context="the moon is cheese.", ledger={})
    assert result["provenance"]["verified_at"] is None
    assert result["provenance"]["rule"] is None


def test_provenance_meta_has_no_default_confidence() -> None:
    node = {
        "type": "paragraph",
        "id": "p1",
        "content": "The team grew quickly this year.",
        "provenance": [{"source_id": "s1", "source_name": "a.pdf", "extracted_quote": "Team grew."}],
    }
    summary = build_node_provenance_meta(node, ledger={"revenue": 100})
    assert summary["confidence"] is None
    assert summary["numeric_consistency"] == "no_lock"
    assert summary["verified_at"] is None


def test_confidence_spans_carry_status_words_and_null_scores() -> None:
    document = {
        "document_id": "doc-1",
        "meta": {},
        "truth_ledger": {"Revenue": 12_000_000},
        "body": [
            {
                "type": "section",
                "id": "s1",
                "title": "Results",
                "children": [
                    {
                        "type": "paragraph",
                        "id": "p1",
                        "content": "Revenue reached 12000000. The moon is cheese.",
                        "provenance": [
                            {"source_id": "s1", "source_name": "a.pdf", "extracted_quote": "Revenue reached 12000000 in Q3."}
                        ],
                    }
                ],
            }
        ],
    }
    z3 = {"violations": ["Metric 'moon' = 1 contradicts locked == 0"], "lock_results": []}
    spans = [s for s in build_confidence_spans(document, z3_results=z3) if s["nodeId"] == "p1"]
    assert spans
    assert all(s["score"] is None for s in spans)
    words = {s["numeric_consistency"] for s in spans}
    assert words <= {"matches_lock", "no_lock", "contradicts_lock"}
    assert "matches_lock" in words
    assert "contradicts_lock" in words
    rows = build_macro_appendix(document, z3_results=z3)
    assert all(r["z3Score"] is None and r["z3_score"] is None for r in rows)
    assert all("numeric_consistency" in r for r in rows)


# --- legacy verifiers -------------------------------------------------------


def test_heuristic_overlap_is_insufficient_evidence() -> None:
    verdict = native_heuristic_verify(
        "Revenue reached twelve million in Q3 according to the board",
        "Cover letter: revenue, board, million, twelve — words scattered across the file.",
    )
    assert verdict["grounded"] is False
    assert verdict["verdict"] == INSUFFICIENT_EVIDENCE
    assert verdict["reason"] == "heuristic overlap is not verification"
    assert verdict["score"] is None
    assert verdict["support"]["passage"] is None
    assert _verdict_to_lock(verdict, source_id="s1", web=False) is None


def test_verbatim_match_quotes_the_source_at_its_offset() -> None:
    source = "Intro line.\nRevenue reached twelve million in Q3. Next."
    verdict = native_heuristic_verify("Revenue reached twelve million in Q3", source)
    assert verdict["grounded"] is True
    assert verdict["support"]["passage"] == "Revenue reached twelve million in Q3"
    assert source[verdict["support"]["offset"] :].startswith("Revenue reached")
    lock = _verdict_to_lock(verdict, source_id="s1", web=False)
    assert lock is not None
    assert lock["page_coordinates"] is None
    assert lock["confidence"] is None


def test_orchestrator_locks_are_unverified_not_amber() -> None:
    locks = _locks_from_verdicts(
        [{"claim_id": "claim_1", "text": "x"}],
        [{"claim": "x", "grounded": False, "verdict": INSUFFICIENT_EVIDENCE, "reason": "r", "score": 0.4}],
    )
    assert locks[0]["status"] == "unverified"
    assert locks[0]["confidence_score"] is None
    assert locks[0]["verdict"] == INSUFFICIENT_EVIDENCE


def test_compile_task_reports_z3_not_run_and_never_verified() -> None:
    with (
        patch.object(compile_tasks, "fetch_vault_markdown", return_value="Revenue $12M in Q3."),
        patch.object(compile_tasks, "generate_draft", return_value="Revenue $12M in Q3."),
        patch(
            "prompt_matrix.verification.grounding.verify_claims",
            return_value=GroundingVerdict(grounded=True, unverified=[], details={"engine": "fallback"}),
        ),
        patch("prompt_matrix.llm.orchestrator.orchestrate_node_compilation_sync", return_value="OK"),
        patch.object(compile_tasks, "commit_to_ast", return_value={"ok": True, "version": 2}),
        patch.object(compile_tasks.check_and_trigger_automations, "delay"),
    ):
        out = compile_tasks.safe_compile_and_verify.run("proj-1", "node-1")

    assert out["status"] == "success"
    assert out["verified"] is False
    assert out["verification"]["z3"] == compile_tasks.z3_not_run()
    assert out["verification"]["z3"]["status"] == "not_run"
    assert out["verification"]["grounding"]["reason"] == "heuristic overlap is not verification"
    assert out["verification"]["grounding"]["verified"] is False
    assert out["verification"]["redhat"]["status"] == "unverified"
