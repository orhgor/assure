"""Canonical snapshot, artifact hash gate, stable document_id, bounded replay,
and party-name conflict recall (customer engineering handoff, 2026-09-27:
"State Drift Across Artifacts", "document_id Is Null", "Rerun Thrash").

Every export path — per-report JSON/CSV, project JSON/CSV (long and wide), the
verification dossier and its bundle — is refused with 409 when the stored
report's hash does not match, and every artifact that is produced carries the
hash it was checked against."""

from __future__ import annotations

import csv
import hashlib
import io
import json

import pytest

from tests.test_v1_orchestrator import POLICY_LINES, RESULT, VERIFICATION, jdf_cli_bundle


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "integrity.sqlite"))
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


def _row_json(report_id):
    from prompt_matrix.history import get_db

    row = get_db().execute("SELECT report_json FROM parsure_reports WHERE report_id = ?", (report_id,)).fetchone()
    return json.loads(row[0])


def _tamper(report_id, mutate):
    """Edit the stored JSON behind the repository's back — what a hand edit,
    a partial write or an older build would do."""
    from prompt_matrix.history import get_db

    data = _row_json(report_id)
    mutate(data)
    db = get_db()
    db.execute("UPDATE parsure_reports SET report_json = ? WHERE report_id = ?", (json.dumps(data, ensure_ascii=False, default=str), report_id))
    db.commit()


def _events(client, report_id=None, project="default"):
    url = f"/api/projects/{project}/parsure/audit-log" + (f"?report_id={report_id}" if report_id else "")
    return client.get(url).get_json()["events"]


# --------------------------------------------------------------------------
# Snapshot: stamp / verify
# --------------------------------------------------------------------------

def test_stamp_and_verify_round_trip_and_volatile_keys():
    from prompt_matrix.services import snapshot as snap

    report = {"report_id": "pr-x", "fields": [{"name": "premium", "value": 1250.0}], "redhat": {"policy": "rh-graph-v1"},
              "schema_version": "1.0", "policy_version": "v1", "node_id_policy": "eid-v1", "parser_version": "0.2.3",
              "verification_version": "2026-09-25", "timings_ms": {"total": 812.0}, "_page_texts": ["Premium: $1,250.00"],
              "created_at": "2026-09-27 10:00:00"}
    block = snap.stamp(report)
    assert report["snapshot"] is block
    assert block["algorithm"] == "sha256/canonical-json-v1" and len(block["content_hash"]) == 64 and block["stamped_at"]
    assert block["policy_versions"] == {"schema_version": "1.0", "policy_version": "v1", "node_id_policy": "eid-v1", "redhat_policy": "rh-graph-v1",
                                        "parser_version": "0.2.3", "verification_version": "2026-09-25"}
    check = snap.verify(report)
    assert check == {"ok": True, "expected": block["content_hash"], "actual": block["content_hash"]}

    # Volatile keys do not move the hash; the canonical text does not contain them.
    report["timings_ms"] = {"total": 830.0}
    report["_page_texts"] = ["something else"]
    report["updated_at"] = "2026-09-27 11:00:00"
    report["created_at"] = "2026-09-27T10:00:00+00:00"
    assert snap.verify(report)["ok"] is True
    canon = snap.canonical_json(report)
    for key in snap.VOLATILE_KEYS:
        assert f'"{key}"' not in canon
    assert {"snapshot", "updated_at", "_page_texts", "_page_quality", "_layout", "timings_ms"} <= set(snap.VOLATILE_KEYS)

    # The hash survives a JSON round trip (what the row does) …
    loaded = json.loads(json.dumps(report, ensure_ascii=False, default=str))
    assert snap.verify(loaded)["ok"] is True
    # … and a content change is a mismatch; an unstamped report is not ok either.
    loaded["fields"][0]["value"] = 1300.0
    check = snap.verify(loaded)
    assert check["ok"] is False and check["expected"] == block["content_hash"] and check["actual"] != block["content_hash"]
    with pytest.raises(snap.SnapshotMismatch) as exc:
        snap.require_intact(loaded)
    assert exc.value.payload() == {"ok": False, "error": "artifact integrity: report snapshot mismatch", "report_id": "pr-x",
                                   "expected": block["content_hash"], "actual": check["actual"]}
    assert snap.verify({"report_id": "pr-old", "fields": []}) == {"ok": False, "expected": None, "actual": snap.content_hash({"report_id": "pr-old", "fields": []})}


