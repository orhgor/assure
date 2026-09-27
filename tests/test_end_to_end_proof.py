"""End-to-end proof (customer plan ``todos/fable_execution_plan.md`` Parts 2.8,
2.10 and 16, 2026-09-27): the pipeline does not merely *have* a critique, a
verifier, a grounded model pass and a rerun ledger — each one runs, leaves a
trace in ``report["execution"]``, and changes the output in a way the JSON,
the stored row, the export and the replay ledger all show together.

The model is an injected completion (no network): the first grounded pass
answers nothing, the Red-Hat-targeted second pass answers with a verbatim
quote — so the change is attributable to the critique, not to luck."""
from __future__ import annotations

import json

import pytest

from tests.test_v1_orchestrator import RESULT, VERIFICATION, jdf_cli_bundle, _tree_for

#: Debris under the policy label; the real number sits in a footer no label anchor reads.
DEBRIS_LINES = [
    "AUTO POLICY DECLARATIONS",
    "Policy Number: ~~-,;;",
    "Named Insured: MAILING ADDRESS",
    "Policy Period: 01/15/2025 to 01/15/2026",
    "Vehicle: 2003 Honda Accord",
    "VIN: 1HGCM82633A004352",
    "Total Premium: $1,250.00",
    "Liability Limit: $100,000",
    "Collision Deductible: $500",
    "Comprehensive Deductible: $250",
    "Agent: Mary Agent",
    "Authorized Signature: /s/ Mary Agent",
    "Ref AP-2025-0001 (policy) issued to John Q. Sample, the named insured.",
] + ["Coverage notes and conditions apply as stated in the policy forms."] * 4


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "proof.sqlite"))
    monkeypatch.setenv("ASSURE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WTF_CSRF_ENABLED", "0")
    monkeypatch.setenv("CELERY_BROKER_URL", "")
    monkeypatch.setenv("PARSURE_LLM_EXTRACTION", "1")  # the pass runs; the model is injected below
    monkeypatch.setenv("PARSURE_REDHAT_LLM", "0")
    monkeypatch.delenv("ASSURE_S3_BUCKET", raising=False)
    import prompt_matrix.history as history_mod
    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db
    from prompt_matrix.db.jdf_repository import ensure_project
    from prompt_matrix.web import create_app
    init_db()
    ensure_project("default")
    return create_app(require_auth=False).test_client()


class TwoPassCompletion:
    """Nothing on the first (blanket) prompt; the grounded answer on the hinted one."""

    def __init__(self):
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if "(note:" not in prompt:
            return "{}"
        return json.dumps({
            "policy_number": {"quote": "Ref AP-2025-0001 (policy) issued to John Q. Sample", "value": "AP-2025-0001", "page": 1},
            "insured_name": {"quote": "issued to John Q. Sample, the named insured", "value": "John Q. Sample", "page": 1},
        })


def _stored(report_id):
    from prompt_matrix.history import get_db
    return json.loads(get_db().execute("SELECT report_json FROM parsure_reports WHERE report_id = ?", (report_id,)).fetchone()[0])


