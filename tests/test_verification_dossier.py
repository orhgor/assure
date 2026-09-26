"""The Verification Dossier states the run's real state — title, band and sections.

Customer QA of 2026-09-26: a PDF titled "Formal Verification Certificate" for a run
whose JSON read ``fields_accepted 0 / fields_review 12``, a cross-document conflict
on ``insured_name`` and ``signature_quality.quality = questionable``, while the PDF
said "No cross-run contradictions detected" and "No Red-Hat findings recorded".
These tests seed exactly that run and read the dossier back.
"""

from __future__ import annotations

import json

import pytest

from prompt_matrix.services import verification_dossier as vd
from prompt_matrix.services.verification_dossier import (
    build_dossier,
    derive_trust_state,
    verification_state_json,
)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "dossier.db"))
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db

    init_db()
    yield


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------

_BASE = dict(
    gate_status="pass",
    z3_status="PASS",
    claims_unsupported=0,
    accepted_total=3,
    open_review=0,
    open_disputes=0,
    conflicts=0,
    redhat_open=0,
    gate_unverified=False,
)


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({}, "verified"),
        ({"gate_status": "blocked"}, "not_verified"),
        ({"z3_status": "VIOLATION"}, "not_verified"),
        ({"claims_unsupported": 1}, "not_verified"),
        # The customer's run: 12 in review, 0 accepted anywhere.
        ({"accepted_total": 0, "open_review": 12, "conflicts": 1}, "not_verified"),
        ({"open_review": 5}, "review_required"),
        ({"open_disputes": 1}, "review_required"),
        ({"conflicts": 1}, "review_required"),
        ({"redhat_open": 2}, "review_required"),
        ({"gate_status": "review"}, "review_required"),
        ({"gate_unverified": True}, "review_required"),
        # Contradiction outranks "something is open".
        ({"claims_unsupported": 2, "open_review": 4}, "not_verified"),
    ],
)
def test_trust_state_table(overrides, expected):
    state, reasons = derive_trust_state(**{**_BASE, **overrides})
    assert state == expected
    assert bool(reasons) == (expected != "verified")


def test_titles_use_the_word_certificate_only_when_verified():
    assert "Certificate" not in vd.TRUST_TITLES["review_required"]
    assert "Certificate" not in vd.TRUST_TITLES["not_verified"]
    assert "certificate" not in vd.TRUST_SUBTITLES["review_required"].lower()
    assert "certificate" not in vd.TRUST_SUBTITLES["not_verified"].lower()
    assert "Certificate" in vd.TRUST_SUBTITLES["verified"]


# ---------------------------------------------------------------------------
# The customer's run, seeded
# ---------------------------------------------------------------------------


def _field(name, *, value="x", state="unverified", routing="manual_review", reason="compliance-bound field — human confirmation required", confidence=0.62, **extra):
    field = {
        "name": name,
        "label": name.replace("_", " ").capitalize(),
        "value": value,
        "field_state": state,
        "routing_action": routing,
        "review_required": routing != "none",
        "extraction_confidence": confidence,
        "reason": reason,
        "source_span": {"page": 1},
    }
    field.update(extra)
    return field


_QUESTIONABLE = {
    "present": True,
    "quality": "questionable",
    "review_required": True,
    "page": 1,
    "basis": "label 'Signature'; a mark beyond the label exists (marks 0.0210 vs label 0.0080, darkness 0.31) but the V1 heuristic cannot confirm a handwritten signature",
}


