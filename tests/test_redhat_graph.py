"""Automated Red-Hat over the intake evidence graph (``services/redhat_graph``).

One positive and one negative case per rule, the attach/counts contract, and
the grounded model check (a fake completion: supported → no finding;
unsupported with a verbatim quote → finding; without a quote → dropped).
The report shapes mirror ``v1_orchestrator.build_report`` (2026-09-26).
"""

from __future__ import annotations

import json

import pytest

from prompt_matrix.services import redhat_graph as rg


# ---------------------------------------------------------------------------
# Report factory — the orchestrator's shape, minimal
# ---------------------------------------------------------------------------


def _field(name, *, value="x", page=1, node="el-1", tree_node=None, state="unverified", routing="manual_review", **extra):
    f = {
        "name": name,
        "label": name.replace("_", " ").capitalize(),
        "field_type": extra.pop("field_type", "text"),
        "value": value,
        "field_state": state,
        "routing_action": routing,
        "review_required": routing != "none",
        "reason": "" if state == "accepted" else "compliance-bound field — human confirmation required",
        "extraction_confidence": 0.85 if value is not None else 0.0,
        "confidence_basis": "parser_default[jdf-cli] (0.85) × page_quality (1.00) = 0.85" if value is not None else "field not found",
        "verification_confidence": 1.0,
        "provenance_confidence": 1.0 if value is not None else 0.0,
        "field_source_node_id": node,
        "tree_node_id": tree_node,
        "source_span": {"page": page, "span_type": "text_range", "start_char": 0, "end_char": 5, **({"node_id": tree_node} if tree_node else {})} if value is not None else {"span_type": "absent", "pages": [page]},
        "evidence": {"kind": "found", "page": page, "node_id": node, "method": "label_anchor"} if value is not None else {"kind": "absent", "searched_pages": [page], "anchor_node_id": node, "anchor_kind": "layout_node"},
        "evidence_state": "found_unverified" if value is not None else "not_on_document",
    }
    f.update(extra)
    return f


def _report(fields, *, pages=None, classification=None, conflicts=(), texts=None, layout=None, graph_integrity=None, **extra):
    report = {
        "report_id": "pr-test",
        "project_id": "p",
        "document_id": "doc-1",
        "filename": "policy.pdf",
        "parser_name": "jdf-cli",
        "page_count": len(pages) if pages else 1,
        "pages": pages if pages is not None else [{"page": 1, "quality_score": 0.97, "flags": [], "ocr_confidence": None, "text_density": 0.4}],
        "classification": classification or {"document_type": "auto_policy", "confidence": 0.9, "validation": {"agrees": True, "family": "auto", "type_family": "auto"}, "schema_mismatch": False},
        "fields": fields,
        "conflicts": list(conflicts),
        "quality_report": {"signature": None},
        "review_summary": {"fields_total": len(fields), "reasons": [{"reason": "compliance-bound field", "count": 1}]},
        "graph_integrity": graph_integrity if graph_integrity is not None else {"fields": len(fields), "anchored": len(fields), "orphans": 0, "basis": "every field names a JDF node"},
    }
    if texts is not None:
        report["_page_texts"] = texts
    if layout is not None:
        report["_layout"] = layout
    report.update(extra)
    return report


def _clean_fields():
    return [
        _field("policy_number", value="PR-1", node="el-1"),
        _field("insured_name", value="Jane", node="el-2"),
        _field("premium", value=1486.0, node="el-3", extraction_confidence=0.77, confidence_basis="parser_default[jdf-cli] (0.85) × local_ocr (0.91) = 0.77"),
        _field("vin", value="1HGCM82633A004352", node="el-4", extraction_confidence=0.6, confidence_basis="parser_default × page_quality (0.7) = 0.60"),
    ]


def _rules(findings, rule):
    return [f for f in findings if f["rule"] == rule]


def _run(report, **kw):
    kw.setdefault("llm", False)
    return rg.critique_report(report, **kw)["findings"]


# ---------------------------------------------------------------------------
# Rules — positive and negative
# ---------------------------------------------------------------------------


