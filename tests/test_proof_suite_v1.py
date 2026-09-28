"""Proof suite V1 (customer plan V4 Part 5, 2026-09-28) — unskippable, offline.

Three fixtures under ``tests/golden/proof_suite_v1`` run through the real
``run_after_parse`` against PostgreSQL with an injected completion (no
network): the customer's site-report photo with the jdf-cli + tesseract
bundle recorded for it, a paraphrased synthetic report of the same class
(anti-memorisation), and a degraded scan of an auto policy. Each test asserts
what the plan's Definition of Done names — raw candidates on every report and
in every artifact, honest classification, verbatim-grounded facts only, no
``provenance_confidence: 1.0`` on debris, a stable content-derived
``document_id``, a verifying snapshot, the readability floor on a page the
pipeline read, remap-before-reparse on a type change — and the last test
writes ``proof_diff.json`` (``PROOF_DIFF_PATH``) from what the others measured,
failing when nothing changed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import pytest

from tests.test_v1_orchestrator import VERIFICATION

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "golden" / "proof_suite_v1"
PROOF_DIFF_PATH = Path(os.environ.get("PROOF_DIFF_PATH") or (ROOT / "proof_diff.json"))
#: What the tests measured, written by the last test.
PROOF: dict[str, dict] = {}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "proof_v1.sqlite"))
    monkeypatch.setenv("ASSURE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WTF_CSRF_ENABLED", "0")
    monkeypatch.setenv("CELERY_BROKER_URL", "")
    monkeypatch.setenv("PARSURE_LLM_EXTRACTION", "1")  # the grounded passes run; the model is injected
    monkeypatch.setenv("PARSURE_REDHAT_LLM", "0")
    monkeypatch.setenv("PARSURE_VISION", "0")
    monkeypatch.delenv("ASSURE_S3_BUCKET", raising=False)
    import prompt_matrix.history as history_mod
    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db
    from prompt_matrix.db.jdf_repository import ensure_project
    from prompt_matrix.web import create_app
    init_db()
    ensure_project("default")
    return create_app(require_auth=False).test_client()


def _bundle(name: str) -> dict:
    data = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    data.pop("_fixture", None)
    return data


def _intake(file_bytes: bytes, filename: str) -> dict:
    from prompt_matrix.services.parser_router import route_intake

    return route_intake(file_bytes, filename)


def _stored(report_id: str) -> dict:
    from prompt_matrix.history import get_db

    return json.loads(get_db().execute("SELECT report_json FROM parsure_reports WHERE report_id = ?", (report_id,)).fetchone()[0])


def _collapse(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


class QuotingCompletion:
    """A fake model that answers only with verbatim quotes handed to it (no
    network, no invention): ``answers`` maps a field name to ``{quote, value,
    page}``; a prompt that names none of them gets ``{}``."""

    def __init__(self, answers: dict[str, dict]):
        self.answers = answers
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        out = {name: ans for name, ans in self.answers.items() if re.search(rf"\b{re.escape(name)}\b", prompt)}
        return json.dumps(out)


def _assert_common_invariants(r: dict, texts: list[str]) -> None:
    """The plan's cross-fixture requirements."""
    pool = r["raw_candidates"]
    assert isinstance(pool, list) and pool, "raw_candidates missing or empty"
    for c in pool:
        assert "confidence" not in c and "evidence_state" not in c
        span = c["source_span"]
        if c["source_kind"] != "image_vision" and span.get("start_char") is not None:
            assert texts[c["page"] - 1][span["start_char"]:span["end_char"]] == c["raw_text"], (c["label_anchor"], c["raw_text"])
    for f in r["fields"]:
        if f.get("field_type") == "signature":
            continue
        quality = (f.get("value_quality") or {}).get("quality")
        if f.get("provenance_confidence") == 1.0:
            assert quality == "valid", (f["name"], quality, f.get("raw"))
        if f.get("value") is not None:
            page = f["source_span"]["page"]
            assert _collapse(f["raw"]) in _collapse(texts[page - 1]), ("value not verbatim on its page", f["name"], f.get("raw"))
            assert f.get("grounding_quote") and _collapse(f["grounding_quote"]) in _collapse(texts[page - 1]), (f["name"], f.get("grounding_quote"))
        else:
            assert f.get("extraction_confidence") in (0.0, None) and f.get("verification_confidence") is None
    exe = r["execution"]
    assert "redhat_draft" not in exe and exe["z3"]["async_deferred"] is False
    assert exe["raw_candidates"]["status"] == "completed" and exe["raw_candidates"]["candidates"] == len(pool)
    assert exe["llm_grounding"]["status"] in ("ran", "not_needed") and exe["llm_grounding"]["model_path"] == "injected"
    assert exe["redhat_graph"]["status"] == "completed"
    assert r["graph_integrity"]["integrity_score"] == 1.0 and r["graph_integrity"]["orphans"] == 0