def _report(report_id, filename, *, fields, insured, signature=_QUESTIONABLE, accepted=0, flags=("low_res",), quality=0.41):
    review = sum(1 for f in fields if f["routing_action"] != "none")
    return {
        "report_id": report_id,
        "document_id": f"doc-{report_id}",
        "filename": filename,
        "material_type": "image",
        "modality": "phone_photo",
        "parser_name": "jdf-cli+tesseract",
        "parser_version": "0.2.3",
        "document_quality_score": quality,
        "quality_flags": list(flags),
        "pages": [{"page": 1, "quality_score": quality, "flags": list(flags), "ocr_confidence": 0.71}],
        "classification": {"document_type": "auto_policy", "confidence": 0.75},
        "fields": fields,
        "conflicts": [],
        "review_summary": {
            "fields_total": len(fields),
            "fields_found": sum(1 for f in fields if f["value"] is not None),
            "fields_accepted": accepted,
            "fields_review": review,
            "fields_rejected": 0,
            "fields_disputed": 0,
            "reasons": [],
        },
        "quality_report": {
            "summary": "Low resolution on 1 of 1 page; signature is questionable.",
            "flags": list(flags),
            "signature": signature,
            "text_chars": 812,
        },
        "verification": {"z3_status": "PASS", "redhat_status": "complete", "z3_violation_count": 0},
        "laya": {
            "suggested_route": "human_review",
            "human_review": True,
            "escalate": False,
            "model": "rules-v1",
            "reasons": ["low_res on 1 of 1 page"],
        },
        "replay": {"eligible": True, "reasons": ["low extraction confidence (< 0.7) on 12 field(s)"], "replayed": False},
    }


def _seed_customer_run(project_id: str):
    """Two documents disagreeing on insured_name, twelve fields in review, none accepted."""
    from prompt_matrix.db import parsure_repository as repo
    from prompt_matrix.db.jdf_repository import ensure_project
    from prompt_matrix.services.field_extractor import cross_document_conflicts

    ensure_project(project_id, "Customer run")
    names = [
        "policy_number", "insured_name", "vin", "premium", "liability_limit", "collision_deductible",
        "comprehensive_deductible", "effective_date", "expiration_date", "vehicle_year", "vehicle_make", "signature",
    ]
    fields_a = [_field(n, value=("Jane Q. Public" if n == "insured_name" else f"{n}-a")) for n in names]
    fields_a[-1].update(field_type="signature", value=None, signature_quality=_QUESTIONABLE, reason="signature questionable — manual review required")
    fields_b = [_field("insured_name", value="Jane Public-Smith", state="accepted", routing="none", reason="", confidence=0.9)]
    a = _report("pr-a", "policy-photo.jpg", fields=fields_a, insured="Jane Q. Public")
    b = _report("pr-b", "renewal.pdf", fields=fields_b, insured="Jane Public-Smith", accepted=1, flags=(), quality=1.0,
                signature={"present": None, "quality": "unknown", "review_required": False, "page": None,
                           "basis": "no signature label in page text; presence not assessable in V1"})
    b["replay"] = {"eligible": False, "reasons": [], "replayed": False}
    conflicts = cross_document_conflicts([a, b])
    assert conflicts and conflicts[0]["field"] == "insured_name"
    a["conflicts"] = conflicts
    b["conflicts"] = conflicts
    repo.save_report(project_id, a)
    repo.save_report(project_id, b)
    return a, b


