"""Source-level gates of the customer plan V4 (Part 5, 2026-09-28).

Each test reads the code, not a report: the proof suite has no skip/xfail,
both production callers pass ``completion=`` to ``run_after_parse``, no skip
flag exists beyond the Part 2 list, ``provenance_confidence: 1.0`` is never
paired with a non-valid ``value_quality``, export rows never carry a null
``document_id``, and the frozen vocabularies are unchanged."""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from prompt_matrix.services import field_extractor as fx

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "prompt_matrix"
PROOF_TESTS = ("tests/test_proof_suite_v1.py", "tests/test_end_to_end_proof.py", "tests/test_prompt_gates.py", "tests/test_raw_candidates.py")
#: Part 2 guardrail 5 — the only flags that may disable a pipeline step.
ALLOWED_SKIP_FLAGS = {"PARSURE_LLM_EXTRACTION", "PARSURE_REDHAT_LLM", "PARSURE_VISION", "PARSER_SCAN_BACKEND", "JDF_OCR"}
#: Environment names read by the pipeline modules that are NOT skip flags
#: (paths, models, limits, backends). A new name here needs a reason.
KNOWN_NON_SKIP_ENV = {
    "PARSURE_VISION_MAX_PAGES", "PARSURE_VISION_TIMEOUT_S", "ASSURE_OLLAMA_MODEL_VISION", 
    "ASSURE_BEDROCK_MODEL_VISION", "OLLAMA_API_BASE", "ASSURE_LLM_BACKEND", "ASSURE_SCHEMA_DIR", "PARSURE_LLM_INPUT_TOKENS", "PARSURE_LLM_TIMEOUT_S",
    "JDF_CHUNK_STRATEGY", "JDF_ORIENTATION", "JDF_CLI_BIN", "JDF_CLI_VERSION", "JDF_OCR_TIMEOUT", "TESSERACT_LANGS", "PARSURE_GOLDEN_RESULTS", "ASSURE_DATA_DIR",
    "ASSURE_S3_BUCKET", "PEM_TIMEOUT_SECONDS", "AWS_REGION", "AWS_DEFAULT_REGION", "TEXTRACT_MODE", "TEXTRACT_MONTHLY_BUDGET_PAGES",
    "PARSURE_REDHAT_TIMEOUT_S", "ASSURE_TEXTRACT_MODE", "PARSURE_LLM_MAX_INPUT_TOKENS", "ASSURE_OLLAMA_MODEL", "ASSURE_OLLAMA_MODEL_B",
}
PIPELINE_MODULES = ("services/v1_orchestrator.py", "services/raw_candidates.py", "services/field_extractor.py", "services/field_discovery.py",
                    "services/llm_extraction.py", "services/quality_probe.py", "services/table_extraction.py", "services/vision.py",
                    "services/redhat_graph.py", "services/parser_router.py", "services/pdf_ingest.py", "services/jdf_converter.py")