def _artifacts_agree(client, out: dict, r: dict) -> dict:
    """Stored row, JSON API, per-report export and project export all carry the same raw pool."""
    from prompt_matrix.services import snapshot as snap

    stored = _stored(out["report_id"])
    assert snap.verify(stored)["ok"]
    assert stored["raw_candidates"] == r["raw_candidates"]
    api = client.get(f"/api/projects/default/parsure/{out['report_id']}").get_json()
    assert api["integrity"]["ok"] is True and api["report"]["raw_candidates"] == r["raw_candidates"]
    exported = client.get(f"/api/projects/default/parsure/{out['report_id']}/export?format=json")
    assert exported.status_code == 200, exported.get_json()
    body = exported.get_json()
    assert body["document_id"] == r["document_id"] and body["raw_candidates"] == r["raw_candidates"]
    assert body["execution"] and body["graph_integrity"] and body["replay"] is not None and body["snapshot_hash"] == stored["snapshot"]["content_hash"]
    project = client.get("/api/projects/default/parsure/export?format=json").get_json()
    row = next(d for d in project["documents"] if d["report_id"] == out["report_id"])
    assert row["document_id"] == r["document_id"] and row["raw_candidates"] == r["raw_candidates"] and row["execution"]["ran_at"]
    assert all(d["document_id"] for d in project["documents"])
    # a tampered pool refuses to export: the snapshot covers the raw layer
    from prompt_matrix.history import get_db

    tampered = json.loads(json.dumps(stored))
    tampered["raw_candidates"][0]["raw_text"] = "TAMPERED"
    db = get_db()
    db.execute("UPDATE parsure_reports SET report_json = ? WHERE report_id = ?", (json.dumps(tampered, ensure_ascii=False, default=str), out["report_id"]))
    db.commit()
    refused = client.get(f"/api/projects/default/parsure/{out['report_id']}/export?format=json")
    assert refused.status_code == 409
    db.execute("UPDATE parsure_reports SET report_json = ? WHERE report_id = ?", (json.dumps(stored, ensure_ascii=False, default=str), out["report_id"]))
    db.commit()
    return stored


def _label_pass_only(bundle: dict, document_type: str) -> int:
    from prompt_matrix.services import field_extractor as fx

    texts = fx.page_texts(bundle)
    fields = fx.extract_fields(document_type, texts, layout=fx.page_layout(bundle), parser_name=bundle.get("parser_name"), parse_confidence=None,
                               ocr_confidence=bundle.get("ocr_confidence"), page_quality=[None] * len(texts), visual_pages=[None] * len(texts))
    return sum(1 for f in fields if f.get("value") is not None and f.get("field_type") != "signature")