def test_save_and_update_report_stamp_the_row(client):
    from prompt_matrix.db import parsure_repository as repo
    from prompt_matrix.services import snapshot as snap

    rid = _seed()
    stored = _row_json(rid)
    assert stored["snapshot"]["algorithm"] == "sha256/canonical-json-v1"
    assert snap.verify(stored)["ok"] is True, "the row is its own proof"
    loaded = repo.get_report("default", rid)
    assert snap.verify(loaded)["ok"] is True, "_load's created_at/updated_at rewrite does not move the hash"
    assert loaded["snapshot"]["policy_versions"]["node_id_policy"] == "eid-v1" and loaded["snapshot"]["policy_versions"]["redhat_policy"] == "rh-graph-v1"
    first = loaded["snapshot"]["content_hash"]

    # A repository write re-stamps; the public report keeps the block.
    loaded["fields"][0]["value"] = "changed"
    assert repo.update_report("default", rid, loaded)
    again = repo.get_report("default", rid)
    assert again["snapshot"]["content_hash"] != first and snap.verify(again)["ok"] is True
    assert "snapshot" in repo.public_report(again) and "_page_texts" not in repo.public_report(again)
    detail = client.get(f"/api/projects/default/parsure/{rid}").get_json()
    assert detail["integrity"] == {"ok": True, "expected": again["snapshot"]["content_hash"], "actual": again["snapshot"]["content_hash"]}


# --------------------------------------------------------------------------
# The gate: a tampered row exports nothing
# --------------------------------------------------------------------------

def test_tampered_row_is_refused_by_every_export_and_flagged_on_get(client):
    from prompt_matrix.services import snapshot as snap
    from prompt_matrix.services.verification_dossier import build_dossier

    rid = _seed()
    rid_other = _seed(lines=[line.replace("AP-2025-0001", "AP-7") for line in POLICY_LINES], filename="other.pdf",
                      result={"document_id": "doc-2", "revision_id": "rev-2", "version": 2})
    expected = _row_json(rid)["snapshot"]["content_hash"]
    exports_before = len([e for e in _events(client) if e["event_type"] == "exported"])

    def bump_premium(data):
        next(f for f in data["fields"] if f["name"] == "premium")["value"] = 9999.0

    _tamper(rid, bump_premium)

    # GET still answers — the reviewer must see the row — and says it is not intact.
    detail = client.get(f"/api/projects/default/parsure/{rid}")
    assert detail.status_code == 200
    integrity = detail.get_json()["integrity"]
    assert integrity["ok"] is False and integrity["expected"] == expected and integrity["actual"] != expected
    assert next(f for f in detail.get_json()["report"]["fields"] if f["name"] == "premium")["value"] == 9999.0

    refusal = {"ok": False, "error": "artifact integrity: report snapshot mismatch", "report_id": rid, "expected": expected, "actual": integrity["actual"]}
    for url in (
        f"/api/projects/default/parsure/{rid}/export?format=json",
        f"/api/projects/default/parsure/{rid}/export?format=csv",
        "/api/projects/default/parsure/export?format=json",
        "/api/projects/default/parsure/export?format=csv",
        "/api/projects/default/parsure/export?format=csv&wide=1",
        "/api/projects/default/parsure/export?format=csv&state=accepted",
    ):
        res = client.get(url)
        assert res.status_code == 409, url
        assert res.get_json() == refusal, url
        assert res.mimetype == "application/json", "nothing partial: no CSV body, no attachment"
        assert "attachment" not in (res.headers.get("Content-Disposition") or "")
    # The untampered report still exports on its own; the project export is all-or-nothing.
    assert client.get(f"/api/projects/default/parsure/{rid_other}/export?format=json").status_code == 200
    # No "exported" audit event was written for a refused export.
    exported = [e for e in _events(client) if e["event_type"] == "exported"]
    assert len(exported) == exports_before + 1

    # The dossier (PDF, JSON twin, bundle) is built from the same rows and refuses too.
    with pytest.raises(snap.SnapshotMismatch):
        build_dossier("default")
    for fmt in ("dossier-pdf", "bundle"):
        res = client.get(f"/api/projects/default/export?format={fmt}")
        assert res.status_code == 409, fmt
        assert res.get_json() == refusal, fmt

    # A report saved before stamping existed (no snapshot block) is refused the same way — it cannot prove anything.
    def unstamp(data):
        data.pop("snapshot", None)

    _tamper(rid_other, unstamp)
    res = client.get(f"/api/projects/default/parsure/{rid_other}/export?format=csv")
    assert res.status_code == 409 and res.get_json()["expected"] is None and res.get_json()["actual"]