def test_wrong_document_family_high_when_family_disagrees_medium_when_only_mismatch_none_when_agrees():
    disagree = _report(_clean_fields(), classification={"document_type": "auto_policy", "validation": {"agrees": False, "family": "medical", "type_family": "auto"}, "schema_mismatch": True})
    hits = _rules(_run(disagree), "wrong_document_family")
    assert len(hits) == 1 and hits[0]["severity"] == "high" and hits[0]["class"] == "structural"
    assert hits[0]["anchor"]["kind"] == "document_root" and hits[0]["anchor"]["node_id"] == "doc-1" and "note" in hits[0]["anchor"]
    assert "medical" in hits[0]["rationale"] and "auto" in hits[0]["rationale"]

    unknown = _report([], classification={"document_type": "medical_unknown", "validation": {"agrees": True, "family": "medical", "type_family": "medical"}, "schema_mismatch": True,
                                          "suggestion": {"document_type": "medical_claim", "found": 1, "total": 13}})
    hits = _rules(_run(unknown), "wrong_document_family")
    assert len(hits) == 1 and hits[0]["severity"] == "medium" and "medical claim" in hits[0]["rationale"]

    assert _rules(_run(_report(_clean_fields())), "wrong_document_family") == []


def test_coarse_chunking_fires_when_every_found_field_shares_one_node_on_a_dense_page():
    fields = [_field(n, value="v", node="p1e0") for n in ("policy_number", "insured_name", "premium")]
    dense = _report(fields, texts=["x" * 1600])
    hits = _rules(_run(dense), "coarse_chunking")
    assert len(hits) == 1 and hits[0]["severity"] == "medium" and hits[0]["class"] == "structural"
    assert hits[0]["anchor"]["page"] == 1 and hits[0]["anchor"]["node_id"] == "p1e0" and "1,600 characters" in hits[0]["rationale"]

    many_elements = _report(fields, texts=["short"], layout=[[{"node_id": "p1e0"}, {"node_id": "p1e1"}, {"node_id": "p1e2"}]])
    assert "3 elements" in _rules(_run(many_elements), "coarse_chunking")[0]["rationale"]

    # Negative: distinct nodes; or one node on a short single-element page.
    assert _rules(_run(_report(_clean_fields(), texts=["x" * 1600])), "coarse_chunking") == []
    assert _rules(_run(_report(fields, texts=["short"], layout=[[{"node_id": "p1e0"}]])), "coarse_chunking") == []


def test_unstable_or_missing_ids_found_without_node_orphans_and_missing_policy():
    fields = _clean_fields()
    fields[1]["field_source_node_id"] = None
    fields[1]["source_span"].pop("node_id", None)
    report = _report(fields, graph_integrity={"fields": 4, "anchored": 3, "orphans": 1, "basis": "1 field without a node"})
    hits = _rules(_run(report), "unstable_or_missing_ids")
    titles = {h["title"]: h for h in hits}
    assert titles["Found value without a node id"]["severity"] == "medium" and "insured name" in titles["Found value without a node id"]["rationale"]
    assert titles["Found value without a node id"]["anchor"]["kind"] == "document_root"  # no closer node: says so
    assert titles["Fields without any node on the graph"]["severity"] == "medium"
    assert titles["No node id policy recorded"]["severity"] == "low"

    # Negative: every found field addressed, no orphans, a policy named.
    clean = _report(_clean_fields(), node_id_policy="jdf-cli element ids (p<page>e<n>)")
    assert _rules(_run(clean), "unstable_or_missing_ids") == []


def test_broken_connectivity_only_with_a_tree_dangling_is_high_unlinked_is_medium():
    tree = {"body": [{"id": "sec-1", "children": [{"id": "p-1"}, {"id": "p-2"}]}]}
    fields = [_field("policy_number", value="PR-1", node="el-1", tree_node="p-1"), _field("insured_name", value="J", node="el-2", tree_node="p-404"),
              _field("premium", value=1.0, node="el-3", tree_node=None)]
    hits = _rules(_run(_report(fields), tree=tree), "broken_connectivity")
    by_sev = {h["severity"]: h for h in hits}
    assert "p-404" in by_sev["high"]["rationale"] and by_sev["high"]["anchor"]["field"] == "insured_name"
    assert "premium" in by_sev["medium"]["rationale"]

    # Negative: no tree in hand → the rule does not run and says so in the notes.
    out = rg.critique_report(_report(fields), llm=False)
    assert _rules(out["findings"], "broken_connectivity") == []
    assert any("broken_connectivity: no tree in hand" in n for n in out["notes"])
    # Negative: every found field linked to a node the tree has.
    linked = [_field("policy_number", value="PR-1", node="el-1", tree_node="p-1"), _field("insured_name", value="J", node="el-2", tree_node="p-2")]
    assert _rules(_run(_report(linked), tree=tree), "broken_connectivity") == []


