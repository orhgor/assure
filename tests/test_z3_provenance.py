"""Z3 provenance fields on check_claim."""

from __future__ import annotations

from prompt_matrix.ledger.z3_ledger import check_claim


def test_z3_returns_provenance_fields() -> None:
    result = check_claim(
        "Revenue is 100",
        context="--- Page 4 ---\nNAIC policy revenue is 100 for Q3.",
        ledger={"revenue": 100},
        source_label="NAIC_Underwriting_Policy_2025.pdf",
        source_id="file-naic-1",
    )
    prov = result.get("provenance") or {}
    assert prov.get("source_name") == "NAIC_Underwriting_Policy_2025.pdf"
    assert prov.get("source_id") == "file-naic-1"
    assert prov.get("page_number") == 4
    assert prov.get("excerpt")
    assert "revenue" in str(prov.get("rule") or "").lower()
    assert 0.0 <= float(prov.get("confidence") or 0) <= 1.0
    assert prov.get("verified_at")


def test_z3_provenance_rule_from_ledger() -> None:
    result = check_claim(
        "Deductible is 0.02",
        context="deductible rate 0.02",
        ledger={"deductible": 0.02},
        source_label="Policy.pdf",
    )
    prov = result.get("provenance") or {}
    assert "deductible" in str(prov.get("rule") or "").lower()
