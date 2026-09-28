"""Schema registry (plan Part 4.1/4.4/7.3): the taxonomy is built from the
static specs plus runtime JSON, bad files are skipped with a reason, a new
document type needs no code change, and the four benchmark schema gaps route."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from prompt_matrix.services import field_discovery as fd
from prompt_matrix.services import field_extractor as fx
from prompt_matrix.services import schema_registry as sr
from prompt_matrix.services import v1_orchestrator as orch
from tests.test_parsure_routes import client  # noqa: F401  (fixture)

BENCH = Path(__file__).resolve().parents[1] / "bench"
RUNTIME_TYPES = ("endorsement", "cancellation_notice", "repair_estimate", "schedule_of_forms")


@pytest.fixture(autouse=True)
def _restore_registry():
    yield
    for name in list(sr._IN_MEMORY):
        sr._IN_MEMORY.pop(name, None)
    sr.reload()


def _bench_texts():
    sys.path.insert(0, str(BENCH))
    try:
        from cases import texts as T
    finally:
        sys.path.pop(0)
    return T


def test_builtins_and_runtime_schemas_are_one_taxonomy():
    reg = sr.registry()
    assert reg.rejected == []
    assert set(reg.builtin) == set(fx._BUILTIN_DOCUMENT_TYPES) and set(RUNTIME_TYPES) <= set(reg.runtime)
    # public names keep working and agree with the registry
    assert tuple(reg.schemas) == fx.DOCUMENT_TYPES
    assert fx.DOCUMENT_TYPES[: len(fx._BUILTIN_DOCUMENT_TYPES)] == fx._BUILTIN_DOCUMENT_TYPES  # built-ins keep their order (tie-breaks)
    for t in fx.DOCUMENT_TYPES:
        assert t in fx.FIELD_TAXONOMY and t in fx.TYPE_KEYWORDS and fx.TYPE_FAMILY[t] in fx.DOCUMENT_FAMILIES
        assert fx.type_allowed(t, fx.TYPE_FAMILY[t]) and not fx.type_allowed(t, next(f for f in fx.DOCUMENT_FAMILIES if f != fx.TYPE_FAMILY[t]))
        assert all(isinstance(s, fx.FieldSpec) and s.field_type in fx.FIELD_TYPES for s in fx.FIELD_TAXONOMY[t])
    assert fx.TYPE_FAMILY["endorsement"] == "auto" and fx.TYPE_FAMILY["schedule_of_forms"] == "property"
    # the built-in specs were not altered by the merge
    assert fx.FIELD_TAXONOMY["auto_policy"] == fx._BUILTIN_FIELD_TAXONOMY["auto_policy"]


def test_use_shortcut_copies_a_builtin_spec():
    schema = sr.get_schema("schedule_of_forms")
    dwelling = next(s for s in schema.fields if s.name == "dwelling_coverage")
    base = next(s for s in fx._BUILTIN_FIELD_TAXONOMY["property_policy"] if s.name == "dwelling_coverage")
    assert dwelling.anchors == base.anchors and dwelling.compliance_bound == base.compliance_bound and dwelling.field_type == "money"
    d = schema.to_dict(anchors=True)
    assert d["document_type"] == "schedule_of_forms" and d["source"].endswith("schedule_of_forms.json") and d["fields"][0]["anchors"]


def test_bad_schema_files_are_skipped_with_a_reason_never_a_crash(tmp_path, caplog):
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "bad_type.json").write_text(json.dumps({"document_type": "x", "family": "auto", "keywords": ["a", "b", "c"],
                                                        "fields": [{"name": "f", "field_type": "text", "anchors": ["f"]}]}))
    (tmp_path / "bad_family.json").write_text(json.dumps({"document_type": "umbrella", "family": "marine", "keywords": ["a", "b", "c"],
                                                          "fields": [{"name": "f", "field_type": "text", "anchors": ["f"]}]}))
    (tmp_path / "bad_field_type.json").write_text(json.dumps({"document_type": "umbrella", "family": "auto", "keywords": ["a", "b", "c"],
                                                              "fields": [{"name": "f", "field_type": "currency", "anchors": ["f"]}]}))
    (tmp_path / "bad_anchor.json").write_text(json.dumps({"document_type": "umbrella", "family": "auto", "keywords": ["a", "b", "c"],
                                                          "fields": [{"name": "f", "field_type": "money", "anchors": ["(?P<val>oops"]}]}))
    (tmp_path / "few_keywords.json").write_text(json.dumps({"document_type": "umbrella", "family": "auto", "keywords": ["a"],
                                                            "fields": [{"name": "f", "field_type": "money", "anchors": ["f"]}]}))
    (tmp_path / "reserved.json").write_text(json.dumps({"document_type": "auto_unknown", "family": "auto", "keywords": ["a", "b", "c"],
                                                        "fields": [{"name": "f", "field_type": "money", "anchors": ["f"]}]}))
    (tmp_path / "good.json").write_text(json.dumps({"document_type": "umbrella_policy", "family": "auto", "label": "Umbrella policy",
                                                    "keywords": ["umbrella", "excess liability", "underlying policies"],
                                                    "fields": [{"name": "umbrella_limit", "label": "Umbrella limit", "field_type": "money",
                                                                "anchors": ["umbrella\\s*(?:limit|amount)"], "compliance_bound": True}]}))
    reg = sr.reload([sr.BUILTIN_SCHEMA_DIR, tmp_path])
    reasons = {Path(r["path"]).name: r["reason"] for r in reg.rejected}
    assert set(reasons) == {"broken.json", "bad_type.json", "bad_family.json", "bad_field_type.json", "bad_anchor.json", "few_keywords.json", "reserved.json"}
    assert "JSON" in reasons["broken.json"] and "marine" in reasons["bad_family.json"] and "currency" in reasons["bad_field_type.json"]
    assert "does not compile" in reasons["bad_anchor.json"] and "at least 3" in reasons["few_keywords.json"] and "reserved" in reasons["reserved.json"]
    assert "umbrella_policy" in fx.DOCUMENT_TYPES and fx.TYPE_FAMILY["umbrella_policy"] == "auto"
    assert fx.FIELD_TAXONOMY["umbrella_policy"][0].compliance_bound is True
    assert "umbrella" not in fx.DOCUMENT_TYPES
    assert any("skipped" in rec.message for rec in caplog.records)
    sr.reload()
    assert "umbrella_policy" not in fx.DOCUMENT_TYPES


def test_env_dir_is_read_and_a_missing_dir_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setenv(sr.SCHEMA_DIR_ENV, str(tmp_path / "missing") + ":" + str(tmp_path))
    (tmp_path / "boat.json").write_text(json.dumps({"document_type": "boat_policy", "family": "auto", "keywords": ["boat", "hull", "watercraft"],
                                                    "fields": [{"name": "hull_value", "field_type": "money", "anchors": ["hull\\s+value"]}]}))
    reg = sr.reload()
    assert "boat_policy" in reg.schemas and str(tmp_path) in reg.dirs and str(tmp_path / "missing") in reg.dirs
    assert reg.rejected == []


def test_new_document_type_by_registration_needs_no_code_change():
    """Plan 7.3: register ``auto_umbrella_policy``, classify, extract."""
    sr.register_schema({
        "document_type": "auto_umbrella_policy", "family": "auto", "label": "Auto umbrella policy",
        "keywords": ["umbrella", "personal umbrella", "excess liability", "underlying policies", "underlying auto policy", "self-insured retention", "policy number", "named insured"],
        "fields": [
            {"name": "policy_number", "use": "auto_policy.policy_number"},
            {"name": "insured_name", "use": "auto_policy.insured_name"},
            {"name": "umbrella_limit", "label": "Umbrella limit", "field_type": "money", "anchors": ["umbrella\\s*(?:limit|amount)", "limit\\s+of\\s+liability"], "compliance_bound": True},
            {"name": "underlying_policies", "label": "Underlying policies", "field_type": "text", "anchors": ["underlying\\s+(?:policies|policy|auto\\s+policy)"]},
            {"name": "retention", "label": "Self-insured retention", "field_type": "money", "anchors": ["self-?insured\\s+retention", "retained\\s+limit"]},
            {"name": "signature", "use": "auto_policy.signature"},
        ],
    })
    text = ("PERSONAL UMBRELLA POLICY DECLARATIONS\nPolicy Number: UMB-77-2025\nNamed Insured: Dana Whitfield\n"
            "Vehicle: 2019 Subaru Outback, VIN 4S4BSAFC8K3312221\nUmbrella Limit: $1,000,000 excess liability\n"
            "Underlying Auto Policy: NAP-4471-2025 with Northstar Mutual\nSelf-Insured Retention: $250\nAuthorized Signature: /s/ Dana Whitfield\n")
    cls = fx.classify_document(text)
    assert cls["document_type"] == "auto_umbrella_policy", cls["basis"]
    fields = {f["name"]: f["value"] for f in fx.extract_fields("auto_umbrella_policy", [text], parser_name="jdf-cli", parse_confidence=None, ocr_confidence=None, page_quality=[None])}
    assert fields["policy_number"] == "UMB-77-2025" and fields["umbrella_limit"] == 1000000.0 and fields["retention"] == 250.0
    assert fields["underlying_policies"].startswith("NAP-4471-2025")
    assert sr.get_schema("auto_umbrella_policy").source == "runtime"
    assert sr.unregister_schema("auto_umbrella_policy") and "auto_umbrella_policy" not in fx.DOCUMENT_TYPES
    with pytest.raises(sr.SchemaError):
        sr.register_schema({"document_type": "nope", "family": "auto", "keywords": ["a", "b", "c"], "fields": []})


def test_the_four_benchmark_schema_gaps_route_and_extract():
    T = _bench_texts()
    cases = {
        "endorsement": (T.AUTO_ENDORSEMENT, {"policy_number": "NAP-4471-2025", "effective_date": "2025-09-01", "expiration_date": "2026-03-01",
                                             "vin": "4S4BSAFC8K3312221", "vehicle_year_make_model": "2019 Subaru Outback Premium", "premium": 212.0}),
        "cancellation_notice": (T.AUTO_CANCELLATION, {"policy_number": "NAP-4471-2025", "insured_name": "Daniel R. Whitfield", "effective_date": "2025-11-15",
                                                      "vin": "1HGCM82633A004352", "vehicle_year_make_model": "2003 Honda Accord EX Sedan", "amount_due": 248.0}),
        "repair_estimate": (T.REPAIR_ESTIMATE + "\nParts subtotal: $2,139.00\nLabor subtotal: $1,479.00\nTax (6.25% on parts): $133.69\n" + T.REPAIR_ESTIMATE_TOTAL_LINE,
                            {"claim_number": "CLM-2025-093311", "policy_number": "NAP-4471-2025", "vin": "1HGCM82633A004352", "estimated_damage": 4275.0, "parts_total": 2139.0}),
        "schedule_of_forms": (T.COVERAGE_SCHEDULE_HEADER + "\n" + T.COVERAGE_SCHEDULE_FOOTER,
                              {"policy_number": "HO3-55120-PL", "insured_name": "Rosa and Miguel Alvarez", "effective_date": "2025-05-01", "premium": 2140.0}),
    }
    for doc_type, (text, expected) in cases.items():
        cls = fx.classify_document(text)
        assert cls["document_type"] == doc_type, (doc_type, cls["basis"])
        # the evidence rule and the family fallback keep the keyword answer
        assert orch.reclassify_by_evidence(doc_type, cls["confidence"], [text], keyword_hits=cls["matched_keywords"]) is None
        assert orch.family_fallback(doc_type, cls["confidence"], [text], keyword_hits=cls["matched_keywords"]) is None
        got = {f["name"]: f["value"] for f in fx.extract_fields(doc_type, [text], parser_name="jdf-cli", parse_confidence=None, ocr_confidence=None, page_quality=[None])}
        for name, want in expected.items():
            assert got[name] == want, (doc_type, name, got[name])
    # …and the declarations pages the schemas sit next to keep their types
    assert fx.classify_document(T.AUTO_DECLARATIONS)["document_type"] == "auto_policy"
    assert fx.classify_document(T.PROPERTY_DECLARATIONS)["document_type"] == "property_policy"
    assert fx.classify_document(T.AUTO_FNOL)["document_type"] == "auto_claim"
    assert fx.classify_document(T.PROPERTY_CLAIM)["document_type"] == "property_claim"


def test_schemas_route_lists_the_registry(client):  # noqa: F811
    res = client.get("/api/parsure/schemas")
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] and body["format"] == sr.SCHEMA_FORMAT and body["rejected"] == []
    types = [s["document_type"] for s in body["schemas"]]
    assert types == list(fx.DOCUMENT_TYPES) and set(RUNTIME_TYPES) <= set(types)
    sched = next(s for s in body["schemas"] if s["document_type"] == "schedule_of_forms")
    assert sched["family"] == "property" and sched["source"].endswith("schedule_of_forms.json") and "anchors" not in sched["fields"][0]
    assert {f["name"] for f in sched["fields"]} >= {"dwelling_coverage", "deductible", "forms_list", "signature"}
    with_anchors = client.get("/api/parsure/schemas?anchors=1").get_json()
    assert with_anchors["schemas"][0]["fields"][0]["anchors"]
    # the override route accepts a runtime type
    assert "endorsement" in body["families"] or set(body["families"]) == set(fx.DOCUMENT_FAMILIES)


# --------------------------------------------------------------------------- #
# Part 4.2 dynamic discovery / 4.3 intake_extra
# --------------------------------------------------------------------------- #

def test_heuristic_discovery_lists_label_value_pairs_not_taxonomy_fields():
    texts = ["MARINE CARGO CERTIFICATE\nCertificate No: MC-2025-0042\nAssured: Harbor Freight Lines\nVessel: MV Plymouth Star\n"
             "Voyage: Boston to Rotterdam\nSum Insured: $1,250,000\nNotes: ____\nDEDUCTIBLE:\n"]
    pairs = fd.discover_heuristic(texts)
    names = {p["name"]: p for p in pairs}
    assert set(names) == {"certificate_no", "assured", "vessel", "voyage", "sum_insured"}
    assert names["assured"]["value"] == "Harbor Freight Lines" and names["assured"]["taxonomy_field"] is False
    assert names["sum_insured"]["span"]["span_type"] == "text_range" and names["sum_insured"]["page"] == 1 and names["sum_insured"]["method"] == "heuristic_label_value"
    assert texts[0][names["vessel"]["span"]["start_char"]:names["vessel"]["span"]["end_char"]] == "MV Plymouth Star"
    assert fd.applies_to("uncertain") and fd.applies_to("auto_unknown") and not fd.applies_to("auto_policy")


def test_model_discovery_keeps_only_grounded_pairs(monkeypatch):
    monkeypatch.setenv("PARSURE_LLM_EXTRACTION", "1")
    texts = ["Assured: Harbor Freight Lines\nSum Insured: $1,250,000\n"]
    answer = json.dumps([
        {"label": "Assured", "value": "Harbor Freight Lines", "quote": "Assured: Harbor Freight Lines", "page": 1},
        {"label": "Broker", "value": "Acme Marine", "quote": "Broker: Acme Marine", "page": 1},           # not on the page
        {"label": "Sum insured", "value": "$1,000,000", "quote": "Sum Insured: $1,250,000", "page": 1},    # value not in quote
    ])
    pairs, stats = fd.discover_with_model(texts, completion=lambda _p: answer)
    assert [p["name"] for p in pairs] == ["assured"] and pairs[0]["method"] == "llm_grounded_discovery"
    assert pairs[0]["grounding_quote"] == "Assured: Harbor Freight Lines" and pairs[0]["grounding_model"] == "injected"
    assert stats["status"] == "ran" and stats["candidates"] == 3 and stats["grounded"] == 1 and stats["rejected"] == 2
    monkeypatch.setenv("PARSURE_LLM_EXTRACTION", "0")
    off_pairs, off_stats = fd.discover_with_model(texts, completion=lambda _p: answer)
    assert off_pairs == [] and off_stats["status"] == "skipped" and off_stats["reason"] == "PARSURE_LLM_EXTRACTION is off"


def test_unknown_family_report_carries_discovered_fields_and_honest_counts(monkeypatch):
    monkeypatch.setenv("PARSURE_LLM_EXTRACTION", "0")
    from tests.test_v1_orchestrator import RESULT, VERIFICATION, jdf_cli_bundle

    lines = ["DWELLING FIRE INSPECTION REPORT", "Inspector: Lee Park", "Dwelling Age: 48 years", "Roof Condition: Fair, curling shingles",
             "Personal Property Observed: Yes", "Recommended Action: Replace roof within 12 months", "Hazard Insurance Carrier: Bay Colony"]
    report = orch.build_report("default", bundle=jdf_cli_bundle(lines), verification=VERIFICATION, filename="inspection.pdf", result=RESULT, job_id="j", intake=None)
    assert report["classification"]["document_type"] == "property_unknown", report["classification"]["basis"]
    assert report["fields"] == [] and report["review_summary"]["fields_total"] == 0
    names = {p["name"] for p in report["discovered_fields"]}
    assert {"inspector", "roof_condition", "recommended_action"} <= names
    assert all(p["taxonomy_field"] is False and p["span"]["node_id"] for p in report["discovered_fields"])
    assert report["execution"]["discovery"]["status"] == "completed" and report["execution"]["discovery"]["pairs"] == len(report["discovered_fields"])
    assert report["execution"]["discovery"]["llm_grounded"] == 0
    assert any(n.startswith("field discovery:") for n in report["extraction_notes"])


def test_intake_extra_records_unconsumed_keys_only():
    assert orch.intake_extra(None) is None
    assert orch.intake_extra({"parser": "jdf", "material_type": "pdf", "modality": "digital_pdf", "visual_pages": [], "laya": None}) is None
    assert orch.intake_extra({"parser": "jdf", "claim_context": "subrogation", "adjuster_notes": ["late"]}) == {"claim_context": "subrogation", "adjuster_notes": ["late"]}


def test_caps_label_pairs_from_a_designed_report_are_listed():
    """Customer's site report (2026-09-28): labels in small caps with a middle dot
    that OCR reads as "." or "-", or drops. Four pairs, none listed before."""
    texts = ["PROJECT REPORT\nGENERATED 8 JULY 2026\nREPORT ID . . RPT-260708-E7BE23\nSite Update - — 8 Jul\n"
             "PROGRESS - Vehicle sustained heavy impact damage to the front bonnet, bumper, and\n"
             "CAPTURED . 8 JUL 2026 . 0:44 GMT+10 - GPS -27.55344, 152.88712 . EXIF VERIFIED\n"
             "CONTRACTOR\nCLIENT\nSignature & Date\nRPT-260708-E7BE23\n1/1\n"]
    pairs = {p["name"]: p["value"] for p in fd.discover_heuristic(texts)}
    assert pairs["generated"] == "8 JULY 2026" and pairs["report_id"] == "RPT-260708-E7BE23"
    assert pairs["progress"].startswith("Vehicle sustained heavy impact damage")
    assert pairs["captured"].startswith("8 JUL 2026")
    assert "rpt" not in pairs and "contractor" not in pairs and "client" not in pairs  # ids and headings are not pairs


def test_taxonomy_scan_pools_every_schemas_labels_for_an_uncertain_page():
    """The schema-agnostic candidate pool (customer review 2026-09-28): an
    uncertain page keeps what any schema's labels read, as discovered rows that
    name the schemas which would take them — never as the report's fields."""
    texts = ["DECLARATIONS\nPolicy Number: AP-2025-0001\nNamed Insured: John Q. Sample\nVIN: 1HGCM82633A004352\n"
             "Total Premium: $1,250.00\nAuthorized Signature: /s/ Mary Agent\n"]
    rows = {p["name"]: p for p in fd.taxonomy_candidates(texts, None, parser_name="jdf-cli", parse_confidence=0.9,
                                                          ocr_confidence=None, page_quality=[0.9])}
    assert "policy_number" in rows and rows["policy_number"]["value"] == "AP-2025-0001"
    assert "auto_policy" in rows["policy_number"]["schema_candidates"] and len(rows["policy_number"]["schema_candidates"]) > 1
    assert rows["policy_number"]["method"] == "taxonomy_scan" and rows["policy_number"]["taxonomy_field"] is True
    assert rows["vin"]["typed_value"] == "1HGCM82633A004352" and rows["vin"]["span"]["page"] == 1
    assert "signature" not in rows  # its bottom-of-page fallback would report a row for every schema
    notes: list[str] = []
    execution: dict = {}
    pairs = fd.run_discovery("uncertain", texts, None, notes=notes, llm=False, execution=execution,
                             parser_name="jdf-cli", parse_confidence=0.9, page_quality=[0.9])
    assert execution["discovery"]["taxonomy_scan"] >= 3 and execution["discovery"]["status"] == "completed"
    assert {p["name"] for p in pairs} >= {"policy_number", "vin"} and len({p["name"] for p in pairs}) == len(pairs)
    assert fd.run_discovery("auto_policy", texts, None, notes=[], llm=False, execution={}) == []