def test_redhat_finding_triggers_a_hinted_pass_that_changes_the_field_and_every_artifact_agrees(client):
    from prompt_matrix.services import snapshot as snap
    from prompt_matrix.services.v1_orchestrator import run_after_parse

    model = TwoPassCompletion()
    out = run_after_parse("default", bundle=jdf_cli_bundle(DEBRIS_LINES), verification=VERIFICATION, filename="debris.pdf",
                          file_bytes=b"debris-bytes", result={}, job_id="job-p", intake=None, completion=model, tree=_tree_for(DEBRIS_LINES))
    assert out, "run_after_parse returned nothing"
    r = out["report"]
    exe = r["execution"]
    # 1. every step left a trace — nothing "pending", nothing "not_started"
    assert exe["z3"]["status"] == "PASS" and exe["redhat_draft"]["status"] == "complete"
    assert exe["redhat_graph"]["status"] == "completed" and exe["redhat_graph"]["policy"] == "rh-graph-v1"
    assert exe["llm_grounding"]["status"] == "ran" and exe["llm_grounding"]["model"] == "injected"
    assert exe["laya"]["status"] == "not_run" and "router" in exe["laya"]["reason"]  # honest: no router on a direct call
    assert exe["ran_at"]
    # 2. the critique named the suspects (the first run's counts and the targeted list are kept) …
    assert set(r["redhat"]["targeted_pass"]["fields"]) >= {"policy_number", "insured_name"}
    assert r["redhat"]["previous_counts"]["medium"] >= 2
    # 3. … the hinted pass ran once, with the finding as the hint, and changed both fields
    assert len(model.prompts) == 2 and "(note:" in model.prompts[1] and "(note:" not in model.prompts[0]
    targeted = exe["redhat_targeted"]
    assert targeted["status"] == "ran" and set(targeted["changed"]) == {"policy_number", "insured_name"}
    fields = {f["name"]: f for f in r["fields"]}
    pol = fields["policy_number"]
    assert pol["value"] == "AP-2025-0001" and pol["extraction_method"] == "llm_grounded" and pol["evidence_state"] != "found_suspect"
    assert pol["grounding_quote"] == "Ref AP-2025-0001 (policy) issued to John Q. Sample"
    assert pol["grounding_model"] == "injected" and pol["grounding_source"] == "llm"
    assert pol["grounding_span"]["page"] == 1 and pol["grounding_span"]["end_char"] > pol["grounding_span"]["start_char"] >= 0
    assert pol["provenance_confidence"] == 1.0 and pol["value_quality"]["quality"] == "valid"
    assert fields["insured_name"]["value"] == "John Q. Sample"
    # 4. the ledger shows the pass, not as the reviewer's attempt
    passes = [h for h in r["replay"]["history"] if h["trigger"] == "pipeline:redhat_targeted"]
    assert len(passes) == 1 and passes[0]["improved"] is True and set(passes[0]["fields_changed"]) == {"policy_number", "insured_name"}
    assert r["replay"]["passes"] == 1 and r["replay"]["attempts"] == 0 and exe["rerun"]["passes"] == 1 and exe["rerun"]["improved"] is True
    # 5. the critique re-ran over the changed graph and the suspect findings are gone
    assert r["redhat"]["previous_counts"]["medium"] >= 2 and not [f for f in r["redhat"]["findings"] if f["rule"] == "suspect_value"]
    # 6. untouched fields keep their anchors (the fixture tree maps one chunk, so the layout node is the pointer); uids are stable
    for name, node in (("vin", "el-5"), ("premium", "el-6"), ("agent_name", "el-10"), ("effective_date", "el-3")):
        assert fields[name]["field_source_node_id"] == node, (name, fields[name]["field_source_node_id"])
    assert set(targeted["fields"]) == {"policy_number", "insured_name"}  # the flattening finding is not a reason to re-read a field
    assert r["graph_integrity"]["orphans"] == 0 and r["graph_integrity"]["integrity_score"] == 1.0
    assert all(f["field_uid"] == f"field-{r['document_id']}-{f['name']}" for f in r["fields"])
    # 7. the stored row is the canonical snapshot and the JSON API, the export and the record agree on it
    stored = _stored(out["report_id"])
    assert snap.verify(stored)["ok"] and stored["fields"][0]["value"] == r["fields"][0]["value"]
    api = client.get(f"/api/projects/default/parsure/{out['report_id']}").get_json()
    assert api["integrity"]["ok"] is True and {f["name"]: f["value"] for f in api["report"]["fields"]}["policy_number"] == "AP-2025-0001"
    exported = client.get(f"/api/projects/default/parsure/{out['report_id']}/export?format=json")
    assert exported.status_code == 200
    body = exported.get_json()
    ex_fields = body.get("fields") or (body.get("report") or {}).get("fields")
    assert {f["name"]: f["value"] for f in ex_fields}["policy_number"] == "AP-2025-0001"
    assert (body.get("snapshot") or (body.get("report") or {}).get("snapshot"))["content_hash"] == stored["snapshot"]["content_hash"]
    page = client.get(f"/parsing/{out['report_id']}")
    assert page.status_code == 200 and "AP-2025-0001" in page.get_data(as_text=True)
    # 8. a replay of the same bytes compares equal (determinism)
    rp = client.post(f"/api/projects/default/parsure/{out['report_id']}/replay", json={})
    assert rp.status_code == 200 and rp.get_json()["proof"]["deterministic"] is True, rp.get_json()
    # 9. a tampered row exports nothing
    from prompt_matrix.history import get_db
    tampered = _stored(out["report_id"])  # the row as the replay left it
    tampered["fields"][0]["value"] = "TAMPERED"
    db = get_db()
    db.execute("UPDATE parsure_reports SET report_json = ? WHERE report_id = ?", (json.dumps(tampered, ensure_ascii=False, default=str), out["report_id"]))
    db.commit()
    refused = client.get(f"/api/projects/default/parsure/{out['report_id']}/export?format=json")
    assert refused.status_code == 409 and refused.get_json()["error"].startswith("artifact integrity")