def test_not_applicable_vs_not_found_three_signals():
    absent = [_field("vin", value=None, node="el-0", evidence_state="not_on_document"), _field("premium", value=None, node="el-0", evidence_state="unreadable", reason="field not found"),
              _field("policy_number", value=None, node="el-0", evidence_state=None)]
    disagree = {"document_type": "auto_policy", "validation": {"agrees": False, "family": "medical", "type_family": "auto"}, "schema_mismatch": False}
    hits = _rules(_run(_report(absent, classification=disagree)), "not_applicable_vs_not_found")
    titles = {h["title"]: h for h in hits}
    assert titles["Absent fields may be not applicable, not missing"]["severity"] == "medium" and titles["Absent fields may be not applicable, not missing"]["anchor"]["field"] == "vin"
    assert titles["Unreadable page counted as a missing value"]["severity"] == "medium"
    assert titles["Absence recorded without an evidence state"]["severity"] == "low"
    assert all(h["class"] == "evidentiary" for h in hits)

    # Negative: family agrees, unreadable field's reason says so, every absence qualified.
    ok = [_field("vin", value=None, node="el-0", evidence_state="not_on_document"), _field("premium", value=None, node="el-0", evidence_state="unreadable", reason="Page could not be read")]
    assert _rules(_run(_report(ok)), "not_applicable_vs_not_found") == []


@pytest.mark.parametrize("quality", ["questionable", "faint", "incomplete", "stamped"])
def test_signature_ambiguity_medium_for_ambiguous_qualities(quality):
    sig = _field("signature", value=None, node="el-9", field_type="signature", signature_quality={"present": True, "quality": quality, "basis": "text after signature label", "page": 1})
    hits = _rules(_run(_report([sig])), "signature_ambiguity")
    assert len(hits) == 1 and hits[0]["severity"] == "medium" and hits[0]["title"] == f"Signature is {quality}" and hits[0]["anchor"]["field"] == "signature"


def test_signature_ambiguity_missing_is_low_accepted_lowers_clear_is_nothing():
    missing = _field("signature", value=None, node="el-9", field_type="signature", signature_quality={"present": False, "quality": "missing", "basis": "signature line is blank"})
    hits = _rules(_run(_report([missing])), "signature_ambiguity")
    assert len(hits) == 1 and hits[0]["severity"] == "low" and "expected but not found" in hits[0]["title"]

    accepted = _field("signature", value="/s/ J", node="el-9", field_type="signature", state="accepted", routing="none", signature_quality={"present": True, "quality": "questionable"})
    hits = _rules(_run(_report([accepted])), "signature_ambiguity")
    assert len(hits) == 1 and hits[0]["severity"] == "low" and "reviewer accepted" in hits[0]["rationale"]

    # The card's bare-string shape (older reports) is read too.
    faint = _field("signature", value=None, node="el-9", field_type="signature", signature_quality="faint")
    assert _rules(_run(_report([faint])), "signature_ambiguity")[0]["title"] == "Signature is faint"

    clear = _field("signature", value="/s/ J", node="el-9", field_type="signature", signature_quality={"present": True, "quality": "clear"})
    assert _rules(_run(_report([clear])), "signature_ambiguity") == []
    assert _rules(_run(_report(_clean_fields())), "signature_ambiguity") == []


def test_low_quality_evidence_medium_on_a_page_with_found_fields_low_otherwise():
    pages = [{"page": 1, "quality_score": 0.31, "flags": ["blurry"], "ocr_confidence": 0.62}, {"page": 2, "quality_score": 0.9, "flags": ["low_res"], "ocr_confidence": None},
             {"page": 3, "quality_score": 0.95, "flags": [], "ocr_confidence": 0.55}]
    fields = [_field("policy_number", value="PR-1", node="p1e0", page=1)]
    hits = _rules(_run(_report(fields, pages=pages, layout=[[{"node_id": "p1e0"}], [{"node_id": "p2e0"}], []])), "low_quality_evidence")
    by_page = {h["anchor"]["page"]: h for h in hits}
    assert by_page[1]["severity"] == "medium" and "quality 0.31" in by_page[1]["rationale"] and "blurry" in by_page[1]["rationale"] and "OCR confidence 0.62" in by_page[1]["rationale"]
    assert by_page[1]["anchor"]["node_id"] == "p1e0" and by_page[1]["anchor"]["kind"] == "page"
    assert by_page[2]["severity"] == "low" and "no found field rests on it" in by_page[2]["rationale"]
    assert 3 not in by_page  # low OCR only counts on a page that carries a found field

    clean = _report(_clean_fields(), pages=[{"page": 1, "quality_score": 0.97, "flags": [], "ocr_confidence": 0.93}])
    assert _rules(_run(clean), "low_quality_evidence") == []


