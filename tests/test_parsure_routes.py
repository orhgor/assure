"""Parsure review API: list/latest/get, accept/correct/dispute/resolve, field
history, classification override with re-extraction, JSON/CSV export, audit
log and analytics (spec §9 items 17–24, 27)."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone

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
    assert claim["value"] is None and claim["reason"] == "Not on this document type" and claim["evidence_state"] == "not_on_document"
    assert claim["field_source_node_id"] == "el-0" and claim["evidence"]["kind"] == "absent"  # anchored to the first layout node
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


# --------------------------------------------------------------------------
# Review queue (spec §9 item 18) and dispute SLA (item 19/25)
# --------------------------------------------------------------------------

def _sql(sql, params=()):
    from prompt_matrix.history import get_db

    db = get_db()
    db.execute(sql, params)
    db.commit()


def test_queue_orders_newest_report_first_and_overdue_disputes_first(client):
    rid_old = _seed()
    rid_new = _seed(lines=[line.replace("AP-2025-0001", "AP-7") for line in POLICY_LINES], result={"document_id": "doc-2", "revision_id": "rev-2", "version": 2})
    _sql("UPDATE parsure_reports SET created_at = ? WHERE report_id = ?", ("2026-01-01 00:00:00", rid_old))

    # An old dispute whose 72 h have passed, and a fresh one due within 24 h.
    d_old = client.post(f"/api/projects/default/parsure/{rid_old}/fields/liability_limit/dispute", json={"reason": "limit disputed"}).get_json()["dispute"]
    _sql("UPDATE parsure_disputes SET due_at = ? WHERE dispute_id = ?", ("2020-01-01 00:00:00", d_old["dispute_id"]))
    d_new = client.post(f"/api/projects/default/parsure/{rid_new}/fields/premium/dispute", json={"reason": "premium disputed"}).get_json()["dispute"]
    soon = (datetime.utcnow() + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    _sql("UPDATE parsure_disputes SET due_at = ? WHERE dispute_id = ?", (soon, d_new["dispute_id"]))

    res = client.get("/api/projects/default/parsure/queue?limit=200")
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] and set(body) >= {"items", "counts", "total", "now"}
    items = body["items"]
    assert items, "queue must list the fields still asking for a person"
    for it in items:
        assert it["routing_action"] != "none" or it["field_state"] in ("disputed", "rejected")
        assert set(it) >= {"report_id", "document_id", "filename", "document_type", "field_name", "label", "value", "extraction_confidence", "reason", "field_state", "routing_action", "dispute", "created_at"}

    # Newest report first ...
    order = [it["report_id"] for it in items]
    assert order.index(rid_new) < order.index(rid_old)
    assert order == sorted(order, key=lambda r: 0 if r == rid_new else 1)
    # ... and within the old report the overdue dispute leads.
    old_items = [it for it in items if it["report_id"] == rid_old]
    assert old_items[0]["field_name"] == "liability_limit"
    assert old_items[0]["field_state"] == "disputed" and old_items[0]["dispute"]["overdue"] is True
    assert old_items[0]["dispute"]["due_words"].startswith("overdue by")
    new_items = [it for it in items if it["report_id"] == rid_new]
    assert new_items[0]["field_name"] == "premium" and new_items[0]["dispute"]["overdue"] is False
    assert new_items[0]["dispute"]["due_words"].startswith("due in")
    undisputed = [it for it in items if it["dispute"] is None]
    assert undisputed and all(it["field_state"] != "disputed" for it in undisputed)

    counts = body["counts"]
    assert counts["disputed"] == 2 and counts["overdue"] == 1 and counts["rejected"] == 0
    assert counts["needs_review"] == len(items) - 2
    assert body["total"] == len(items)
    assert client.get("/api/projects/default/parsure/queue?limit=1").get_json()["items"] == items[:1]

    # SLA surfaces on dispute rows and in analytics.
    disputes = client.get(f"/api/projects/default/parsure/{rid_old}").get_json()["disputes"]
    assert disputes[0]["overdue"] is True and disputes[0]["status"] == "open"
    a = client.get("/api/projects/default/parsure/analytics").get_json()["analytics"]
    assert a["disputes_open"] == 2 and a["disputes_overdue"] == 1 and a["disputes_due_24h"] == 1

    # Resolving the overdue dispute clears it from the queue and the counters.
    client.post(f"/api/projects/default/parsure/{rid_old}/disputes/{d_old['dispute_id']}/resolve", json={"resolution": "confirmed"})
    body = client.get("/api/projects/default/parsure/queue").get_json()
    assert body["counts"]["overdue"] == 0 and body["counts"]["rejected"] == 1
    assert client.get("/api/projects/default/parsure/analytics").get_json()["analytics"]["disputes_overdue"] == 0


def test_queue_is_empty_for_a_project_without_reports(client):
    body = client.get("/api/projects/default/parsure/queue").get_json()
    assert body == {"ok": True, "items": [], "counts": {"needs_review": 0, "disputed": 0, "overdue": 0, "rejected": 0, "documents": 0, "nothing_extracted": 0, "fields_found": 0, "schema_mismatch": 0, "redhat_high": 0},
                    "total": 0, "now": body["now"]}


def _report(report_id, *, quality, created_at, modality="scanned_pdf", flags=(), fields=()):
    return {
        "report_id": report_id,
        "document_id": f"doc-{report_id}",
        "filename": f"{report_id}.pdf",
        "modality": modality,
        "material_type": "pdf",
        "document_quality_score": quality,
        "quality_flags": list(flags),
        "pages": [],
        "classification": {"document_type": "auto_policy", "confidence": 0.9},
        "fields": list(fields),
        "replay": {"eligible": report_id.endswith("replay")},
        "created_at": created_at,
    }


def _f(name, *, value="x", state="unverified", routing="manual_review", reason="", **extra):
    field = {"name": name, "label": name, "value": value, "field_state": state, "routing_action": routing,
             "review_required": routing != "none", "extraction_confidence": 0.5, "reason": reason}
    field.update(extra)
    return field


def test_analytics_histogram_trend_and_issue_distribution(client, monkeypatch):
    from prompt_matrix.db import parsure_repository as repo

    fixed_now = datetime(2026, 9, 25, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(repo, "_now_dt", lambda: fixed_now)
    day = lambda n: (fixed_now - timedelta(days=n)).strftime("%Y-%m-%d %H:%M:%S")  # noqa: E731

    repo.save_report("default", _report("r1", quality=0.10, created_at=day(0), flags=["blurry"], fields=[
        _f("a", value=None, reason="field not found"),
        _f("b", reason="extraction_confidence 0.40 < 0.75"),
        _f("c", state="accepted", routing="none"),
    ]))
    repo.save_report("default", _report("r2", quality=0.55, created_at=day(0), modality="phone_photo", flags=["blurry", "glare"], fields=[
        _f("sig", field_type="signature", reason="signature_quality faint: low ink"),
        _f("n", reason="number_quality handwritten: smudged", number_quality={"review_required": True}),
    ]))
    repo.save_report("default", _report("r3-replay", quality=0.90, created_at=day(3), fields=[
        _f("z", state="rejected", routing="compliance_review", reason="Z3 violation: sum mismatch", z3_violation=True),
        _f("p", reason="plausibility rule 'x' failed: too low"),
        _f("k", reason="compliance-bound field — human confirmation required"),
    ]))
    repo.save_report("default", _report("r4", quality=None, created_at=day(3), fields=[_f("q", state="accepted", routing="none")]))
    repo.save_report("default", _report("r5", quality=0.80, created_at=day(45), fields=[]))  # outside the 30-day window
    repo.record_correction("default", "r1", "b", original_value="x", corrected_value="y", actor="t", reason=None)

    a = client.get("/api/projects/default/parsure/analytics").get_json()["analytics"]
    assert a["documents"] == 5 and a["quality_unscored"] == 1
    assert [h["bucket"] for h in a["quality_histogram"]] == ["0.0–0.2", "0.2–0.4", "0.4–0.6", "0.6–0.8", "0.8–1.0"]
    assert [h["count"] for h in a["quality_histogram"]] == [1, 0, 1, 0, 2]
    assert a["by_modality"] == {"scanned_pdf": 4, "phone_photo": 1}
    assert a["fields_total"] == 9 and a["fields_review"] == 7
    assert a["review_rate"] == round(7 / 9, 4) and a["correction_rate"] == round(1 / 9, 4) and a["replay_eligible_rate"] == round(1 / 5, 4)
    assert a["disputes_open"] == 0 and a["disputes_overdue"] == 0 and a["disputes_due_24h"] == 0

    flags = {i["key"]: i["count"] for i in a["issue_distribution"]["quality_flags"]}
    assert flags == {"blurry": 2, "glare": 1}
    reasons = {i["key"]: i["count"] for i in a["issue_distribution"]["field_reasons"]}
    assert reasons == {"not_found": 1, "low_confidence": 1, "signature": 1, "number_quality": 1, "verification": 1, "plausibility": 1, "compliance": 1}
    assert a["issue_distribution"]["field_reasons"][0]["label"] in {"Compliance-bound", "Low confidence", "Not found", "Number quality", "Plausibility", "Signature", "Verification"}

    trend = a["trend"]
    assert len(trend) == 30 and a["trend_days"] == 30
    assert trend[-1]["day"] == "2026-09-25" and trend[0]["day"] == "2026-08-27"
    today = trend[-1]
    assert today["documents"] == 2 and today["avg_quality"] == round((0.10 + 0.55) / 2, 3) and today["fields_review"] == 4
    three = next(d for d in trend if d["day"] == "2026-09-22")
    assert three["documents"] == 2 and three["avg_quality"] == 0.9 and three["fields_review"] == 3
    assert all(d["documents"] == 0 and d["avg_quality"] is None for d in trend if d["day"] not in ("2026-09-25", "2026-09-22"))


def test_analytics_rates_are_none_not_zero_without_fields(client):
    a = client.get("/api/projects/default/parsure/analytics").get_json()["analytics"]
    assert a["documents"] == 0 and a["avg_document_quality"] is None
    assert a["review_rate"] is None and a["correction_rate"] is None and a["replay_eligible_rate"] is None
    assert [h["count"] for h in a["quality_histogram"]] == [0, 0, 0, 0, 0]
    assert a["issue_distribution"] == {"quality_flags": [], "field_reasons": []} and a["by_modality"] == {}
    assert len(a["trend"]) == 30 and all(d["avg_quality"] is None for d in a["trend"])


# --------------------------------------------------------------------------
# Project-wide export (client ask of 2026-09-25: the extracted data, in bulk)
# --------------------------------------------------------------------------

def test_project_export_csv_long_wide_json_and_filters(client):
    from prompt_matrix.routers.parsure_routes import PROJECT_CSV_COLUMNS

    rid_policy = _seed()
    rid_claim = _seed(lines=[line.replace("AP-2025-0001", "AP-7") for line in POLICY_LINES], filename="loss-notice.pdf",
                      result={"document_id": "doc-2", "revision_id": "rev-2", "version": 2})
    client.post(f"/api/projects/default/parsure/{rid_claim}/classification", json={"document_type": "auto_claim", "reason": "loss notice"})
    client.post(f"/api/projects/default/parsure/{rid_policy}/fields/premium/accept", json={"actor": "ana"})

    # Long CSV: one row per document × field, the documented columns, a dated filename.
    res = client.get("/api/projects/default/parsure/export?format=csv")
    assert res.status_code == 200 and res.mimetype == "text/csv"
    import re as _re
    assert _re.fullmatch(r'attachment; filename="parsure-default-\d{4}-\d{2}-\d{2}\.csv"', res.headers["Content-Disposition"])
    rows = list(csv.DictReader(io.StringIO(res.get_data(as_text=True))))
    assert tuple(rows[0].keys()) == PROJECT_CSV_COLUMNS
    reports = {r["report_id"]: r for r in client.get("/api/projects/default/parsure").get_json()["reports"]}
    assert len(rows) == sum(r["review_summary"]["fields_total"] for r in reports.values())
    assert {r["report_id"] for r in rows} == {rid_policy, rid_claim}
    assert {r["document_type"] for r in rows} == {"auto_policy", "auto_claim"}
    premium = next(r for r in rows if r["report_id"] == rid_policy and r["field"] == "premium")
    assert premium["label"] == "Premium" and premium["value"] == "1250.0" and premium["field_state"] == "accepted" and premium["source_page"] == "1"
    assert premium["filename"] == "policy.pdf" and premium["created_at"]

    # Filters: by type, by state; not_found rows have no value, accepted rows are accepted.
    only_claim = list(csv.DictReader(io.StringIO(client.get("/api/projects/default/parsure/export?format=csv&document_type=auto_claim").get_data(as_text=True))))
    assert only_claim and all(r["document_type"] == "auto_claim" for r in only_claim)
    missing = list(csv.DictReader(io.StringIO(client.get("/api/projects/default/parsure/export?format=csv&state=not_found").get_data(as_text=True))))
    assert missing and all(r["value"] == "" for r in missing)
    accepted = list(csv.DictReader(io.StringIO(client.get("/api/projects/default/parsure/export?format=csv&state=accepted").get_data(as_text=True))))
    assert accepted and all(r["field_state"] == "accepted" for r in accepted)
    review = list(csv.DictReader(io.StringIO(client.get("/api/projects/default/parsure/export?format=csv&state=needs_review").get_data(as_text=True))))
    assert review and all(r["field_state"] != "accepted" for r in review)
    assert client.get("/api/projects/default/parsure/export?format=csv&state=bogus").status_code == 400
    assert client.get("/api/projects/default/parsure/export?format=xml").status_code == 400
    assert client.get("/api/projects/nope/parsure/export?format=csv").status_code == 404

    # Wide CSV: one row per document, labels as headers, a needs_review count.
    wide = client.get("/api/projects/default/parsure/export?format=csv&wide=1")
    assert wide.status_code == 200
    table = list(csv.reader(io.StringIO(wide.get_data(as_text=True))))
    header, body = table[0], table[1:]
    assert header[:5] == ["report_id", "filename", "document_type", "created_at", "needs_review"]
    assert "Policy number" in header and "Premium" in header and "Claim number" in header
    assert header.index("Policy number") < header.index("Premium")  # taxonomy order
    assert len(set(header)) == len(header)  # no two columns read the same
    assert len(body) == 2 and {r[0] for r in body} == {rid_policy, rid_claim}
    policy_row = next(r for r in body if r[0] == rid_policy)
    assert policy_row[header.index("Premium")] == "1250.0" and policy_row[header.index("Claim number")] == ""
    assert int(policy_row[4]) == reports[rid_policy]["review_summary"]["fields_review"]

    # JSON: fields nested by name, private keys absent.
    js = client.get("/api/projects/default/parsure/export?format=json")
    assert js.status_code == 200 and js.headers["Content-Disposition"].endswith('.json"')
    body = js.get_json()
    assert body["ok"] and body["project_id"] == "default" and len(body["documents"]) == 2
    doc = next(d for d in body["documents"] if d["report_id"] == rid_policy)
    assert set(doc) == {"report_id", "document_id", "filename", "document_type", "created_at", "fields"}
    assert doc["fields"]["premium"]["value"] == 1250.0 and doc["fields"]["premium"]["field_state"] == "accepted"
    assert "_page_texts" not in json_dumps(body)

    # One project-scoped audit event per download.
    events = client.get("/api/projects/default/parsure/audit-log").get_json()["events"]
    exported = [e for e in events if e["event_type"] == "exported" and e["payload"].get("scope") == "project"]
    assert len(exported) == 7 and exported[0]["report_id"] is None
    assert exported[0]["payload"]["format"] == "json" and exported[0]["payload"]["documents"] == 2
    assert any(e["payload"]["wide"] for e in exported)
    assert any(e["payload"]["filters"] == {"document_type": "auto_claim", "state": None} for e in exported)


def json_dumps(value):
    import json

    return json.dumps(value, default=str)


def test_report_page_texts_and_find_report_are_repository_only(client):
    from prompt_matrix.db import parsure_repository as repo

    rid = _seed()
    found = repo.find_report(rid)
    assert found and found["project_id"] == "default" and found["report_id"] == rid
    assert repo.find_report("pr-nope") is None

    texts = repo.report_page_texts("default", rid)
    assert isinstance(texts, list) and texts and all(isinstance(t, str) for t in texts)
    assert "AP-2025-0001" in texts[0]
    assert repo.report_page_texts("default", "pr-nope") is None
    assert repo.report_page_texts("other", rid) is None
    repo.save_report("default", {"report_id": "bare", "filename": "bare.pdf", "fields": [], "pages": []})
    assert repo.report_page_texts("default", "bare") is None

    # The API and both exports never carry the page text.
    for url in (f"/api/projects/default/parsure/{rid}", f"/api/projects/default/parsure/{rid}/export?format=json",
                "/api/projects/default/parsure/export?format=json", "/api/projects/default/parsure/latest"):
        assert "_page_texts" not in client.get(url).get_data(as_text=True), url


def test_summaries_and_queue_carry_fields_found_and_document_counts(client, monkeypatch):
    """``fields_found`` on every list summary and on the queue counts, and the
    queue's ``documents`` equals ``attention_counts`` — one unit per count."""
    from prompt_matrix.db import parsure_repository as repo
    from tests.test_v1_orchestrator import CLAIM_PROSE_LINES

    monkeypatch.setenv("PARSURE_LLM_EXTRACTION", "0")
    rid_ok = _seed()
    rid_empty = _seed(lines=CLAIM_PROSE_LINES, filename="loss-letter.pdf", result={"document_id": "doc-2", "revision_id": "rev-2", "version": 2})
    listing = {r["report_id"]: r for r in client.get("/api/projects/default/parsure").get_json()["reports"]}
    assert listing[rid_ok]["fields_found"] == 11 and listing[rid_ok]["review_summary"]["fields_found"] == 11 and listing[rid_ok]["nothing_extracted"] is False
    assert listing[rid_empty]["fields_found"] == 0 and listing[rid_empty]["nothing_extracted"] is True
    assert listing[rid_empty]["review_summary"]["reasons"][0]["reason"] == "document type may be wrong — change it and the fields are re-read"
    assert listing[rid_empty]["quality_summary"].startswith("No fields could be read as Auto claim.")

    queue = client.get("/api/projects/default/parsure/queue").get_json()
    attention = repo.attention_counts(repo.list_reports("default"))
    assert queue["total"] == attention["fields"] and queue["counts"]["documents"] == attention["documents"] == 2
    assert queue["counts"]["nothing_extracted"] == 1 and queue["counts"]["fields_found"] == 11
    empty_items = [i for i in queue["items"] if i["report_id"] == rid_empty]
    assert len(empty_items) == 10 and all(i["nothing_extracted"] is True and i["fields_total"] == 10 and i["fields_found"] == 0 for i in empty_items)
    assert all(i["nothing_extracted"] is False for i in queue["items"] if i["report_id"] == rid_ok)
    # The analytics column counts by the same rule as the queue.
    a = client.get("/api/projects/default/parsure/analytics").get_json()["analytics"]
    assert a["fields_review"] == queue["total"]