def _calls(source: str, name: str) -> list[ast.Call]:
    tree = ast.parse(source)
    out: list[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if (isinstance(fn, ast.Name) and fn.id == name) or (isinstance(fn, ast.Attribute) and fn.attr == name):
                out.append(node)
    return out


def test_no_skip_or_xfail_in_the_proof_tests():
    for rel in PROOF_TESTS:
        src = (ROOT / rel).read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                dotted = ast.unparse(fn)
                assert not dotted.startswith(("pytest.skip", "pytest.xfail", "pytest.mark.skip", "pytest.mark.xfail", "pytest.importorskip")), (rel, node.lineno, dotted)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for d in node.decorator_list:
                    assert "skip" not in ast.unparse(d) and "xfail" not in ast.unparse(d), (rel, node.name, ast.unparse(d))


@pytest.mark.parametrize("rel", ["prompt_matrix/services/pdf_ingest.py", "prompt_matrix/routers/substrate.py"])
def test_every_production_run_after_parse_call_passes_completion(rel):
    src = (ROOT / rel).read_text(encoding="utf-8")
    calls = _calls(src, "run_after_parse")
    assert calls, f"{rel}: no run_after_parse call found"
    for call in calls:
        kws = {k.arg for k in call.keywords}
        assert "completion" in kws, f"{rel}:{call.lineno} run_after_parse( without completion="
        comp = next(k.value for k in call.keywords if k.arg == "completion")
        assert "app_completion" in ast.unparse(comp), f"{rel}:{call.lineno} completion is not the app's model path: {ast.unparse(comp)}"


def test_the_app_completion_is_reported_as_the_app_model_path_not_injected():
    from prompt_matrix.services import llm_extraction as lx

    c = lx.app_completion("proj")
    assert lx.is_app_completion(c) and lx.is_app_completion(None) and not lx.is_app_completion(lambda p: "{}")
    assert lx.model_path_for(c, "m") == "app:m" and lx.model_path_for(lambda p: "{}") == "injected"
    assert lx.model_id_for(lambda p: "{}") == "injected"


def test_no_new_skip_flags_beyond_the_part_2_list():
    env_re = re.compile(r"os\.environ\.get\(\s*[\"']([A-Z0-9_]+)[\"']|os\.getenv\(\s*[\"']([A-Z0-9_]+)[\"']|_env_flag\(\s*[\"']([A-Z0-9_]+)[\"']")
    seen: set[str] = set()
    for rel in PIPELINE_MODULES:
        src = (PKG / rel).read_text(encoding="utf-8")
        for m in env_re.finditer(src):
            seen.add(next(g for g in m.groups() if g))
    skip_like = {n for n in seen if re.search(r"PARSURE_|JDF_OCR|PARSER_SCAN|SKIP|DISABLE|ENABLE", n)}
    unknown = skip_like - ALLOWED_SKIP_FLAGS - KNOWN_NON_SKIP_ENV
    assert not unknown, f"new pipeline switches need a decision, not a flag: {sorted(unknown)}"


def _golden_fields() -> list[dict]:
    """Every field the label pass produces over the golden set and the raw-candidate fixtures."""
    out: list[dict] = []
    golden = ROOT / "tests" / "golden"
    for path in sorted(golden.glob("*.txt")) + sorted((golden / "prose").glob("*.txt")):
        texts = fx.split_pages(path.read_text(encoding="utf-8"))
        for doc_type in fx.FIELD_TAXONOMY:
            out.extend(fx.extract_fields(doc_type, texts, layout=fx.page_layout({"text": "\f".join(texts)}), parser_name="jdf-cli",
                                         parse_confidence=None, ocr_confidence=None, page_quality=[0.9] * len(texts), visual_pages=[None] * len(texts)))
    for name in ("site_report_photo.ocr.json", "ocr_degraded_policy.ocr.json", "site_report_paraphrase.bundle.json"):
        bundle = json.loads((golden / "proof_suite_v1" / name).read_text(encoding="utf-8"))
        texts = fx.page_texts(bundle)
        for doc_type in fx.FIELD_TAXONOMY:
            out.extend(fx.extract_fields(doc_type, texts, layout=fx.page_layout(bundle), parser_name=bundle.get("parser_name"), parse_confidence=None,
                                         ocr_confidence=bundle.get("ocr_confidence"), page_quality=[0.5] * len(texts), visual_pages=[None] * len(texts)))
    return out


def test_provenance_one_never_co_occurs_with_a_non_valid_value_quality():
    fields = _golden_fields()
    assert len(fields) > 500
    suspects = [f for f in fields if f.get("evidence_state") == "found_suspect"]
    assert suspects, "the corpus carries no suspect — the gate would prove nothing"
    for f in fields:
        if f.get("field_type") == "signature":
            continue  # ink presence is the probe's verdict; a signature field has no value shape
        quality = (f.get("value_quality") or {}).get("quality")
        if f.get("provenance_confidence") == 1.0:
            assert quality == "valid", (f["name"], quality, f.get("raw"))
        if quality and quality != "valid":
            assert f.get("provenance_confidence") == fx.PROVENANCE_BY_SHAPE[quality] and f.get("value") is None, (f["name"], quality)


def test_export_row_never_carries_a_null_document_id():
    from prompt_matrix.routers import parsure_routes as pr

    row = pr.export_row({"report_id": "pr-x", "filename": "f.pdf", "classification": {"document_type": "auto_policy"}, "fields": [],
                         "raw_candidates": [{"candidate_id": "rc-1"}], "execution": {"ran_at": "t"}, "graph_integrity": {"orphans": 0}, "replay": {"history": []}})
    assert row["document_id"] and row["document_id"].startswith("doc-") and row["raw_candidates"] == [{"candidate_id": "rc-1"}]
    assert set(pr.EXPORT_ROW_KEYS) <= set(row)
    src = (PKG / "routers" / "parsure_routes.py").read_text(encoding="utf-8")
    body = src[src.index("def export_row("):]
    body = body[: body.index("\ndef ", 10)]
    assert '"document_id": export_document_id(report)' in body and 'report.get("document_id")' not in body


def test_frozen_vocabularies_are_unchanged():
    from prompt_matrix.services import v1_orchestrator as orch

    assert fx.FIELD_STATES == ("accepted", "partial", "unverified", "disputed", "rejected", "not_found")
    assert fx.ROUTING_ACTIONS == ("none", "manual_review", "adjudicator_queue", "compliance_review", "retry_parsure", "replay_later", "field_not_found")
    assert fx.EVIDENCE_STATES == ("found_verified", "found_unverified", "not_on_document", "unreadable", "schema_mismatch", "found_suspect")
    assert orch.RERUN_TRIGGERS == ("classification_override", "replay") and orch.PIPELINE_TRIGGERS == ("pipeline:llm_grounded", "pipeline:redhat_targeted")
    assert "redhat_draft" not in orch.EXECUTION_STEPS


def test_ocr_engine_is_imported_where_it_is_called():
    src = (PKG / "routers" / "jdf_memory_routes.py").read_text(encoding="utf-8")
    assert "ocr_engine()" in src and re.search(r"import .*\bocr_engine\b", src)


def test_no_builder_writes_a_constant_provenance_of_one():
    """Plan V5 R1: every ``provenance_confidence`` assignment in the services
    derives from ``PROVENANCE_BY_SHAPE`` or is an explicit, commented fallback
    (the visual signature probe's 0.5) — never the literal ``1.0``."""
    import re

    offenders = []
    for path in sorted((ROOT / "prompt_matrix" / "services").glob("*.py")):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"""\[["']provenance_confidence["']\]\s*=\s*1(\.0)?\b""", line):
                offenders.append(f"{path.name}:{i}: {line.strip()}")
    assert not offenders, offenders