def test_cross_document_conflict_each_entry_high_anchored_to_the_field_with_dispute_and_correction_named():
    fields = _clean_fields()
    conflicts = [{"field": "insured_name", "kind": "cross_document", "values": [{"report_id": "pr-test", "value": "Jane"}, {"report_id": "pr-b", "value": "Jane Public-Smith"}]}]
    hits = _rules(_run(_report(fields, conflicts=conflicts), disputes=[{"field_name": "insured_name", "status": "open"}], corrections=[{"field_name": "insured_name"}]), "cross_document_conflict")
    assert len(hits) == 1 and hits[0]["severity"] == "high" and hits[0]["class"] == "evidentiary"
    assert hits[0]["anchor"] == {"node_id": "el-2", "element_id": "el-2", "page": 1, "field": "insured_name", "kind": "field"}
    assert "a dispute is open on it" in hits[0]["rationale"] and "it was corrected" in hits[0]["rationale"]
    assert _rules(_run(_report(fields)), "cross_document_conflict") == []


def test_export_overclaiming_certificate_language_outside_verified_is_high():
    report = _report(_clean_fields())
    hits = _rules(_run(report, export_state={"trust_state": "review_required", "title": "Formal Verification Certificate"}), "export_overclaiming")
    assert len(hits) == 1 and hits[0]["severity"] == "high" and hits[0]["class"] == "export" and "Certificate" in hits[0]["rationale"]
    assert _rules(_run(report, export_state={"trust_state": "verified", "title": "Verification Dossier — Verified", "subtitle": "Certificate of verification"}), "export_overclaiming") == []
    assert _rules(_run(report, export_state={"trust_state": "review_required", "title": "Verification Dossier — Review required"}), "export_overclaiming") == []
    out = rg.critique_report(report, llm=False)
    assert _rules(out["findings"], "export_overclaiming") == [] and any("export checks" in n and "no export state" in n for n in out["notes"])


def test_confidence_flattening_default_triple_is_medium_shared_value_is_low():
    flat = [_field(n, value="v", node=f"el-{i}", extraction_confidence=0.85, verification_confidence=0.85, provenance_confidence=0.85,
                   confidence_basis="parser_default (0.85)") for i, n in enumerate(("policy_number", "insured_name", "premium"))]
    hits = _rules(_run(_report(flat)), "confidence_flattening")
    by_sev = {h["severity"]: h for h in hits}
    assert "one default number" in by_sev["medium"]["title"] and by_sev["medium"]["anchor"]["field"] == "policy_number"
    assert "3 of 3 found fields carry extraction confidence 0.85" in by_sev["low"]["rationale"]

    # Negative: distinct extraction confidences, layers apart.
    assert _rules(_run(_report(_clean_fields())), "confidence_flattening") == []
    # Two found fields are too few for the share rule.
    two = [_field("a", value="v", node="el-1", extraction_confidence=0.5), _field("b", value="v", node="el-2", extraction_confidence=0.5)]
    assert _rules(_run(_report(two)), "confidence_flattening") == []


def test_fallback_rendering_high_for_text_or_fallback_renderer():
    report = _report(_clean_fields())
    for es in ({"renderer": "text"}, {"engine": "fallback"}, {"renderer": "weasyprint", "fallback": True}):
        hits = _rules(_run(report, export_state={"trust_state": "verified", **es}), "fallback_rendering")
        assert len(hits) == 1 and hits[0]["severity"] == "high" and hits[0]["class"] == "export", es
    assert _rules(_run(report, export_state={"trust_state": "verified", "renderer": "weasyprint"}), "fallback_rendering") == []


# ---------------------------------------------------------------------------
# Shape, ordering, attach
# ---------------------------------------------------------------------------