def test_site_report_photo_classifies_honestly_with_raw_candidates_and_verbatim_facts_only(client):
    from prompt_matrix.services import field_extractor as fx
    from prompt_matrix.services.v1_orchestrator import run_after_parse

    jpeg = (FIXTURES / "site_report_photo.jpg").read_bytes()
    bundle = _bundle("site_report_photo.ocr.json")
    texts = fx.page_texts(bundle)
    model = QuotingCompletion({})  # the model knows nothing this page does not say; nothing to add
    out = run_after_parse("default", bundle=bundle, verification=VERIFICATION, filename="site_report_photo.jpg", file_bytes=jpeg, result={},
                          job_id=None, intake=_intake(jpeg, "site_report_photo.jpg"), completion=model)
    assert out, "run_after_parse returned nothing"
    r = out["report"]
    cls = r["classification"]
    assert cls["document_type"] == "site_report" or (cls["schema_mismatch"] and cls["suggestion"]), cls
    _assert_common_invariants(r, texts)
    fields = {f["name"]: f for f in r["fields"]}
    # real facts, grounded verbatim; the id came through the raw pool (tesseract wrote "+" for the middle dot)
    assert fields["report_id"]["value"] == "RPT-260708-E7BE23" and fields["report_id"]["extraction_method"] == "raw_candidate"
    assert fields["report_id"]["candidate_source"]["source_kind"] in ("layout_text", "discovery")
    assert fields["report_date"]["value"] == "2026-07-08" and fields["capture_date"]["value"] == "2026-07-08"
    assert fields["gps"]["value"] == [-27.55344, 152.88712]
    assert fields["summary"]["value"].startswith("PROGRESS") and fields["next_action"]["value"].startswith("Request an inspection")
    # nothing fabricated: the unsigned blocks are debris under their labels, not names
    for name in ("contractor", "client"):
        assert fields[name]["value"] is None and fields[name]["evidence_state"] == "found_suspect" and fields[name]["provenance_confidence"] < 1.0
    assert fields["site"]["value"] is None and fields["site"]["field_state"] == "not_found"
    labels = {c["label_anchor"]: c["raw_text"] for c in r["raw_candidates"]}
    assert labels["REPORT ID"] == "RPT-260708-E7BE23"
    assert r["projection"]["fields"]["report_id"]["outcome"] in ("mapped", "review_needed") and r["projection"]["fields"]["report_id"]["replaced"] == "found_suspect"
    # readability: the pipeline read six labels verbatim; the page cannot score as unreadable
    page = r["pages"][0]
    assert page["quality_score"] >= 0.5, (page["quality_score"], page["basis"])
    # six labels read with a valid shape, two signature captions read as debris: 6/8
    assert page["grounding_success"] >= 0.7 and page["ocr_confidence"] == 0.8
    assert r["execution"]["page_quality"]["status"] == "completed"
    assert r["quality_report"]["low_quality_pages"] == []
    # identity
    assert r["document_id"] == "doc-" + hashlib.sha256(jpeg).hexdigest()[:16] and r["document_id_source"] == "content_hash"
    assert all(f["field_uid"] == f"field-{r['document_id']}-{f['name']}" for f in r["fields"])
    _artifacts_agree(client, out, r)
    PROOF["site_report_photo"] = {
        "classification": cls["document_type"], "basis": cls["basis"],
        "fields_found_label_pass_only": _label_pass_only(bundle, "site_report"),
        "fields_found_pipeline": sum(1 for f in r["fields"] if f["value"] is not None and f["field_type"] != "signature"),
        "raw_candidates": len(r["raw_candidates"]), "mapped_from_pool": sorted(n for n, e in r["projection"]["fields"].items() if e.get("replaced")),
        "page_quality_probe": page.get("quality_score_probe"), "page_quality": page["quality_score"], "page_basis": page["basis"],
        "suspects": sorted(f["name"] for f in r["fields"] if f.get("evidence_state") == "found_suspect"),
        "model_prompts": len(model.prompts),
    }