def test_the_customer_run_is_not_a_certificate(db):
    a, _b = _seed_customer_run("qa-run")
    built = build_dossier("qa-run")
    state, html = built["state"], built["html"]

    # 12 fields in review on the photo, 1 accepted on the renewal; nothing locked or supported.
    assert state["counts"]["fields_review"] == 12
    assert state["counts"]["fields_accepted"] == 1
    assert state["counts"]["conflicts"] == 1
    assert state["trust_state"] == "review_required"
    assert state["title"] == "Verification Dossier — Review required"
    assert "12 fields need review · 1 accepted · 1 conflict · signature questionable" in state["status_band"]
    assert "Red-Hat: not run" in state["status_band"]
    assert "Certificate" not in html
    assert "certificate" not in html.lower()

    # The conflict names both values and both documents, and says why.
    assert "Jane Q. Public" in html and "Jane Public-Smith" in html
    assert "policy-photo.jpg" in html and "renewal.pdf" in html
    assert "a shared identifier must carry one value per project" in html
    assert "No cross-run contradictions detected" not in html
    assert "fewer than two runs recorded" in html

    # The signature is a first-class unresolved item with the measurement verbatim.
    sig = state["sections"]["signature"]
    assert sig["status"] == "unresolved"
    by_doc = {item["document"]: item for item in sig["items"]}
    assert by_doc["policy-photo.jpg"]["status"] == "present but questionable"
    assert by_doc["policy-photo.jpg"]["unresolved"] is True
    assert by_doc["renewal.pdf"]["status"] == "not assessed"
    assert "present but questionable" in html
    assert "cannot confirm a handwritten signature" in html

    # Review items carry reason and confidence; quality and replay carry the JSON's numbers.
    review = state["sections"]["review_required"]
    assert review["count"] == 12
    assert all(item["reason"] for item in review["items"])
    assert "62%" in html  # 0.62 extraction confidence
    assert "low_res" in html and "0.41" in html and "812 characters of text" in html
    assert "low extraction confidence (&lt; 0.7) on 12 field(s)" in html
    assert "human_review" in html  # Laya's suggested route
    # The intake's own Z3 result is printed as what it is; the "complete" Red-Hat
    # status is a shape pass and is not allowed to read as an audit.
    assert "Parse-time verification of this document: Z3 PASS (0 violations)" in html
    assert "a shape pass, not an audit" in html
    assert state["intake"]["documents"][0]["verification"]["z3_status"] == "PASS"
    assert state["sections"]["replay"]["eligible"] == 1

    # A denial for a check that did not run is never printed.
    assert "No Red-Hat findings recorded" not in html
    assert "No locked claims recorded" not in html
    assert "0 locked claims" in html


def test_nothing_accepted_anywhere_is_not_verified(db):
    from prompt_matrix.db import parsure_repository as repo
    from prompt_matrix.db.jdf_repository import ensure_project

    ensure_project("qa-zero", "Zero accepted")
    fields = [_field(f"f{i}") for i in range(12)]
    repo.save_report("qa-zero", _report("pr-z", "scan.pdf", fields=fields, insured="x"))
    state = build_dossier("qa-zero")["state"]
    assert state["counts"]["fields_accepted"] == 0
    assert state["counts"]["fields_review"] == 12
    assert state["trust_state"] == "not_verified"
    assert state["title"] == "Verification Dossier — Not verified"
    assert state["status_band"].startswith("12 fields need review · 0 accepted")
    assert "nothing has been accepted" in " ".join(state["reasons"])


def test_open_dispute_is_listed_with_its_due_words(db):
    from prompt_matrix.db import parsure_repository as repo

    _seed_customer_run("qa-dispute")
    repo.open_dispute("qa-dispute", "pr-a", "premium", reason="premium disagrees with the invoice", actor="reviewer")
    built = build_dossier("qa-dispute")
    disputes = built["state"]["sections"]["disputes"]
    assert disputes["count"] == 1 and disputes["overdue"] == 0
    assert disputes["items"][0]["field"] == "premium"
    assert disputes["items"][0]["due_words"].startswith("due in")
    assert "1 open dispute" in built["state"]["status_band"]
    assert "premium disagrees with the invoice" in built["html"]