def test_every_finding_has_the_shape_and_a_non_empty_anchor_and_ids_are_per_class():
    fields = _clean_fields()
    fields[0]["field_source_node_id"] = None
    report = _report(fields, conflicts=[{"field": "vin", "values": []}], classification={"document_type": "auto_policy", "validation": {"agrees": False, "family": "medical", "type_family": "auto"}, "schema_mismatch": True})
    out = rg.critique_report(report, llm=False, export_state={"trust_state": "not_verified", "title": "Certificate", "renderer": "text"})
    assert out["policy"] == "rh-graph-v1" and out["ran_at"] and set(out["counts"]) == {"high", "medium", "low"} and set(out["classes"]) == {"structural", "evidentiary", "export"}
    seen_ids = set()
    for f in out["findings"]:
        assert set(f) >= {"id", "rule", "policy", "title", "severity", "class", "anchor", "rationale"}
        assert f["severity"] in rg.SEVERITIES and f["class"] in rg.CLASSES and f["policy"] == "rh-graph-v1"
        assert f["id"].startswith(f"rh-{f['class']}-") and f["id"] not in seen_ids
        seen_ids.add(f["id"])
        assert f["anchor"]["node_id"], f  # never empty
        assert f["rationale"].endswith(".") and "\n" not in f["rationale"]
    sev = [f["severity"] for f in out["findings"]]
    assert sev == sorted(sev, key=rg._SEV_ORDER.__getitem__)  # high first
    assert out["counts"]["high"] >= 3 and out["classes"]["export"] == 2


def test_attach_findings_writes_the_block_and_prepends_high_reasons_once():
    report = _report(_clean_fields(), conflicts=[{"field": "insured_name", "values": []}, {"field": "vin", "values": []}])
    out = rg.critique_report(report, llm=False)
    block = rg.attach_findings(report, out)
    assert report["redhat"] is block
    assert block["policy"] == "rh-graph-v1" and block["counts"]["high"] == 2 and block["classes"]["evidentiary"] >= 2
    assert block["notes"] == out["notes"] and block["ran_at"] == out["ran_at"]
    reasons = report["review_summary"]["reasons"]
    assert reasons[0]["reason"].startswith("Red-Hat: ") and reasons[1]["reason"].startswith("Red-Hat: ")
    assert reasons[2] == {"reason": "compliance-bound field", "count": 1}
    # Re-attaching replaces the Red-Hat reasons instead of stacking them.
    rg.attach_findings(report, [])
    assert report["redhat"]["counts"] == {"high": 0, "medium": 0, "low": 0}
    assert report["review_summary"]["reasons"] == [{"reason": "compliance-bound field", "count": 1}]
    # A bare list of findings is accepted and numbered.
    rg.attach_findings(report, [{"rule": "x", "title": "T", "severity": "high", "class": "export", "anchor": {"node_id": "doc-1"}, "rationale": "R."}])
    assert report["redhat"]["findings"][0]["id"] == "rh-export-1" and report["review_summary"]["reasons"][0] == {"reason": "Red-Hat: T", "count": 1}


def test_findings_view_and_anchor_labels():
    assert rg.findings_view({})["ran"] is False and "no critique is recorded" in rg.findings_view({})["reason"]
    assert rg.findings_view(None)["count"] == 0
    assert rg.anchor_label({"kind": "field", "field": "insured_name", "page": 2, "element_id": "p2e3", "node_id": "p-abc"}) == "Field insured name · Page 2 · p2e3"
    assert rg.anchor_label({"kind": "page", "page": 2, "node_id": "p2e0"}) == "Page 2 · p2e0"
    assert rg.anchor_label({"kind": "document_root", "node_id": "doc-1"}) == "Document root · doc-1"
    report = _report(_clean_fields(), conflicts=[{"field": "vin", "values": []}])
    rg.attach_findings(report, rg.critique_report(report, llm=False))
    view = rg.findings_view(report)
    assert view["ran"] and view["high"] == 1 and view["findings"][0]["anchor_label"] == "Field vin · Page 1 · el-4"


def test_a_failing_rule_is_a_note_not_an_exception(monkeypatch):
    def boom(ctx):
        raise RuntimeError("rule bug")

    monkeypatch.setattr(rg, "RULES", (boom, rg.cross_document_conflict))
    out = rg.critique_report(_report(_clean_fields(), conflicts=[{"field": "vin", "values": []}]), llm=False)
    assert out["counts"]["high"] == 1 and any("boom did not run: RuntimeError: rule bug" in n for n in out["notes"])


def test_the_real_report_shape_from_build_report_yields_no_high_finding_on_a_clean_text_layer():
    from tests.test_v1_orchestrator import RESULT, VERIFICATION, jdf_cli_bundle
    from prompt_matrix.services.v1_orchestrator import build_report

    report = build_report("p", bundle=jdf_cli_bundle(), verification=VERIFICATION, filename="policy.pdf", result=RESULT, job_id=None, intake=None)
    out = rg.critique_report(report, llm=False)
    assert out["counts"]["high"] == 0, [f["title"] for f in out["findings"] if f["severity"] == "high"]
    assert all(f["anchor"]["node_id"] for f in out["findings"])