def test_type_change_remaps_the_stored_pool_before_rereading_and_the_pool_is_append_only(client):
    from prompt_matrix.services import field_extractor as fx
    from prompt_matrix.services.v1_orchestrator import run_after_parse

    jpeg = (FIXTURES / "site_report_photo.jpg").read_bytes()
    bundle = _bundle("site_report_photo.ocr.json")
    out = run_after_parse("default", bundle=bundle, verification=VERIFICATION, filename="site_report_photo.jpg", file_bytes=jpeg, result={},
                          job_id=None, intake=None, completion=QuotingCompletion({}))
    r = out["report"]
    pool_before = json.dumps(r["raw_candidates"], sort_keys=True)
    # reviewer says auto_policy: wrong for this page, and the report says so — but the pool is untouched
    res = client.post(f"/api/projects/default/parsure/{out['report_id']}/classification", json={"document_type": "auto_policy", "reason": "test"})
    assert res.status_code == 200, res.get_json()
    r2 = res.get_json()["report"]
    assert r2["classification"]["document_type"] == "auto_policy" and r2["classification"]["schema_mismatch"] is True
    assert json.dumps(r2["raw_candidates"][: len(r["raw_candidates"])], sort_keys=True) == pool_before  # append-only: same prefix
    entry = r2["replay"]["history"][-1]
    assert entry["trigger"] == "classification_override" and entry["grounded"] is False and "no model call" in entry["grounded_reason"]
    assert "report_id" in entry["fields_changed"] and entry.get("mapping_changed") is None  # schema mismatch: nothing to project onto
    assert r2["replay"]["attempts"] == 1
    # back to site_report: the id is re-projected from the STORED pool (the projection log says so), no model, one more attempt
    res = client.post(f"/api/projects/default/parsure/{out['report_id']}/classification", json={"document_type": "site_report", "reason": "test"})
    r3 = res.get_json()["report"]
    fields = {f["name"]: f for f in r3["fields"]}
    assert fields["report_id"]["value"] == "RPT-260708-E7BE23" and fields["report_id"]["extraction_method"] == "raw_candidate"
    assert fields["report_id"]["candidate_source"]["candidate_id"] in {c["candidate_id"] for c in r["raw_candidates"]}
    entry = r3["replay"]["history"][-1]
    assert entry["mapping_changed"]["mapped"] + entry["mapping_changed"]["review_needed"] >= 1 and entry["mapping_changed"]["document_type"] == "site_report"
    assert r3["replay"]["attempts"] == 2 and r3["execution"]["projection"]["status"] == "completed"
    assert json.dumps(r3["raw_candidates"][: len(r["raw_candidates"])], sort_keys=True) == pool_before
    # the manual replay declares itself ungrounded too
    rp = client.post(f"/api/projects/default/parsure/{out['report_id']}/replay", json={})
    assert rp.status_code == 200, rp.get_json()
    proof = rp.get_json()["proof"]
    assert proof["grounded"] is False and proof["deterministic"] is True
    stored = _stored(out["report_id"])
    assert stored["replay"]["history"][-1]["trigger"] == "replay" and stored["replay"]["history"][-1]["grounded"] is False
    assert fx.EXTRACTION_METHODS[-1] == "raw_candidate"
    PROOF["type_change_remap"] = {"report_id_after_override_back": fields["report_id"]["value"], "history_triggers": [h["trigger"] for h in stored["replay"]["history"]],
                                  "pool_size": len(stored["raw_candidates"]), "pool_prefix_identical": True}