def test_redhat_zero_findings_is_distinguished_from_not_run(db, monkeypatch):
    from prompt_matrix.db.jdf_repository import ensure_project

    ensure_project("qa-redhat", "Red-Hat words")
    ran_clean = {"items": [], "count": 0, "ran": True, "reason": "", "other_revisions": [], "in_export": False}
    monkeypatch.setattr(vd, "project_redhat_findings", lambda pid, tree: ran_clean)
    built = build_dossier("qa-redhat")
    assert built["state"]["sections"]["redhat"]["status"] == "clear"
    assert built["state"]["counts"]["redhat_findings"] == 0
    assert "Red-Hat: 0 findings" in built["state"]["status_band"]
    assert "Red-Hat ran and recorded 0 findings" in built["html"]

    not_run = {"items": [], "count": 0, "ran": False, "reason": "no Red-Hat audit was requested for this compile", "other_revisions": [], "in_export": False}
    monkeypatch.setattr(vd, "project_redhat_findings", lambda pid, tree: not_run)
    built = build_dossier("qa-redhat")
    assert built["state"]["sections"]["redhat"]["status"] == "not_run"
    assert built["state"]["counts"]["redhat_findings"] is None
    assert "Red-Hat: not run" in built["state"]["status_band"]
    assert "Not run — No Red-Hat audit was requested" in built["html"]
    assert "0 findings" not in built["html"]

    with_findings = {
        "items": [{"severity": "high", "title": "Omitted carve-out", "message": "The memo omits the carve-out the source states.", "status": "open", "node_id": "p1", "run_id": "", "source": "annotations.redhat"}],
        "count": 1, "ran": True, "reason": "", "other_revisions": [], "in_export": True,
    }
    monkeypatch.setattr(vd, "project_redhat_findings", lambda pid, tree: with_findings)
    built = build_dossier("qa-redhat")
    assert "Red-Hat: 1 finding" in built["state"]["status_band"]
    assert "omits the carve-out" in built["html"]
    assert built["state"]["trust_state"] != "verified"


def test_verified_state_requires_everything_closed(db, monkeypatch):
    from prompt_matrix.db import parsure_repository as repo
    from prompt_matrix.db.jdf_repository import ensure_project

    ensure_project("qa-verified", "All closed")
    fields = [_field(f"f{i}", state="accepted", routing="none", reason="", confidence=0.95) for i in range(4)]
    stamped = {"present": True, "quality": "stamped", "review_required": False, "page": 1, "basis": "label 'Signature'; STAMP next to the label"}
    repo.save_report("qa-verified", _report("pr-v", "clean.pdf", fields=fields, insured="x", signature=stamped, accepted=4, flags=(), quality=1.0))
    passing = {
        "gate_status": "pass", "z3_status": "PASS", "redhat_count": 0, "unverified": False, "unverified_reason": "",
        "provenance_stats": {"eligible": 3, "anchored": 3, "supported": 3, "partial": 0, "unsupported": 0, "unanchored": 0, "unverified": 0},
    }
    monkeypatch.setattr(vd, "compute_export_gate", lambda pid, tree: passing)
    monkeypatch.setattr(vd, "project_redhat_findings", lambda pid, tree: {"items": [], "count": 0, "ran": True, "reason": "", "other_revisions": [], "in_export": False})
    built = build_dossier("qa-verified")
    state = built["state"]
    assert state["trust_state"] == "verified", state["reasons"]
    assert state["title"] == "Verification Dossier — Verified"
    assert "Certificate" in built["html"]
    assert "0 fields need review · 4 accepted · 0 conflicts · signature stamped · Red-Hat: 0 findings" in state["status_band"]

    # The same run with the signature questionable is no longer verified.
    repo.save_report("qa-verified", _report("pr-v", "clean.pdf", fields=fields, insured="x", signature=_QUESTIONABLE, accepted=4, flags=(), quality=1.0))
    state = build_dossier("qa-verified")["state"]
    assert state["trust_state"] == "review_required"
    assert "signature is unresolved" in " ".join(state["reasons"])


def test_the_json_twin_is_the_dict_the_html_was_rendered_from(db):
    _seed_customer_run("qa-twin")
    built = build_dossier("qa-twin")
    state = built["state"]
    twin = json.loads(verification_state_json(state))
    assert twin == json.loads(json.dumps(state, default=str))
    assert twin["schema"] == vd.STATE_SCHEMA
    # Every headline figure the JSON carries is printed in the HTML.
    assert twin["title"] in built["html"]
    assert twin["status_band"] in built["html"]
    assert twin["document"]["content_hash"] in built["html"]
    for reason in twin["reasons"]:
        assert reason in built["html"]