def test_every_exported_artifact_carries_the_snapshot_hash(client):
    from prompt_matrix.routers.parsure_routes import CSV_COLUMNS, PROJECT_CSV_COLUMNS, PROJECT_CSV_WIDE_FIXED
    from prompt_matrix.services.verification_dossier import build_dossier

    rid = _seed()
    digest = _row_json(rid)["snapshot"]["content_hash"]

    js = client.get(f"/api/projects/default/parsure/{rid}/export?format=json").get_json()
    assert js["snapshot"]["content_hash"] == digest
    rows = list(csv.DictReader(io.StringIO(client.get(f"/api/projects/default/parsure/{rid}/export?format=csv").get_data(as_text=True))))
    assert CSV_COLUMNS[-1] == "snapshot_hash" and rows and all(r["snapshot_hash"] == digest for r in rows)

    long_rows = list(csv.DictReader(io.StringIO(client.get("/api/projects/default/parsure/export?format=csv").get_data(as_text=True))))
    assert "snapshot_hash" in PROJECT_CSV_COLUMNS and long_rows and all(r["snapshot_hash"] == digest and r["document_id"] for r in long_rows)
    wide = list(csv.reader(io.StringIO(client.get("/api/projects/default/parsure/export?format=csv&wide=1").get_data(as_text=True))))
    assert tuple(wide[0][: len(PROJECT_CSV_WIDE_FIXED)]) == PROJECT_CSV_WIDE_FIXED
    assert wide[1][wide[0].index("snapshot_hash")] == digest
    project = client.get("/api/projects/default/parsure/export?format=json").get_json()
    assert project["snapshot"]["algorithm"] == "sha256/canonical-json-v1"
    assert project["snapshot"]["reports"] == [{"report_id": rid, "document_id": "doc-default", "content_hash": digest}]
    assert project["documents"][0]["snapshot"] == {"algorithm": "sha256/canonical-json-v1", "content_hash": digest}

    built = build_dossier("default")
    state = built["state"]
    assert state["intake_snapshots"] == [{"report_id": rid, "document_id": "doc-default", "content_hash": digest}]
    assert state["intake"]["documents"][0]["content_hash"] == digest
    assert digest in built["html"], "the dossier shows the hash of each intake report it was built from"
    assert "Intake report snapshots" in built["html"]
    # The exported event names the hash the file carries.
    exported = [e for e in _events(client, rid) if e["event_type"] == "exported"]
    assert exported and all(e["payload"]["snapshot_hash"] == digest for e in exported)


# --------------------------------------------------------------------------
# document_id: content-derived and stable
# --------------------------------------------------------------------------