def test_paraphrased_site_report_gets_the_same_treatment_without_memorised_text(client):
    from prompt_matrix.services import field_extractor as fx
    from prompt_matrix.services.v1_orchestrator import run_after_parse

    pdf = (FIXTURES / "site_report_paraphrase.pdf").read_bytes()
    bundle = _bundle("site_report_paraphrase.bundle.json")
    texts = fx.page_texts(bundle)
    assert "RPT-260708" not in texts[0] and "smash" not in texts[0]  # genuinely different text and values
    model = QuotingCompletion({})
    out = run_after_parse("default", bundle=bundle, verification=VERIFICATION, filename="site_report_paraphrase.pdf", file_bytes=pdf, result={},
                          job_id=None, intake=_intake(pdf, "site_report_paraphrase.pdf"), completion=model)
    r = out["report"]
    assert r["classification"]["document_type"] == "site_report", r["classification"]
    _assert_common_invariants(r, texts)
    fields = {f["name"]: f for f in r["fields"]}
    # the text layer keeps the middle dot, so the label pass reads the id itself; the pool agrees with it
    assert fields["report_id"]["value"] == "SR-2026-0314-A1" and fields["report_id"]["extraction_method"] in ("label_anchor", "raw_candidate")
    assert r["projection"]["status"] == "completed" and r["projection"]["candidates"] == len(r["raw_candidates"])
    assert fields["report_date"]["value"] == "2026-03-14" and fields["capture_date"]["value"] == "2026-03-14"
    assert fields["gps"]["value"] == [51.5074, -0.1278]
    assert fields["contractor"]["value"] == "Northgate Roofing Ltd" and fields["client"]["value"] == "Harbour Estates Management"
    assert fields["site"]["value"] == "12 Harbour Quay, Plant Room Roof"
    assert fields["summary"]["value"].startswith("Roofing membrane") or fields["summary"]["value"].startswith("STATUS")
    assert fields["next_action"]["value"].startswith("Book a roofing contractor")
    assert not [f for f in r["redhat"]["findings"] if f["rule"] == "suspect_value"]
    _artifacts_agree(client, out, r)
    PROOF["site_report_paraphrase"] = {
        "classification": r["classification"]["document_type"],
        "fields_found_label_pass_only": _label_pass_only(bundle, "site_report"),
        "fields_found_pipeline": sum(1 for f in r["fields"] if f["value"] is not None and f["field_type"] != "signature"),
        "raw_candidates": len(r["raw_candidates"]), "mapped_from_pool": sorted(n for n, e in r["projection"]["fields"].items() if e.get("replaced")),
        "page_quality": r["pages"][0]["quality_score"],
    }


def test_degraded_scan_rejects_debris_keeps_labels_in_the_pool_and_the_grounded_pass_recovers_only_what_the_text_supports(client):
    from prompt_matrix.services import field_extractor as fx
    from prompt_matrix.services.v1_orchestrator import run_after_parse

    pdf = (FIXTURES / "ocr_degraded_policy.pdf").read_bytes()
    bundle = _bundle("ocr_degraded_policy.ocr.json")
    texts = fx.page_texts(bundle)
    text = texts[0]
    label_only = fx.extract_fields("auto_policy", texts, layout=fx.page_layout(bundle), parser_name=bundle.get("parser_name"), parse_confidence=None,
                                   ocr_confidence=bundle.get("ocr_confidence"), page_quality=[None], visual_pages=[None])
    empty = {f["name"] for f in label_only if f["value"] is None and f["field_type"] != "signature"}
    # the fake model quotes lines of the OCR text as they are — the one thing it may do — for fields the label pass left empty
    answers: dict[str, dict] = {}
    for name, needle in (("policy_number", r"DG-?4471-?2026"), ("premium", r"\$?\s?1,?480(?:\.00)?"), ("agent_name", r"D[a-z]+ Whitfield"),
                         ("insured_name", r"Priya N\. Raman"), ("vin", r"2T1BURHE0KC123456"), ("liability_limit", r"\$?250,?000")):
        m = re.search(needle, text)
        if m and name in empty:
            line = next(l for l in text.splitlines() if m.group(0) in l)
            answers[name] = {"quote": line.strip(), "value": m.group(0), "page": 1}
    assert answers, f"the recorded OCR supports no grounded recovery: empty={sorted(empty)}"
    invented_quote = "Vehicle: 1999 Ford Ranger"
    assert _collapse(invented_quote) not in _collapse(text)
    invented = {"vehicle_year_make_model": {"quote": invented_quote, "value": "1999 Ford Ranger", "page": 1}}
    model = QuotingCompletion({**invented, **answers})
    out = run_after_parse("default", bundle=bundle, verification=VERIFICATION, filename="ocr_degraded_policy.pdf", file_bytes=pdf, result={},
                          job_id=None, intake=_intake(pdf, "ocr_degraded_policy.pdf"), completion=model)
    r = out["report"]
    _assert_common_invariants(r, texts)
    fields = {f["name"]: f for f in r["fields"]}
    suspects = [f for f in r["fields"] if f.get("evidence_state") == "found_suspect"]
    for f in suspects:
        assert f["value"] is None and f["provenance_confidence"] < 1.0 and f["grounding_quote"]
    # labels survive in the pool even where the value is debris
    assert r["raw_candidates"], "no raw candidate on the degraded scan"
    # the grounded pass recovered only what the text supports: an answer whose quote is not on the page changed nothing
    llm = r["execution"]["llm_grounding"]
    assert llm["status"] == "ran" and llm["model_path"] == "injected"
    recovered = [n for n, f in fields.items() if f.get("extraction_method") == "llm_grounded"]
    for n in recovered:
        assert _collapse(fields[n]["grounding_quote"]) in _collapse(text)
    assert fields["vehicle_year_make_model"]["value"] != "1999 Ford Ranger" and "vehicle_year_make_model" not in recovered
    assert llm["candidates_rejected"] >= 1  # the invented quote was refused
    assert set(recovered) >= set(answers), (recovered, sorted(answers))
    # the page stays a poor scan: the floor cannot lift a page whose reads are mostly debris
    page = r["pages"][0]
    PROOF["ocr_degraded_policy"] = {
        "classification": r["classification"]["document_type"], "ocr_confidence": bundle.get("ocr_confidence"),
        "fields_found_label_pass_only": _label_pass_only(bundle, r["classification"]["document_type"]) if r["classification"]["document_type"] in fx.FIELD_TAXONOMY else 0,
        "fields_found_pipeline": sum(1 for f in r["fields"] if f["value"] is not None and f["field_type"] != "signature"),
        "recovered_by_grounded_pass": sorted(recovered), "rejected_invented_answer": "vehicle_year_make_model" not in recovered,
        "suspects": sorted(f["name"] for f in suspects), "raw_candidates": len(r["raw_candidates"]),
        "page_quality": page["quality_score"], "page_quality_probe": page.get("quality_score_probe"), "grounding_success": page.get("grounding_success"),
        "candidates_rejected": llm["candidates_rejected"],
    }
    assert page["quality_score"] is not None and page["quality_score"] < 0.5, (page["quality_score"], page["basis"])
    _artifacts_agree(client, out, r)


