"""The Parsure benchmark harness (``scripts/benchmark.py``) and its frozen set (``bench/``).

Pure checks only — no PostgreSQL, no jdf-cli: the manifest validates, every
generator produces a readable file of the kind it claims, and the metric
arithmetic / gate evaluation / ``n/a`` handling are right on a fixed fake
result set. The real pipeline run is ``scripts/benchmark.py`` itself.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import fitz
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_harness():
    spec = importlib.util.spec_from_file_location("benchmark_harness", ROOT / "scripts" / "benchmark.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def harness():
    return _load_harness()


@pytest.fixture(scope="module")
def manifest(harness):
    return harness.load_manifest()


@pytest.fixture(scope="module")
def generators():
    if str(ROOT / "bench") not in sys.path:
        sys.path.insert(0, str(ROOT / "bench"))
    from cases import generators as g  # type: ignore[import-not-found]

    return g


# --------------------------------------------------------------------------- #
# Manifest
# --------------------------------------------------------------------------- #

class TestManifest:
    def test_validates(self, harness, manifest):
        assert harness.validate_manifest(manifest) == []

    def test_frozen_version_and_date(self, manifest):
        assert manifest["version"] == "bench-v1"
        assert manifest["frozen_at"] == "2026-09-26"

    def test_unique_ids_and_every_class_present(self, harness, manifest):
        ids = [c["id"] for c in manifest["cases"]]
        assert len(ids) == len(set(ids))
        assert {c["class"] for c in manifest["cases"]} == set(harness.CLASSES)

    def test_at_least_sixteen_synthetic_cases(self, manifest):
        synthetic = [c for c in manifest["cases"] if c["source"]["kind"] == "synthetic"]
        assert len(synthetic) >= 16

    def test_every_synthetic_generator_exists(self, manifest, generators):
        for c in manifest["cases"]:
            if c["source"]["kind"] != "synthetic":
                continue
            for inp in c["inputs"]:
                assert inp["generator"] in generators.GENERATORS, f"{c['id']}: unknown generator {inp['generator']}"

    def test_seeded_conflicts_name_shared_fields(self, manifest):
        pairs = [c for c in manifest["cases"] if c["seeded_conflicts"]]
        assert len(pairs) >= 3
        for c in pairs:
            assert len(c["inputs"]) >= 2
            assert set(c["seeded_conflicts"]) <= {"policy_number", "insured_name", "vin"}

    def test_schema_gap_cases_are_flagged(self, manifest):
        try:
            from prompt_matrix.services.field_extractor import DOCUMENT_TYPES
        except ImportError:
            pytest.skip("prompt_matrix not importable")
        for c in manifest["cases"]:
            for inp in c["inputs"]:
                exp = inp["expected"]
                t = exp.get("document_type")
                if t is None or t == "mixed_bundle" or t in DOCUMENT_TYPES:
                    assert exp.get("schema_exists") is not False, f"{c['id']}: {t} exists, must not be flagged"
                else:
                    assert exp.get("schema_exists") is False, f"{c['id']}: {t} is not in DOCUMENT_TYPES and must carry schema_exists=false"

    def test_validator_reports_problems(self, harness, manifest):
        broken = json.loads(json.dumps(manifest))
        broken["cases"][1]["id"] = broken["cases"][0]["id"]
        broken["cases"][2]["class"] = "invoices"
        broken["cases"][3]["inputs"][0]["expected"].pop("document_type")
        broken["version"] = "v1"
        problems = harness.validate_manifest(broken)
        assert any("duplicate" in p for p in problems)
        assert any("invoices" in p for p in problems)
        assert any("document_type missing" in p for p in problems)
        assert any("bench-vN" in p for p in problems)

    def test_select_cases_filters(self, harness, manifest):
        photos = harness.select_cases(manifest, classes=["photos"])
        assert photos and all(c["class"] == "photos" for c in photos)
        one = harness.select_cases(manifest, ids=["pol-auto-declarations-typed"])
        assert [c["id"] for c in one] == ["pol-auto-declarations-typed"]
        assert harness.select_cases(manifest, ids=["nope"]) == []


# --------------------------------------------------------------------------- #
# Generators
# --------------------------------------------------------------------------- #

class TestGenerators:
    def test_every_generator_produces_a_readable_file(self, generators):
        for name, fn in generators.GENERATORS.items():
            filename, data, meta = fn()
            ext = filename.rsplit(".", 1)[-1].lower()
            assert data[:4] in (b"%PDF", b"\x89PNG", b"\xff\xd8\xff\xe0", b"\xff\xd8\xff\xe1", b"\xff\xd8\xff\xdb"), f"{name}: unrecognised header"
            with fitz.open(stream=data, filetype="pdf" if ext == "pdf" else ext) as doc:
                assert len(doc) >= 1, name
                assert doc[0].rect.width > 100 and doc[0].rect.height > 100, name
            assert isinstance(meta, dict) and meta.get("modality"), name

    def test_typed_pdfs_carry_a_text_layer_and_scans_do_not(self, generators):
        typed = ("auto_declarations_typed", "cms1500_typed", "bundle_policy_and_fnol_typed", "coverage_schedule_table_typed")
        scans = ("auto_declarations_rotated_scan_90", "auto_declarations_low_contrast_scan", "adjuster_note_handwritten_scan", "auto_declarations_signed_stroke_scan")
        for name in typed:
            _, data, _ = generators.build(name)
            with fitz.open(stream=data, filetype="pdf") as doc:
                assert "".join(p.get_text() for p in doc).strip(), f"{name} should have a text layer"
        for name in scans:
            _, data, _ = generators.build(name)
            with fitz.open(stream=data, filetype="pdf") as doc:
                assert not "".join(p.get_text() for p in doc).strip(), f"{name} must be image-only"

    def test_bundle_has_three_pages_and_two_documents(self, generators):
        _, data, meta = generators.build("bundle_policy_and_fnol_typed")
        with fitz.open(stream=data, filetype="pdf") as doc:
            assert len(doc) == 3
            assert "FIRST NOTICE OF CLAIM" in doc[2].get_text()
            assert "DECLARATIONS" in doc[0].get_text()
        assert meta["documents"] == 2

    def test_rotated_scan_is_landscape(self, generators):
        _, data, meta = generators.build("auto_declarations_rotated_scan_90")
        with fitz.open(stream=data, filetype="pdf") as doc:
            assert doc[0].rect.width > doc[0].rect.height
        assert meta["rotation"] == 90

    def test_signature_stroke_changes_the_pixels(self, generators):
        _, signed, _ = generators.build("auto_declarations_signed_stroke_scan")
        _, blank, _ = generators.build("auto_declarations_blank_signature_scan")
        assert signed != blank
        with fitz.open(stream=signed, filetype="pdf") as a, fitz.open(stream=blank, filetype="pdf") as b:
            pa, pb = a[0].get_pixmap(dpi=50), b[0].get_pixmap(dpi=50)
            assert pa.samples != pb.samples

    def test_handwritten_meta_says_which_font_or_not_extractable(self, generators):
        _, _, meta = generators.build("adjuster_note_handwritten_scan")
        assert ("script_font" in meta) and (meta["script_font"] or meta.get("not_extractable_by_design") is True)

    def test_partial_page_is_half_height(self, generators):
        _, data, _ = generators.build("auto_declarations_partial_page_scan")
        with fitz.open(stream=data, filetype="pdf") as doc:
            assert abs(doc[0].rect.height - generators.PAGE_H / 2) < 1

    def test_endorsement_vin_passes_the_check_digit(self):
        try:
            from prompt_matrix.services.field_extractor import validate_vin
        except ImportError:
            pytest.skip("prompt_matrix not importable")
        from bench.cases import texts  # type: ignore[import-not-found]

        assert validate_vin("4S4BSAFC8K3312221")["valid"]
        assert "4S4BSAFC8K3312221" in texts.AUTO_ENDORSEMENT


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #

class TestScoring:
    def test_same_value_rules(self, harness):
        s = harness.same_value
        assert s(1486.0, 1486)
        assert s(1486.004, 1486.0) and not s(1486.01, 1486.0)
        assert s(" Daniel  R. Whitfield ", "daniel r. whitfield")
        assert s(["M54.50", "G89.29"], ["g89.29", "m54.50"])
        assert not s(None, "x") and not s("x", None) and s(None, None)
        assert not s(True, 1)

    def _report(self, doc_type, fields, *, documents=None, sig=None, conflicts=None, redhat=None):
        rep = {"classification": {"document_type": doc_type, "validation": {"family": "auto"}, "family": {"family": "auto"}},
               "fields": fields, "documents": documents or [{"index": 0, "pages": [1], "document_type": doc_type}],
               "quality_report": {"signature": sig}, "conflicts": conflicts or [], "_layout": [[{"node_id": "p-1", "element_id": "p1:a"}]]}
        if redhat is not None:
            rep["redhat"] = redhat
        return rep

    def test_evaluate_input_routing_recall_anchoring_signature(self, harness):
        fields = [
            {"name": "policy_number", "value": "NAP-4471-2025", "field_source_node_id": "p-1", "element_id": "p1:a"},
            {"name": "premium", "value": 1486.0, "field_source_node_id": None, "tree_node_id": None},
            {"name": "vin", "value": None},
            {"name": "signature", "value": None},
        ]
        rep = self._report("auto_policy", fields, sig={"present": False, "quality": "missing"})
        out = harness.evaluate_input(
            {"document_type": "auto_policy", "family": "auto", "fields": {"policy_number": "NAP-4471-2025", "premium": 1486.0, "vin": "1HGCM82633A004352"},
             "signature_present": True}, rep)
        assert out["routing_ok"] is True and out["family_ok"] is True and out["schema_gap"] is False
        assert out["fields_expected"] == 3 and out["fields_hit"] == 2
        assert [m["field"] for m in out["misses"]] == ["vin"]
        assert out["found_count"] == 2 and out["anchored_count"] == 1
        assert out["signature_ok"] is False

    def test_schema_gap_is_a_miss_but_flagged(self, harness):
        rep = self._report("auto_policy", [])
        out = harness.evaluate_input({"document_type": "endorsement", "family": "auto", "fields": {}, "schema_exists": False}, rep)
        assert out["routing_ok"] is False and out["schema_gap"] is True and out["family_ok"] is True

    def test_bundle_requires_segments_and_scores_per_segment(self, harness):
        fields = [
            {"name": "policy_number", "value": "NAP-4471-2025", "segment": 0, "field_source_node_id": "p-1"},
            {"name": "claim_number", "value": "CLM-1", "segment": 1, "field_source_node_id": "p-2"},
        ]
        docs = [{"index": 0, "pages": [1, 2], "document_type": "auto_policy"}, {"index": 1, "pages": [3], "document_type": "auto_claim"}]
        rep = self._report("mixed_bundle", fields, documents=docs)
        expected = {"document_type": "mixed_bundle", "family": "auto", "fields": None, "documents": [
            {"pages": [1, 2], "document_type": "auto_policy", "fields": {"policy_number": "NAP-4471-2025"}},
            {"pages": [3], "document_type": "auto_claim", "fields": {"claim_number": "CLM-1", "adjuster_name": "T. Greeley"}},
        ]}
        out = harness.evaluate_input(expected, rep)
        assert out["routing_ok"] is True and out["segments_ok"] is True
        assert out["fields_expected"] == 3 and out["fields_hit"] == 2
        assert out["misses"][0]["segment"] == 1
        # wrong page split → routing miss even though the type is mixed_bundle
        rep["documents"][0]["pages"] = [1]
        assert harness.evaluate_input(expected, rep)["routing_ok"] is False

    def test_conflicts_found_from_conflicts_and_redhat(self, harness):
        rep = self._report("auto_claim", [], conflicts=[{"field": "vin", "kind": "cross_document", "values": []}],
                           redhat={"findings": [{"rule": "cross_document_conflict", "class": "evidentiary", "anchor": {"field": "policy_number"}}]})
        out = harness.conflicts_found(rep, ["vin", "policy_number", "insured_name"])
        assert out["found"] == {"vin": "conflicts[]", "policy_number": "redhat.findings"}
        assert out["missing"] == ["insured_name"] and out["redhat_block_present"] is True

    def test_identity_multisets(self, harness):
        rep_a = self._report("auto_policy", [{"name": "x", "value": 1, "field_source_node_id": "p-1", "element_id": "p1:a"}])
        rep_b = self._report("auto_policy", [{"name": "x", "value": 1, "field_source_node_id": "p-9", "element_id": "p1:a"}])
        key, ids_a = harness.identity_multiset(rep_a)
        _, ids_b = harness.identity_multiset(rep_b)
        assert key == "element_id" and ids_a == ids_b
        assert harness.node_id_multiset(rep_a) != harness.node_id_multiset(rep_b)
        rep_c = {"fields": [{"name": "x", "field_source_node_id": "p-1"}], "_layout": [[{"node_id": "p-1"}]]}
        assert harness.identity_multiset(rep_c)[0] == "field_source_node_id"

    def test_percentile_nearest_rank(self, harness):
        assert harness.percentile([], 0.95) is None
        assert harness.percentile([5.0], 0.95) == 5.0
        assert harness.percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.5) == 5
        assert harness.percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.95) == 10

    def test_latency_prefers_ingest_duration_over_report_stage_clock(self, harness):
        ms, src = harness.latency_ms({"timings_ms": {"total": 11.6}}, {"duration_ms": 2447}, 2500.0)
        assert (ms, src) == (2447.0, "ingest.duration_ms")
        ms, src = harness.latency_ms({"timings_ms": {"total": 11.6}}, {}, 2500.0)
        assert (ms, src) == (2500.0, "wall_clock")


# --------------------------------------------------------------------------- #
# Aggregation and gates on a fixed fake result set
# --------------------------------------------------------------------------- #

def _fake_results():
    def inp(routing_ok, hit, expected, found, anchored, latency, *, gap=False, replay=True, sig=None, error=None, family_ok=True):
        rec = {"filename": "f", "status": "error" if error else "run", "routing_ok": routing_ok, "family_ok": family_ok, "schema_gap": gap,
               "fields_expected": expected, "fields_hit": hit, "found_count": found, "anchored_count": anchored, "latency_ms": latency,
               "replay_identical": replay, "replay_values_identical": True, "replay_node_ids_identical": False, "misses": [], "signature_ok": sig}
        if error:
            rec["error"] = error
        return rec

    return [
        {"id": "a", "class": "policies", "latency_class": "policy_form", "status": "run", "conflicts": None,
         "inputs": [inp(True, 11, 11, 11, 11, 400.0, sig=True), inp(False, 5, 8, 8, 8, 700.0, gap=True)]},
        {"id": "b", "class": "claims", "latency_class": "claim_packet", "status": "run",
         "conflicts": {"seeded": ["vin", "insured_name"], "found": {"vin": "conflicts[]"}, "missing": ["insured_name"], "redhat_block_present": False},
         "inputs": [inp(True, 8, 8, 9, 9, 500.0), inp(True, 8, 8, 9, 6, 600.0, replay=False)]},
        {"id": "c", "class": "photos", "latency_class": "photo_signature", "status": "run", "conflicts": None,
         "inputs": [inp(False, 0, 11, 0, 0, 12000.0, error="PdfIngestError: could not read")]},
        {"id": "d", "class": "handwritten", "latency_class": "photo_signature", "status": "skipped", "skip_reason": "placeholder", "inputs": []},
    ]


class TestAggregateAndGates:
    def test_per_class_metrics(self, harness):
        m = harness.aggregate(_fake_results())
        pol = m["per_class"]["policies"]
        assert pol["inputs"] == 2 and pol["routing_accuracy"] == 0.5 and pol["schema_gaps"] == 1 and pol["schema_gap_misses"] == 1
        assert pol["routing_accuracy_existing_schemas"] == 1.0
        assert pol["extraction_recall"] == round(16 / 19, 4)
        assert pol["anchoring_rate"] == 1.0 and pol["signature_accuracy"] == 1.0
        assert pol["redhat_recall"] is None  # no seeded conflicts in this class → n/a
        assert pol["latency_p50_ms"] == 400.0 and pol["latency_p95_ms"] == 700.0
        clm = m["per_class"]["claims"]
        assert clm["redhat_recall"] == 0.5 and clm["replay_determinism"] == 0.5 and clm["anchoring_rate"] == round(15 / 18, 4)
        pho = m["per_class"]["photos"]
        assert pho["errors"] == 1 and pho["routing_accuracy"] == 0.0 and pho["extraction_recall"] == 0.0
        assert pho["anchoring_rate"] is None and pho["latency_p95_ms"] is None  # an errored ingest has no fields and no latency
        assert m["cases_run"] == 3 and m["cases_skipped"] == 1
        assert "handwritten" not in m["per_class"]

    def test_overall_and_latency_classes(self, harness):
        m = harness.aggregate(_fake_results())
        o = m["overall"]
        assert o["routing_n"] == 5 and o["routing_ok"] == 3 and o["routing_accuracy"] == 0.6
        assert o["redhat_recall"] == 0.5
        assert m["per_latency_class"]["policy_form"]["p95_ms"] == 700.0
        assert m["per_latency_class"]["photo_signature"]["n"] == 0 and m["per_latency_class"]["photo_signature"]["p95_ms"] is None
        assert m["per_latency_class"]["mixed_bundle"] == {"n": 0, "p50_ms": None, "p95_ms": None, "ceiling_ms": 15000}

    def test_gates_pass_fail_and_na(self, harness):
        m = harness.aggregate(_fake_results())
        gates = {(g["gate"], g["scope"]): g for g in harness.evaluate_gates(m)}
        assert gates[("routing_accuracy", "overall")]["status"] == "FAIL"
        assert gates[("anchoring_rate", "overall")]["status"] == "PASS"  # 35/38 ≥ 0.9
        assert gates[("redhat_recall", "overall")]["status"] == "FAIL"
        assert gates[("replay_determinism", "overall")]["status"] == "FAIL"
        assert gates[("latency_p95_ms", "policy_form")]["status"] == "PASS"
        assert gates[("latency_p95_ms", "photo_signature")]["status"] == "n/a"
        assert gates[("latency_p95_ms", "mixed_bundle")]["status"] == "n/a"

    def test_empty_run_is_all_na(self, harness):
        m = harness.aggregate([])
        assert m["overall"]["routing_accuracy"] is None and m["overall"]["latency_p95_ms"] is None
        assert all(g["status"] == "n/a" for g in harness.evaluate_gates(m))
        assert harness.fmt_pct(None) == "n/a" and harness.fmt_ms(None) == "n/a"

    def test_markdown_prints_na_not_a_number(self, harness):
        m = harness.aggregate(_fake_results())
        result = {"benchmark_version": "bench-v1", "git_sha": "abc", "run_at": "now", "options": {"llm": False}, "env": {"pg_schema": "t", "scan_backend": "jdf-ocr"},
                  "metrics": m, "gates": harness.evaluate_gates(m), "notes": [], "cases": _fake_results()}
        md = harness.render_markdown(result)
        assert "| policies | 2 | 50 % (1 schema-gap) |" in md
        assert "n/a (0/0)" in md  # red-hat with no seeded conflict
        assert "| latency_p95_ms | photo_signature | < 10.00 s | n/a | **n/a** |" in md
        assert "**FAIL**" in md and "**PASS**" in md
        assert "Failing cases:" in md and "`c` — f: ERROR" in md

    def test_baseline_diff_names_regressions(self, harness):
        m = harness.aggregate(_fake_results())
        cur = {"metrics": m, "gates": harness.evaluate_gates(m), "cases": _fake_results()}
        base_cases = _fake_results()
        base_cases[1]["inputs"][0]["routing_ok"] = False
        base_cases[0]["inputs"][0]["misses"] = [{"field": "vin"}]
        bm = harness.aggregate(base_cases)
        base = {"metrics": bm, "gates": harness.evaluate_gates(bm), "cases": base_cases}
        diff = harness.diff_against_baseline(cur, base, "old.json")
        joined = "\n".join(diff["lines"])
        assert "b f: routing fixed" in joined
        assert "a f: now found ['vin']" in joined
        assert "overall routing_accuracy: 40 % → 60 %" in joined