def test_document_id_is_content_derived_and_stable_across_uploads(client):
    from prompt_matrix.db import parsure_repository as repo
    from prompt_matrix.history import get_db
    from prompt_matrix.services.v1_orchestrator import run_after_parse

    pdf_bytes = b"%PDF-1.4 the same bytes uploaded twice"
    params = dict(bundle=jdf_cli_bundle(), verification=VERIFICATION, filename="policy.pdf", job_id=None, intake=None)
    first = run_after_parse("default", file_bytes=pdf_bytes, result={"document_id": None, "revision_id": None, "version": None}, **params)
    second = run_after_parse("default", file_bytes=pdf_bytes, result={}, **params)
    expected = "doc-" + hashlib.sha256(pdf_bytes).hexdigest()[:16]
    assert first["report"]["document_id"] == second["report"]["document_id"] == expected
    assert first["report"]["document_id_source"] == "content_hash"
    col = get_db().execute("SELECT document_id FROM parsure_reports WHERE report_id = ?", (first["report_id"],)).fetchone()[0]
    assert col == expected, "persisted into the column list_reports dedupes on"
    assert [r["report_id"] for r in repo.list_reports("default")] == [second["report_id"]], "the same bytes are one document"
    assert len(repo.list_reports("default", current_only=False)) == 2

    # Different bytes → a different id; no bytes → a generated, still non-empty id; the pipeline's id wins when given.
    third = run_after_parse("default", file_bytes=b"other bytes", result={}, **params)
    assert third["report"]["document_id"] != expected and third["report"]["document_id"].startswith("doc-")
    fourth = run_after_parse("default", file_bytes=None, result={}, **params)
    assert fourth["report"]["document_id"].startswith("doc-") and fourth["report"]["document_id_source"] == "generated"
    fifth = run_after_parse("default", file_bytes=pdf_bytes, result=RESULT, **params)
    assert fifth["report"]["document_id"] == "doc-default" and fifth["report"]["document_id_source"] == "ingest"

    # Export rows never carry an empty document id, even for a report saved without one.
    legacy = {"report_id": "pr-legacy", "filename": "legacy.pdf", "classification": {"document_type": "auto_policy"},
              "fields": [{"name": "policy_number", "label": "Policy number", "value": "AP-1", "field_state": "unverified", "routing_action": "manual_review"}],
              "review_summary": {"fields_total": 1, "fields_review": 1, "fields_rejected": 0}}
    repo.save_report("default", legacy)
    rows = list(csv.DictReader(io.StringIO(client.get("/api/projects/default/parsure/export?format=csv").get_data(as_text=True))))
    assert rows and all(r["document_id"] for r in rows)
    legacy_row = next(r for r in rows if r["report_id"] == "pr-legacy")
    assert legacy_row["document_id"] == "doc-" + hashlib.sha256(b"pr-legacy").hexdigest()[:16]
    project = client.get("/api/projects/default/parsure/export?format=json").get_json()
    assert all(d["document_id"] for d in project["documents"])


# --------------------------------------------------------------------------
# Replay: proof, ledger, bounds
# --------------------------------------------------------------------------

def test_replay_writes_a_proof_and_stops_after_two_runs_without_improvement(client):
    from prompt_matrix.services import v1_orchestrator as orch

    rid = _seed()
    before = client.get(f"/api/projects/default/parsure/{rid}").get_json()["report"]
    assert before["replay"]["attempts"] == 0 and before["replay"]["max_attempts"] == 3 and before["replay"]["stop_rule"] is None
    assert before["replay"]["last_proof"] is None and before["replay"]["replayed"] is False and before["replay"]["history"] == []
    stored_hash = before["snapshot"]["content_hash"]

    res = client.post(f"/api/projects/default/parsure/{rid}/replay", json={"actor": "auditor"})
    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    proof = body["proof"]
    assert set(proof) >= {"deterministic", "fields_identical", "fields_total", "changed", "snapshot_before", "snapshot_after", "compared", "at"}
    assert proof["compared"] == ["name", "value", "element_id", "field_state"]
    assert proof["fields_total"] == len(before["fields"]) and proof["fields_identical"] + len(proof["changed"]) == proof["fields_total"]
    assert proof["deterministic"] is True and proof["changed"] == [], "the same text read by the same policy is the same contract"
    assert proof["snapshot_before"] == stored_hash and proof["snapshot_after"]
    replay = body["report"]["replay"]
    assert replay["replayed"] is True and replay["last_proof"] == proof and replay["attempts"] == 1 and replay["max_attempts"] == 3
    entry = replay["history"][0]
    assert set(entry) >= {"at", "trigger", "policy_version", "node_id_policy", "redhat_policy", "fields_found_before", "fields_found_after",
                          "improved", "snapshot_before", "snapshot_after"}
    # additive since 2026-09-28 (plan V4): what changed, what the remap mapped, and that no model ran on the web tier
    assert set(entry) - {"at", "trigger", "policy_version", "node_id_policy", "redhat_policy", "fields_found_before", "fields_found_after",
                         "improved", "snapshot_before", "snapshot_after"} <= {"fields_changed", "mapping_changed", "grounded", "grounded_reason"}
    assert entry["grounded"] is False and entry["fields_changed"] == []
    assert entry["trigger"] == "replay" and entry["policy_version"] == orch.POLICY_VERSION and entry["node_id_policy"] == "eid-v1"
    assert entry["redhat_policy"] == "rh-graph-v1" and entry["improved"] is False and entry["fields_found_before"] == entry["fields_found_after"]
    assert entry["snapshot_before"] == stored_hash and entry["snapshot_after"] == proof["snapshot_after"]
    assert replay["stop_rule"] is None, "one run without improvement is not yet the rule"
    events = _events(client, rid)
    assert events[0]["event_type"] == "replayed" and events[0]["actor"] == "auditor" and events[0]["payload"]["proof"]["deterministic"] is True
    # The persisted report is re-stamped and intact; its hash includes the ledger entry.
    detail = client.get(f"/api/projects/default/parsure/{rid}").get_json()
    assert detail["integrity"]["ok"] is True and detail["report"]["snapshot"]["content_hash"] != stored_hash

    # Second run without improvement → the stop rule is set; the third is refused, nothing changes.
    second = client.post(f"/api/projects/default/parsure/{rid}/replay", json={})
    assert second.status_code == 200 and second.get_json()["report"]["replay"]["attempts"] == 2
    assert second.get_json()["report"]["replay"]["stop_rule"].startswith("no_improvement")
    third = client.post(f"/api/projects/default/parsure/{rid}/replay", json={})
    assert third.status_code == 409
    refused = third.get_json()
    assert refused["ok"] is False and "no_improvement" in refused["error"] and "did not raise fields_found" in refused["error"]
    assert refused["replay"]["attempts"] == 2
    after = client.get(f"/api/projects/default/parsure/{rid}").get_json()["report"]["replay"]
    assert after["attempts"] == 2 and len(after["history"]) == 2, "a refused rerun leaves no entry"
    assert [e["event_type"] for e in _events(client, rid)].count("replayed") == 2