def test_zz_write_proof_diff_showing_a_real_behaviour_change():
    """Last in file order: the measurements the tests above collected go to
    ``PROOF_DIFF_PATH`` (CI uploads it). Fails when a fixture is missing or
    when nothing changed between the label pass alone and the pipeline."""
    assert set(PROOF) >= {"site_report_photo", "type_change_remap", "site_report_paraphrase", "ocr_degraded_policy"}, sorted(PROOF)
    photo = PROOF["site_report_photo"]
    changes = {
        "site_report_photo": {
            "fields_found": [photo["fields_found_label_pass_only"], photo["fields_found_pipeline"]],
            "page_quality": [photo["page_quality_probe"], photo["page_quality"]],
            "mapped_from_pool": photo["mapped_from_pool"],
        },
        "site_report_paraphrase": {"fields_found": [PROOF["site_report_paraphrase"]["fields_found_label_pass_only"], PROOF["site_report_paraphrase"]["fields_found_pipeline"]],
                                   "mapped_from_pool": PROOF["site_report_paraphrase"]["mapped_from_pool"]},
        "ocr_degraded_policy": {"fields_found": [PROOF["ocr_degraded_policy"]["fields_found_label_pass_only"], PROOF["ocr_degraded_policy"]["fields_found_pipeline"]],
                                "recovered_by_grounded_pass": PROOF["ocr_degraded_policy"]["recovered_by_grounded_pass"]},
    }
    behaviour_changed = (
        photo["fields_found_pipeline"] > photo["fields_found_label_pass_only"]
        and (photo["page_quality_probe"] is None or photo["page_quality"] > photo["page_quality_probe"])
        and PROOF["ocr_degraded_policy"]["fields_found_pipeline"] > PROOF["ocr_degraded_policy"]["fields_found_label_pass_only"]
    )
    diff = {"suite": "proof_suite_v1", "behaviour_changed": behaviour_changed, "summary": changes, "measurements": PROOF}
    PROOF_DIFF_PATH.write_text(json.dumps(diff, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    assert behaviour_changed, json.dumps(changes, indent=1)
