"""Parsure review API: list/latest/get, accept/correct/dispute/resolve, field
history, classification override with re-extraction, JSON/CSV export, audit
log and analytics (spec §9 items 17–24, 27)."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta

import pytest

from tests.test_v1_orchestrator import POLICY_LINES, RESULT, VERIFICATION, jdf_cli_bundle


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "parsure.sqlite"))
    monkeypatch.setenv("ASSURE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WTF_CSRF_ENABLED", "0")
    monkeypatch.setenv("CELERY_BROKER_URL", "")
    monkeypatch.delenv("ASSURE_S3_BUCKET", raising=False)
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db
    from prompt_matrix.db.jdf_repository import ensure_project
    from prompt_matrix.web import create_app

    init_db()
    ensure_project("default")
    return create_app(require_auth=False).test_client()


def _seed(project="default", lines=POLICY_LINES, **kw):
    from prompt_matrix.services.v1_orchestrator import run_after_parse

    params = dict(bundle=jdf_cli_bundle(lines), verification=VERIFICATION, filename="policy.pdf", file_bytes=None, result=RESULT, job_id="job-1", intake=None)
    params.update(kw)
    out = run_after_parse(project, **params)
    assert out
    return out["report_id"]


def _field(client, report_id, name):
    report = client.get(f"/api/projects/default/parsure/{report_id}").get_json()["report"]
    return next(f for f in report["fields"] if f["name"] == name)


def test_latest_is_404_until_a_report_exists(client):
    res = client.get("/api/projects/default/parsure/latest")
    assert res.status_code == 404 and res.get_json() == {"ok": False, "error": "No intake report yet."}
    listing = client.get("/api/projects/default/parsure").get_json()
    assert listing["ok"] and listing["reports"] == [] and listing["analytics"]["documents"] == 0


def test_list_latest_and_get(client):
    rid = _seed()
    listing = client.get("/api/projects/default/parsure").get_json()
    assert len(listing["reports"]) == 1
    summary = listing["reports"][0]
    assert summary["report_id"] == rid and summary["document_type"] == "auto_policy" and summary["parser_name"] == "jdf-cli"
    assert set(summary) >= {"filename", "material_type", "modality", "page_count", "document_quality_score", "review_summary", "quality_summary", "replay_eligible", "created_at"}
    latest = client.get("/api/projects/default/parsure/latest").get_json()
    assert latest["report"]["report_id"] == rid and "_page_texts" not in latest["report"]
    detail = client.get(f"/api/projects/default/parsure/{rid}").get_json()
    assert detail["ok"] and detail["corrections"] == [] and detail["disputes"] == []
    assert client.get("/api/projects/default/parsure/pr-nope").status_code == 404
    assert client.get(f"/api/projects/other/parsure/{rid}").status_code == 404


def test_accept_sets_state_and_routing_and_logs(client):
    rid = _seed()
    res = client.post(f"/api/projects/default/parsure/{rid}/fields/premium/accept", json={"actor": "reviewer@x"})
    assert res.status_code == 200
    f = res.get_json()["field"]
    assert (f["field_state"], f["routing_action"], f["review_required"]) == ("accepted", "none", False)
    events = client.get(f"/api/projects/default/parsure/audit-log?report_id={rid}").get_json()["events"]
    assert events[0]["event_type"] == "field_accepted" and events[0]["field_name"] == "premium" and events[0]["actor"] == "reviewer@x"
    # A field with no value cannot be "accepted" into existence.
    rid2 = _seed(lines=[line for line in POLICY_LINES if not line.startswith("VIN")])
    assert client.post(f"/api/projects/default/parsure/{rid2}/fields/vin/accept", json={}).status_code == 409


def test_correct_then_history_then_export(client):
    rid = _seed()
    before = _field(client, rid, "premium")
    assert before["value"] == 1250.0
    res = client.post(f"/api/projects/default/parsure/{rid}/fields/premium/correct", json={"value": "$1,300.00", "reason": "declarations page says 1300", "actor": "ana"})
    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    assert body["field"]["value"] == 1300.0 and body["field"]["corrected"] is True
    assert (body["field"]["field_state"], body["field"]["routing_action"]) == ("accepted", "none")
    assert body["replay"]["eligible"] is True and any("corrected" in r for r in body["replay"]["reasons"])
    assert "value" in client.post(f"/api/projects/default/parsure/{rid}/fields/premium/correct", json={}).get_json()["error"]

    hist = client.get(f"/api/projects/default/parsure/{rid}/fields/premium/history").get_json()
    assert hist["original_value"] == 1250.0
    assert len(hist["corrections"]) == 1 and hist["corrections"][0]["corrected_value"] == 1300.0 and hist["corrections"][0]["actor"] == "ana"
    assert hist["events"][0]["event_type"] == "field_corrected"

    js = client.get(f"/api/projects/default/parsure/{rid}/export?format=json")
    assert js.status_code == 200 and "attachment" in js.headers["Content-Disposition"]
    exported = js.get_json()
    assert "_page_texts" not in exported and next(f for f in exported["fields"] if f["name"] == "premium")["value"] == 1300.0

    cs = client.get(f"/api/projects/default/parsure/{rid}/export?format=csv")
    assert cs.status_code == 200 and cs.mimetype == "text/csv" and cs.headers["Content-Disposition"].endswith('.csv"')
    rows = list(csv.DictReader(io.StringIO(cs.get_data(as_text=True))))
    assert {r["name"] for r in rows} == {f["name"] for f in exported["fields"]}
    premium_row = next(r for r in rows if r["name"] == "premium")
    assert premium_row["value"] == "1300.0" and premium_row["field_state"] == "accepted" and premium_row["source_page"] == "1"
    assert set(rows[0]) == {"name", "label", "value", "extraction_confidence", "confidence_basis", "verification_confidence", "field_state", "routing_action", "review_required", "reason", "source_page"}
    events = [e["event_type"] for e in client.get(f"/api/projects/default/parsure/audit-log?report_id={rid}").get_json()["events"]]
    assert events[:2] == ["exported", "exported"]
    assert client.get(f"/api/projects/default/parsure/{rid}/export?format=xml").status_code == 400


def test_correct_vin_rejects_bad_check_digit(client):
    rid = _seed()
    bad = client.post(f"/api/projects/default/parsure/{rid}/fields/vin/correct", json={"value": "1HGCM82633A004353"})
    assert bad.status_code == 400 and "check digit" in bad.get_json()["error"]
    good = client.post(f"/api/projects/default/parsure/{rid}/fields/vin/correct", json={"value": "4t1bf3ek6bu123456"})
    assert good.status_code == 200 and good.get_json()["field"]["value"] == "4T1BF3EK6BU123456"


def test_dispute_has_72h_due_and_resolve_paths(client):
    rid = _seed()
    res = client.post(f"/api/projects/default/parsure/{rid}/fields/liability_limit/dispute", json={"reason": "limit disputed by insured", "actor": "bob"})
    assert res.status_code == 201, res.get_json()
    dispute = res.get_json()["dispute"]
    opened = datetime.strptime(dispute["opened_at"], "%Y-%m-%d %H:%M:%S")
    due = datetime.strptime(dispute["due_at"], "%Y-%m-%d %H:%M:%S")
    assert due - opened == timedelta(hours=72) and dispute["status"] == "open"
    f = res.get_json()["field"]
    assert (f["field_state"], f["routing_action"]) == ("disputed", "adjudicator_queue") and f["dispute_id"] == dispute["dispute_id"]
    assert client.post(f"/api/projects/default/parsure/{rid}/fields/liability_limit/dispute", json={"reason": "again"}).status_code == 409
    assert client.post(f"/api/projects/default/parsure/{rid}/fields/premium/dispute", json={}).status_code == 400

    analytics = client.get("/api/projects/default/parsure/analytics").get_json()["analytics"]
    assert analytics["disputes_open"] == 1

    # Resolve with a value → accepted + correction recorded.
    res = client.post(f"/api/projects/default/parsure/{rid}/disputes/{dispute['dispute_id']}/resolve", json={"resolution": "policy schedule confirms 150000", "value": 150000, "actor": "adj"})
    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    assert body["dispute"]["status"] == "resolved" and body["dispute"]["resolved_at"]
    assert body["field"]["value"] == 150000.0 and (body["field"]["field_state"], body["field"]["routing_action"]) == ("accepted", "none")
    assert client.post(f"/api/projects/default/parsure/{rid}/disputes/{dispute['dispute_id']}/resolve", json={"resolution": "x"}).status_code == 409
    hist = client.get(f"/api/projects/default/parsure/{rid}/fields/liability_limit/history").get_json()
    assert len(hist["disputes"]) == 1 and hist["disputes"][0]["status"] == "resolved" and len(hist["corrections"]) == 1

    # Resolve without a value → rejected.
    res = client.post(f"/api/projects/default/parsure/{rid}/fields/agent_name/dispute", json={"reason": "not the agent of record"})
    d2 = res.get_json()["dispute"]["dispute_id"]
    res = client.post(f"/api/projects/default/parsure/{rid}/disputes/{d2}/resolve", json={"resolution": "agent not on file"})
    assert res.get_json()["field"]["field_state"] == "rejected"
    types = [e["event_type"] for e in client.get(f"/api/projects/default/parsure/audit-log?report_id={rid}").get_json()["events"]]
    assert types.count("dispute_opened") == 2 and types.count("dispute_resolved") == 2


def test_classification_override_reextracts_for_the_new_type(client):
    rid = _seed()
    res = client.post(f"/api/projects/default/parsure/{rid}/classification", json={"document_type": "auto_claim", "reason": "this is the loss notice", "actor": "ana"})
    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    assert body["reextracted"] is True
    cls = body["report"]["classification"]
    assert cls["document_type"] == "auto_claim" and cls["override"]["previous"] == "auto_policy" and cls["override"]["actor"] == "ana"
    names = {f["name"] for f in body["report"]["fields"]}
    assert "claim_number" in names and "collision_deductible" not in names
    claim = next(f for f in body["report"]["fields"] if f["name"] == "claim_number")
    assert claim["value"] is None and claim["reason"] == "field not found"
    assert client.post(f"/api/projects/default/parsure/{rid}/classification", json={"document_type": "invoice"}).status_code == 400
    events = client.get(f"/api/projects/default/parsure/audit-log?report_id={rid}").get_json()["events"]
    assert events[0]["event_type"] == "classification_overridden" and events[0]["payload"]["document_type"] == "auto_claim"
    listing = client.get("/api/projects/default/parsure").get_json()
    assert listing["analytics"]["by_document_type"] == {"auto_claim": 1}


def test_analytics_counters_are_counts(client, tmp_path, monkeypatch):
    rid1 = _seed()
    rid2 = _seed(lines=[line.replace("AP-2025-0001", "AP-7") for line in POLICY_LINES], result={"document_id": "doc-2", "revision_id": "rev-2", "version": 2})
    client.post(f"/api/projects/default/parsure/{rid1}/fields/agent_name/correct", json={"value": "Someone Else"})
    client.post(f"/api/projects/default/parsure/{rid2}/fields/premium/dispute", json={"reason": "r"})
    monkeypatch.setenv("PARSURE_GOLDEN_RESULTS", str(tmp_path / "missing.json"))
    a = client.get("/api/projects/default/parsure/analytics").get_json()["analytics"]
    assert a["documents"] == 2 and a["by_document_type"] == {"auto_policy": 2}
    assert a["corrections"] == 1 and a["disputes_open"] == 1
    listing = client.get("/api/projects/default/parsure").get_json()["reports"]
    assert a["replay_eligible"] == sum(1 for r in listing if r["replay_eligible"]) >= 1
    assert a["fields_total"] == 24 and a["fields_review"] >= 2 and a["fields_rejected"] >= 0
    assert a["golden_accuracy"] is None
    (tmp_path / "missing.json").write_text('{"field_accuracy": 0.98, "type_accuracy": 1.0, "documents": 6, "fields_checked": 52, "run_at": "2026-09-25T00:00:00+00:00"}')
    a = client.get("/api/projects/default/parsure/analytics").get_json()["analytics"]
    assert a["golden_accuracy"]["field_accuracy"] == 0.98
    detail = client.get(f"/api/projects/default/parsure/{rid2}").get_json()["report"]
    assert [c["field"] for c in detail["conflicts"]] == ["policy_number"]