def test_z3_violation_changes_the_output_and_the_ledger_says_so(client):
    from prompt_matrix.services.v1_orchestrator import run_after_parse

    violation = {"severity": "high", "category": "numeric", "description": "premium $1,250.00 exceeds the liability limit ratio", "node_id": None}
    ver = {"z3": {"z3_status": "VIOLATION", "violations": [violation], "checked_at": "2026-09-27T00:00:00+00:00"}, "z3_status": "VIOLATION", "redhat_status": "complete"}
    out = run_after_parse("default", bundle=jdf_cli_bundle(), verification=ver, filename="z3.pdf", file_bytes=b"z3", result=RESULT, job_id=None,
                          intake=None, completion=lambda prompt: "{}")
    r = out["report"]
    premium = next(f for f in r["fields"] if f["name"] == "premium")
    assert premium["z3_violation"] is True and premium["field_state"] == "rejected" and premium["routing_action"] == "compliance_review"
    assert premium["verification_confidence"] == 0.0 and premium["verification_source"] == "z3"
    assert r["execution"]["z3"] == {"status": "VIOLATION", "violations": 1, "reason": None}
    assert r["verification"]["z3_violation_count"] == 1 and r["review_summary"]["fields_rejected"] >= 1
    # the same document with a passing verification is not rejected: the verifier, not a default, decided
    out2 = run_after_parse("default", bundle=jdf_cli_bundle(), verification=VERIFICATION, filename="z3b.pdf", file_bytes=b"z3b", result=RESULT,
                           job_id=None, intake=None, completion=lambda prompt: "{}")
    premium2 = next(f for f in out2["report"]["fields"] if f["name"] == "premium")
    assert premium2["field_state"] == "unverified" and premium2["z3_violation"] is False
    assert premium2["verification_confidence"] == 1.0 and premium2["verification_source"] == "plausibility_rule"  # a rule spoke; the default did not
    agent2 = next(f for f in out2["report"]["fields"] if f["name"] == "agent_name")
    assert agent2["verification_confidence"] == 0.85 and agent2["verification_basis"].startswith("document-level Z3 PASS")


def test_grounded_first_pass_is_a_recorded_pipeline_pass_with_grounding(client):
    from prompt_matrix.services.v1_orchestrator import run_after_parse

    lines = [l for l in DEBRIS_LINES if not l.startswith("Agent:")] + ["This policy was countersigned by Mary Agent on behalf of the carrier."]
    calls = []

    def completion(prompt):
        calls.append(prompt)
        return json.dumps({"agent_name": {"quote": "countersigned by Mary Agent on behalf", "value": "Mary Agent", "page": 1}})

    out = run_after_parse("default", bundle=jdf_cli_bundle(lines), verification=VERIFICATION, filename="agent.pdf", file_bytes=b"agent", result={},
                          job_id=None, intake=None, completion=completion, tree=_tree_for(lines))
    r = out["report"]
    agent = next(f for f in r["fields"] if f["name"] == "agent_name")
    assert agent["value"] == "Mary Agent" and agent["extraction_method"] == "llm_grounded"
    assert agent["grounding_quote"] == "countersigned by Mary Agent on behalf" and agent["grounding_source"] == "llm" and agent["grounding_model"] == "injected"
    llm = r["execution"]["llm_grounding"]
    assert llm["status"] == "ran" and "agent_name" in llm["fields_filled"] and llm["fields_offered"] >= 3 and llm["ms"] >= 0
    first = [h for h in r["replay"]["history"] if h["trigger"] == "pipeline:llm_grounded"]
    assert len(first) == 1 and first[0]["improved"] is True and "agent_name" in first[0]["fields_changed"]
    assert r["replay"]["attempts"] == 0 and r["replay"]["passes"] >= 1 and r["replay"]["stop_rule"] is None
    # a label-anchored field carries its own (secondary) grounding
    vin = next(f for f in r["fields"] if f["name"] == "vin")
    assert vin["grounding_quote"] == "VIN: 1HGCM82633A004352" and vin["grounding_model"] == "label_anchor" and vin["grounding_source"] == "label_anchor"
    absent = [f for f in r["fields"] if f["value"] is None and f["field_type"] != "signature" and f["evidence_state"] != "found_suspect"]
    assert all(f["grounding_quote"] is None and f["verification_confidence"] is None for f in absent)
    suspects = [f for f in r["fields"] if f["evidence_state"] == "found_suspect"]
    assert suspects and all(f["grounding_quote"] and f["provenance_confidence"] < 1.0 for f in suspects)  # the debris is quoted, not trusted


def test_export_is_refused_when_the_field_graph_has_orphans(client):
    from prompt_matrix.db import parsure_repository as repo

    legacy = {"report_id": "pr-orphans", "filename": "flat.txt", "document_id": "doc-flat", "classification": {"document_type": "auto_policy"},
              "fields": [{"name": "policy_number", "label": "Policy number", "value": "AP-1", "field_state": "unverified", "routing_action": "manual_review"},
                         {"name": "vin", "label": "VIN", "value": None, "field_state": "not_found", "routing_action": "field_not_found"}],
              "review_summary": {"fields_total": 2, "fields_review": 1, "fields_rejected": 0},
              "graph_integrity": {"fields": 2, "anchored": 0, "orphans": 2, "integrity_score": 0.0, "orphan_list": ["policy_number", "vin"]}}
    repo.save_report("default", legacy)
    res = client.get("/api/projects/default/parsure/pr-orphans/export?format=json")
    assert res.status_code == 409 and "integrity floor" in res.get_json()["error"] and res.get_json()["orphans"] == ["policy_number", "vin"]