def test_rerun_ceiling_counts_overrides_and_refuses_the_fourth(client):
    rid = _seed()
    # Override to a type whose fields are not on the page (fewer found), back (more found), away again (fewer).
    for doc_type, improved in (("auto_claim", False), ("auto_policy", True), ("property_policy", False)):
        res = client.post(f"/api/projects/default/parsure/{rid}/classification", json={"document_type": doc_type, "actor": "ana"})
        assert res.status_code == 200, res.get_json()
        entry = res.get_json()["report"]["replay"]["history"][-1]
        assert entry["trigger"] == "classification_override" and entry["improved"] is improved
    replay = client.get(f"/api/projects/default/parsure/{rid}").get_json()["report"]["replay"]
    assert replay["attempts"] == 3 and replay["stop_rule"].startswith("max_attempts")
    assert [h["trigger"] for h in replay["history"]] == ["classification_override"] * 3, "append-only, in order"
    assert all(h["snapshot_before"] and h["snapshot_after"] for h in replay["history"])
    assert replay["history"][1]["snapshot_before"] != replay["history"][0]["snapshot_before"]

    refused = client.post(f"/api/projects/default/parsure/{rid}/replay", json={})
    assert refused.status_code == 409 and "max_attempts" in refused.get_json()["error"] and "3 of 3" in refused.get_json()["error"]
    fourth = client.post(f"/api/projects/default/parsure/{rid}/classification", json={"document_type": "auto_policy"})
    assert fourth.status_code == 409 and "max_attempts" in fourth.get_json()["error"]
    assert client.get(f"/api/projects/default/parsure/{rid}").get_json()["report"]["classification"]["document_type"] == "property_policy"
    events = [e["event_type"] for e in _events(client, rid)]
    assert events.count("classification_overridden") == 3 and "replayed" not in events


def test_replay_without_stored_page_text_is_refused(client):
    from prompt_matrix.db import parsure_repository as repo

    rid = _seed()
    report = repo.get_report("default", rid)
    report.pop("_page_texts", None)
    repo.update_report("default", rid, report)
    res = client.post(f"/api/projects/default/parsure/{rid}/replay", json={})
    assert res.status_code == 409 and "No stored page text" in res.get_json()["error"]
    assert client.post("/api/projects/default/parsure/pr-nope/replay", json={}).status_code == 404