# ---------------------------------------------------------------------------
# Grounded model check
# ---------------------------------------------------------------------------

_TEXT = "Policy Number: PR-1\nNamed Insured: Jane Q. Public\nAnnual Premium: $1,486.00\nVIN: 1HGCM82633A004352\n"


def _llm_report():
    return _report(_clean_fields(), texts=[_TEXT])


def test_model_check_supported_answer_yields_no_finding_and_a_note(monkeypatch):
    monkeypatch.delenv("PARSURE_REDHAT_LLM", raising=False)
    seen = {}

    def completion(prompt):
        seen["prompt"] = prompt
        return json.dumps({"unsupported": []})

    out = rg.critique_report(_llm_report(), llm=True, completion=completion)
    assert _rules(out["findings"], "unsupported_claim") == []
    assert any("unsupported-claim check ran (injected completion): 0 grounded findings, 0 dropped" in n for n in out["notes"])
    assert "insured_name" in seen["prompt"] and "=== PAGE 1 ===" in seen["prompt"] and "Named Insured: Jane Q. Public" in seen["prompt"]


def test_model_check_unsupported_with_verbatim_quote_is_a_grounded_finding():
    answer = json.dumps({"unsupported": [{"field": "insured_name", "quote": "named insured:   jane q. public", "why": "the extracted name is a fragment"}]})
    out = rg.critique_report(_llm_report(), llm=True, completion=lambda p: answer)
    hits = _rules(out["findings"], "unsupported_claim")
    assert len(hits) == 1 and hits[0]["severity"] == "medium" and hits[0]["class"] == "evidentiary"
    assert hits[0]["anchor"]["field"] == "insured_name" and hits[0]["anchor"]["page"] == 1 and hits[0]["anchor"]["node_id"] == "el-2"
    assert "Named Insured" in hits[0]["rationale"] or "named insured" in hits[0]["rationale"]
    assert "the extracted name is a fragment" in hits[0]["rationale"] and hits[0]["model"] == "injected completion"


def test_model_check_drops_findings_without_a_verbatim_quote_or_unknown_field():
    answer = json.dumps({"unsupported": [
        {"field": "insured_name", "quote": "The insured is John Doe", "why": "invented"},
        {"field": "insured_name", "why": "no quote at all"},
        {"field": "not_a_field", "quote": "Policy Number: PR-1", "why": "unknown field"},
    ]})
    out = rg.critique_report(_llm_report(), llm=True, completion=lambda p: answer)
    assert _rules(out["findings"], "unsupported_claim") == []
    assert any("0 grounded findings, 3 dropped without a verbatim quote" in n for n in out["notes"])


def test_model_check_is_bounded_and_never_raises():
    import time

    def slow(prompt):
        time.sleep(2)
        return "{}"

    out = rg.critique_report(_llm_report(), llm=True, completion=slow, timeout_s=0.2)
    assert _rules(out["findings"], "unsupported_claim") == [] and any("model unavailable (timed out after 0.2s)" in n for n in out["notes"])

    def broken(prompt):
        raise ConnectionError("refused")

    out = rg.critique_report(_llm_report(), llm=True, completion=broken)
    assert any("model unavailable" in n and "refused" in n for n in out["notes"])
    out = rg.critique_report(_llm_report(), llm=True, completion=lambda p: "not json")
    assert any("not the expected JSON" in n for n in out["notes"])


def test_model_check_skips_with_a_reason(monkeypatch):
    monkeypatch.setenv("PARSURE_REDHAT_LLM", "0")
    out = rg.critique_report(_llm_report(), completion=lambda p: "{}")
    assert any("skipped: PARSURE_REDHAT_LLM=0" in n for n in out["notes"])
    monkeypatch.delenv("PARSURE_REDHAT_LLM", raising=False)
    out = rg.critique_report(_report(_clean_fields()), completion=lambda p: "{}")  # no page text
    assert any("skipped: no page text on the report" in n for n in out["notes"])
    out = rg.critique_report(_report([_field("vin", value=None)], texts=[_TEXT]), completion=lambda p: "{}")
    assert any("skipped: no found field to check" in n for n in out["notes"])
    monkeypatch.setattr(rg, "backend_available", lambda: (False, "no key"))
    out = rg.critique_report(_llm_report())
    assert any("skipped: no model backend (no key)" in n for n in out["notes"])