# ---------------------------------------------------------------------------
# Intake graph critique (services/redhat_graph) on the API surfaces
# ---------------------------------------------------------------------------


def _attach_critique(project, report_id, **critique_kw):
    from prompt_matrix.db import parsure_repository as repo
    from prompt_matrix.services import redhat_graph as rg

    report = repo.get_report(project, report_id)
    critique_kw.setdefault("llm", False)
    rg.attach_findings(report, rg.critique_report(report, **critique_kw))
    repo.update_report(project, report_id, report)
    return report


def _strip_critique(project, report_id):
    """Since 2026-09-26 run_after_parse runs the critique itself; these tests
    exercise the 'not run' surfaces, so the auto block is removed first."""
    from prompt_matrix.db import parsure_repository as repo

    report = repo.get_report(project, report_id)
    report.pop("redhat", None)
    report["review_summary"]["reasons"] = [r for r in report["review_summary"].get("reasons", []) if not str(r.get("reason", "")).startswith("Red-Hat: ")]
    repo.update_report(project, report_id, report)


def test_queue_counts_carry_redhat_high_and_summaries_carry_the_block_counts(client, monkeypatch):
    monkeypatch.setenv("PARSURE_LLM_EXTRACTION", "0")
    rid = _seed()
    _strip_critique("default", rid)
    # Before any critique: "not run", zero highs, summary says so.
    summary = client.get("/api/projects/default/parsure").get_json()["reports"][0]
    assert summary["redhat"] == {"ran": False, "count": 0, "high": 0, "medium": 0, "low": 0, "classes": {}}
    assert client.get("/api/projects/default/parsure/queue").get_json()["counts"]["redhat_high"] == 0

    report = _attach_critique("default", rid, export_state={"trust_state": "review_required", "title": "Formal Verification Certificate", "renderer": "text"})
    assert report["redhat"]["counts"]["high"] == 2  # certificate language + fallback renderer
    body = client.get(f"/api/projects/default/parsure/{rid}").get_json()["report"]
    assert body["redhat"]["policy"] == "rh-graph-v1" and body["redhat"]["counts"]["high"] == 2
    assert body["review_summary"]["reasons"][0]["reason"].startswith("Red-Hat: ")
    assert all(f["anchor"]["node_id"] for f in body["redhat"]["findings"])
    summary = client.get("/api/projects/default/parsure").get_json()["reports"][0]
    assert summary["redhat"]["ran"] is True and summary["redhat"]["high"] == 2 and summary["redhat"]["classes"]["export"] == 2
    queue = client.get("/api/projects/default/parsure/queue").get_json()
    assert queue["counts"]["redhat_high"] == 2


def test_analytics_redhat_findings_by_class_counts_only_reports_that_ran(client, monkeypatch):
    monkeypatch.setenv("PARSURE_LLM_EXTRACTION", "0")
    rid_a = _seed()
    rid_b = _seed(filename="second.pdf", result={"document_id": "doc-2", "revision_id": "rev-2", "version": 2})
    _strip_critique("default", rid_a)
    _strip_critique("default", rid_b)
    a = client.get("/api/projects/default/parsure/analytics").get_json()["analytics"]
    assert a["redhat_reports_run"] == 0 and a["redhat_findings_by_class"] == {"structural": 0, "evidentiary": 0, "export": 0}
    report = _attach_critique("default", rid_a, export_state={"trust_state": "not_verified", "renderer": "fallback"})
    a = client.get("/api/projects/default/parsure/analytics").get_json()["analytics"]
    assert a["redhat_reports_run"] == 1
    assert a["redhat_findings_by_class"]["export"] == 1
    assert a["redhat_findings_by_class"] == report["redhat"]["classes"]
    assert a["redhat_findings_by_severity"] == report["redhat"]["counts"]