def test_rerun_stop_rule_reads_the_history_not_the_counter():
    from prompt_matrix.services import v1_orchestrator as orch

    assert orch.rerun_stop_rule(None) is None
    assert orch.rerun_stop_rule({"attempts": 0, "history": [{"trigger": "replay", "improved": False}] * 3}).startswith("max_attempts")
    assert orch.rerun_stop_rule({"attempts": 2, "history": [{"trigger": "replay", "improved": False}, {"trigger": "classification_override", "improved": True}]}) is None
    assert orch.rerun_stop_rule({"history": [{"trigger": "replay", "improved": True}, {"trigger": "replay", "improved": False}, {"trigger": "replay", "improved": False}]}).startswith("max_attempts")
    assert orch.rerun_stop_rule({"history": [{"trigger": "classification_override", "improved": False}, {"trigger": "replay", "improved": False}]}).startswith("no_improvement")
    with pytest.raises(ValueError):
        orch.record_rerun({}, trigger="cron", before_found=0, after_found=0, snapshot_before=None, snapshot_after=None)


# --------------------------------------------------------------------------
# Red-Hat conflict recall: insured_name and claimant_name are one party key
# --------------------------------------------------------------------------

def test_claimant_and_insured_names_are_compared_as_one_party_key():
    from prompt_matrix.services import field_extractor as fx

    assert fx.PARTY_NAME_FIELDS == ("insured_name", "claimant_name")
    policy = {"report_id": "pr-policy", "document_id": "doc-p", "fields": [{"name": "insured_name", "value": "John Q. Sample"}]}
    claim = {"report_id": "pr-claim", "document_id": "doc-c", "fields": [{"name": "claimant_name", "value": "Jane Doe"}]}
    conflicts = fx.cross_document_conflicts([policy, claim])
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c["field"] == "insured_name" and c["fields"] == ["claimant_name", "insured_name"] and c["kind"] == "cross_document"
    assert c["report_ids"] == ["pr-claim", "pr-policy"]
    assert c["values"] == [
        {"report_id": "pr-policy", "document_id": "doc-p", "field": "insured_name", "value": "John Q. Sample"},
        {"report_id": "pr-claim", "document_id": "doc-c", "field": "claimant_name", "value": "Jane Doe"},
    ]
    # The same person under both names is not a conflict; a schema-mismatch value is not compared.
    same = {"report_id": "pr-claim", "fields": [{"name": "claimant_name", "value": "JOHN  Q. SAMPLE"}]}
    assert fx.cross_document_conflicts([policy, same]) == []
    mismatch = {"report_id": "pr-claim", "fields": [{"name": "claimant_name", "value": "Jane Doe", "evidence_state": "schema_mismatch"}]}
    assert fx.cross_document_conflicts([policy, mismatch]) == []
    # A single-name conflict keeps the one field name.
    other = {"report_id": "pr-2", "fields": [{"name": "insured_name", "value": "Someone Else"}]}
    only = fx.cross_document_conflicts([policy, other])[0]
    assert only["field"] == "insured_name" and only["fields"] == ["insured_name"]


def test_party_name_conflict_reaches_the_saved_report_and_the_dossier(client):
    from prompt_matrix.db import parsure_repository as repo
    from prompt_matrix.services import v1_orchestrator as orch
    from prompt_matrix.services.verification_dossier import build_dossier

    rid = _seed()
    claim = {"report_id": "pr-claim", "document_id": "doc-claim", "filename": "claim.pdf", "classification": {"document_type": "auto_claim"},
             "fields": [{"name": "claimant_name", "label": "Claimant", "value": "Jane Doe", "field_state": "unverified", "routing_action": "manual_review"}],
             "review_summary": {"fields_total": 1, "fields_review": 1, "fields_rejected": 0}, "conflicts": []}
    orch.attach_conflicts("default", claim)
    assert [c["field"] for c in claim["conflicts"]] == ["insured_name"] and claim["conflicts"][0]["fields"] == ["claimant_name", "insured_name"]
    assert set(claim["conflicts"][0]["report_ids"]) == {rid, "pr-claim"}
    repo.save_report("default", claim)
    state = build_dossier("default")["state"]
    assert state["sections"]["conflicts"]["count"] >= 1
    assert any(c["field"] == "insured_name" for c in state["sections"]["conflicts"]["items"])
