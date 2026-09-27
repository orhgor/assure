"""Export filename convention (plan Part 8.5): type, document id, the key data
points that were actually found, a UTC timestamp; slugified, ≤ 150 chars."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from prompt_matrix.services import export_names as en
from tests.test_parsure_routes import _seed, client  # noqa: F401  (fixture)

NOW = datetime(2026, 9, 27, 15, 0, 0, tzinfo=timezone.utc)


def _report(doc_type="auto_policy", document_id="abc123def4567890", **values):
    fields = [{"name": n, "value": v, "field_state": "accepted"} for n, v in values.items()]
    return {"classification": {"document_type": doc_type}, "document_id": document_id, "fields": fields}


def test_filename_carries_type_document_and_found_points():
    r = _report(policy_number="AP-2025-0001", insured_name="John Q. Sample", effective_date="2025-01-15", vin="1HGCM82633A004352")
    assert en.build_export_filename(r, "json", now=NOW) == "auto-policy_doc-abc123def456_policy-AP-2025-0001_insured-John_Q_Sample_eff-2025-01-15_vin-1HGCM82633A004352_20260927T150000Z.json"


def test_only_found_points_appear_never_placeholders():
    r = _report("title", "def456")
    assert en.build_export_filename(r, "json", now=NOW) == "title_doc-def456_20260927T150000Z.json"
    r = _report("property_unknown", "77ab01", premium=2140.0)  # a money value is not a key point
    assert en.build_export_filename(r, "csv", now=NOW) == "property-unknown_doc-77ab01_20260927T150000Z.csv"
    r = _report("auto_claim", "x1")
    r["fields"] = [{"name": "policy_number", "value": None}, {"name": "claim_number", "value": "CLM-9", "field_state": "rejected"},
                   {"name": "claimant_name", "value": "Rosa Alvarez", "field_state": "unverified"}, {"name": "date_of_loss", "value": "2025-09-02", "field_state": "accepted"}]
    assert en.build_export_filename(r, "json", now=NOW) == "auto-claim_doc-x1_insured-Rosa_Alvarez_loss-2025-09-02_20260927T150000Z.json"
    assert en.build_export_filename({}, "json", now=NOW) == "document_20260927T150000Z.json"


def test_slug_is_filesystem_safe_and_ascii():
    assert en.slug("Ärger & Söhne / Ltd.") == "Arger_Sohne_Ltd"
    assert en.slug("../../etc/passwd") == "etc-passwd"
    assert en.slug("  a   b  ") == "a_b" and en.slug(None) == "" and en.slug("Ünïcödé") == "Unicode"
    name = en.build_export_filename(_report(insured_name="Zoë O'Brien-Smith Jr.", policy_number="P/1 2#3"), "json", now=NOW)
    assert re.fullmatch(r"[A-Za-z0-9._-]+", name) and "insured-Zoe_OBrien-Smith_Jr" in name and "policy-P-1_2-3" in name


def test_filename_is_capped_at_150_and_keeps_type_id_and_timestamp():
    r = _report("real_estate_transaction_closing_disclosure_statement", "d" * 40, policy_number="P" * 60, insured_name="N " * 40,
                effective_date="2025-01-15", date_of_loss="2025-02-02", vin="1HGCM82633A004352")
    name = en.build_export_filename(r, "json", now=NOW)
    assert len(name) <= en.MAX_FILENAME and name.endswith("_20260927T150000Z.json") and name.startswith("real-estate-transaction-closing-disclosure-statement_doc-dddddddddddd")
    assert "policy-" in name  # the first data point survives; later ones are dropped from the right
    tiny = en.build_export_filename(_report("x" * 200, "y" * 50), "json", now=NOW)
    assert len(tiny) <= en.MAX_FILENAME and tiny.endswith("_20260927T150000Z.json")
    with_suffix = en.build_export_filename(_report(), "pdf", suffix="verification dossier", now=NOW)
    assert with_suffix == "auto-policy_doc-abc123def456_verification_dossier_20260927T150000Z.pdf"


def test_project_export_filename_names_what_it_holds():
    docs = [{}, {}, {}]
    assert en.build_project_export_filename("default", docs, "csv", now=NOW) == "parsure_default_3-docs_20260927T150000Z.csv"
    assert en.build_project_export_filename("proj/../x", docs, "json", document_type="auto_policy", state="needs_review", now=NOW) == \
        "parsure_proj-..-x_3-docs_auto-policy_needs_review_20260927T150000Z.json"


def test_report_and_project_export_routes_use_the_convention(client):  # noqa: F811
    rid = _seed()
    res = client.get(f"/api/projects/default/parsure/{rid}/export?format=json")
    assert res.status_code == 200
    disposition = res.headers["Content-Disposition"]
    m = re.fullmatch(r'attachment; filename="([^"]+)"', disposition)
    assert m, disposition
    name = m.group(1)
    assert name.startswith("auto-policy_doc-doc-default_policy-AP-2025-0001_insured-John_Q_Sample_eff-2025-01-15_vin-1HGCM82633A004352_")
    assert re.search(r"_\d{8}T\d{6}Z\.json$", name) and len(name) <= en.MAX_FILENAME
    csv = client.get(f"/api/projects/default/parsure/{rid}/export?format=csv")
    assert re.search(r'filename="auto-policy_doc-doc-default_.*\.csv"$', csv.headers["Content-Disposition"])
    proj = client.get("/api/projects/default/parsure/export?format=json&document_type=auto_policy")
    assert re.search(r'filename="parsure_default_1-docs_auto-policy_\d{8}T\d{6}Z\.json"$', proj.headers["Content-Disposition"])