def test_table_cells_obey_the_provenance_gate_too():
    """Cross-builder gate (V5 R1): the table builder, fed a header-shaped cell
    and a valid cell, obeys the same rule as the label pass."""
    from prompt_matrix.services import table_extraction as te
    from tests.test_table_extraction import _specs, table_bundle

    rows = [["Coverage A · Dwelling", "HO 00 03", "", "TOTAL LIMIT", "", "$2,500", "$1,412.00"],
            ["Coverage B · Other Structures", "HO 00 03", "", "$42,500", "", "$2,500", "$96.00"]]
    table = te.collect_tables(table_bundle(rows=rows))[0]
    spec = _specs("dwelling_coverage")[0]
    for cell in ({"row": 0, "col": 3, "raw": "TOTAL LIMIT", "value": None, "pick": "row_label", "basis": "b"},
                 {"row": 1, "col": 3, "raw": "$42,500", "value": 42500.0, "pick": "row_label", "basis": "b"}):
        f = te.build_table_field(spec, table, cell, parser_name="jdf-cli", parse_confidence=None, ocr_confidence=None, page_quality=[0.9], visual_pages=[None])
        quality = f["value_quality"]["quality"]
        if f["provenance_confidence"] == 1.0:
            assert quality == "valid"
        if quality != "valid":
            assert f["provenance_confidence"] == fx.PROVENANCE_BY_SHAPE[quality] and f["value"] is None


def test_every_report_carries_the_build_stamp():
    """Plan V5 review protocol V1: an artifact names the build that made it."""
    from prompt_matrix.services.build_info import build_stamp, reset_cache

    reset_cache()
    stamp = build_stamp()
    assert set(stamp) == {"commit", "branch", "source"} and stamp["source"] in ("env", "git", "none")
    assert stamp["commit"] == "unknown" or len(stamp["commit"]) >= 7