def test_no_intake_report_says_not_run_not_none(db):
    from prompt_matrix.db.jdf_repository import ensure_project

    ensure_project("qa-empty", "Empty")
    built = build_dossier("qa-empty")
    state = built["state"]
    assert state["intake"]["present"] is False
    assert state["sections"]["conflicts"]["status"] == "not_run"
    assert state["sections"]["signature"]["status"] == "not_run"
    assert state["sections"]["quality"]["status"] == "not_run"
    assert "no intake report · conflicts: not run · signature not assessed" in state["status_band"]
    assert "Not run — no intake report" in built["html"]
    assert "detected" not in built["html"].lower()


# ---------------------------------------------------------------------------
# Intake graph critique (services/redhat_graph) — the second Red-Hat pass
# ---------------------------------------------------------------------------


def _seed_with_intake_critique(project_id: str, *, conflicts: bool):
    """The customer run, its reports carrying an rh-graph-v1 block; with
    ``conflicts=False`` the block is attached to a clean single report."""
    from prompt_matrix.db import parsure_repository as repo
    from prompt_matrix.db.jdf_repository import ensure_project
    from prompt_matrix.services import redhat_graph as rg

    if conflicts:
        a, b = _seed_customer_run(project_id)
        for r in (a, b):
            rg.attach_findings(r, rg.critique_report(r, llm=False))
            repo.update_report(project_id, r["report_id"], r)
        return a, b
    ensure_project(project_id, "Clean run")
    fields = [_field("insured_name", value="Jane", state="accepted", routing="none", reason="", confidence=0.9,
                     field_source_node_id="el-1", evidence_state="found_verified", verification_confidence=1.0, provenance_confidence=1.0)]
    r = _report("pr-clean", "renewal.pdf", fields=fields, insured="Jane", accepted=1, flags=(), quality=1.0,
                signature={"present": None, "quality": "unknown", "review_required": False, "page": None, "basis": "no signature label"})
    r["graph_integrity"] = {"fields": 1, "anchored": 1, "orphans": 0, "basis": "every field names a JDF node"}
    r["node_id_policy"] = "jdf-cli element ids"
    r["classification"]["validation"] = {"agrees": True, "family": "auto", "type_family": "auto"}
    rg.attach_findings(r, rg.critique_report(r, llm=False))
    repo.save_report(project_id, r)
    return r, None


def test_intake_critique_findings_are_listed_beside_the_draft_audit_and_a_high_one_blocks_verified(db, monkeypatch):
    """Two passes, two labels: the draft audit (project_redhat_findings) and
    the intake graph critique (report["redhat"]). The customer's conflict on
    insured_name is a high intake finding; it alone must keep the export
    from reading verified."""
    a, _b = _seed_with_intake_critique("qa-rh-intake", conflicts=True)
    assert a["redhat"]["counts"]["high"] >= 1 and a["redhat"]["policy"] == "rh-graph-v1"
    ran_clean = {"items": [], "count": 0, "ran": True, "reason": "", "other_revisions": [], "in_export": False}
    monkeypatch.setattr(vd, "project_redhat_findings", lambda pid, tree: ran_clean)
    built = build_dossier("qa-rh-intake")
    state, html = built["state"], built["html"]
    section = state["sections"]["redhat"]
    assert section["status"] == "clear" and section["draft_label"] == "draft audit" and section["intake_label"] == "intake graph critique"
    intake = section["intake"]
    assert intake["status"] == "findings" and intake["reports_run"] == 2 and intake["policy"] == "rh-graph-v1"
    assert intake["high"] >= 2  # one conflict finding per report
    titles = {i["title"] for i in intake["items"]}
    assert "Insured name conflicts with another document" in titles
    item = next(i for i in intake["items"] if i["rule"] == "cross_document_conflict")
    assert item["severity"] == "high" and item["class"] == "evidentiary" and item["report_id"] in ("pr-a", "pr-b")
    # The customer's fields carried no node id, so the anchor is the document root — named as such, with the field it concerns.
    assert item["anchor"]["kind"] == "document_root" and item["anchor"]["field"] == "insured_name"
    assert item["where"] == f"Field insured name · Page 1 · Document root · doc-{item['report_id']}"
    assert state["counts"]["redhat_intake_findings"] == intake["count"] and state["counts"]["redhat_intake_high"] == intake["high"]
    assert f"intake critique: {intake['count']} findings ({intake['high']} high)" in state["status_band"]
    # The HTML carries both passes, labelled, and the intake rows.
    assert "Draft audit — Red-Hat multipass over the compiled draft" in html
    assert "Intake graph critique — rules over the intake evidence graph" in html
    assert "Red-Hat ran and recorded 0 findings" in html
    assert "Insured name conflicts with another document" in html and "Field insured name" in html
    assert "intake graph critique:" in html and "draft audit:" in html
    assert state["trust_state"] != "verified"


def test_a_high_intake_finding_alone_makes_review_required_never_verified(db, monkeypatch):
    """Everything else closed, one high intake finding → review_required with
    the reason named; the same run with no high finding → verified."""
    from prompt_matrix.db import parsure_repository as repo

    r, _ = _seed_with_intake_critique("qa-rh-high", conflicts=False)
    assert r["redhat"]["counts"]["high"] == 0
    monkeypatch.setattr(vd, "compute_export_gate", lambda pid, tree: {"gate_status": "pass", "z3_status": "PASS", "unverified": False, "provenance_stats": {"eligible": 0, "anchored": 0, "supported": 0, "unsupported": 0}, "violations": []})
    monkeypatch.setattr(vd, "project_redhat_findings", lambda pid, tree: {"items": [], "count": 0, "ran": True, "reason": "", "other_revisions": [], "in_export": False})
    monkeypatch.setattr(vd, "list_runs", lambda workspace_id: [])
    state = build_dossier("qa-rh-high")["state"]
    assert state["trust_state"] == "verified", state["reasons"]
    assert state["sections"]["redhat"]["intake"]["status"] == "clear" and state["counts"]["redhat_intake_findings"] == 0
    assert "intake critique: 0 findings" in state["status_band"]
    assert "recorded 0 findings" in build_dossier("qa-rh-high")["html"]

    # Now one high finding on the intake graph (a certificate-language export state judged at export time).
    from prompt_matrix.services import redhat_graph as rg

    rg.attach_findings(r, rg.critique_report(r, llm=False, export_state={"trust_state": "review_required", "title": "Formal Verification Certificate"}))
    assert r["redhat"]["counts"]["high"] == 1
    repo.update_report("qa-rh-high", r["report_id"], r)
    state = build_dossier("qa-rh-high")["state"]
    assert state["trust_state"] == "review_required"
    assert "1 high Red-Hat finding on the intake graph" in state["reasons"]
    assert state["title"] == "Verification Dossier — Review required"


def test_intake_critique_not_run_is_said_not_counted_as_zero(db):
    """Reports saved without a block (before the critique existed) read
    "not run" with the reason; the draft audit's words are untouched."""
    _seed_customer_run("qa-rh-none")
    state = build_dossier("qa-rh-none")["state"]
    intake = state["sections"]["redhat"]["intake"]
    assert intake["status"] == "not_run" and "no critique is recorded on the 2 intake reports" in intake["reason"]
    assert state["counts"]["redhat_intake_findings"] is None and state["counts"]["redhat_intake_high"] is None
    assert "intake critique: not run" in state["status_band"]
    html = build_dossier("qa-rh-none")["html"]
    assert "Not run — No critique is recorded on the 2 intake reports." in html
    assert derive_trust_state(**_BASE, intake_redhat_high=1) == ("review_required", ["1 high Red-Hat finding on the intake graph"])
    assert derive_trust_state(**_BASE, intake_redhat_high=0) == ("verified", [])
