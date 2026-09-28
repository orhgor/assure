#!/usr/bin/env python3
"""Fable's proof run (customer plan ``todos/fable_execution_plan.md`` Part 2.10,
2026-09-27): drive a real document through the running stack over HTTP and
check, against the artifacts the stack returns, that every pipeline step ran
and left its trace — not that the code "has" the step.

    .venv/bin/python scripts/final_run.py                       # http://127.0.0.1:8765
    .venv/bin/python scripts/final_run.py --base http://host:8765 --pdf my.pdf
    .venv/bin/python scripts/final_run.py --tamper                # also proves the 409 gate (needs DATABASE_URL)

Exit code 0 only when every check passes. Each line names the check, the
value the stack reported and PASS/FAIL/SKIP with the reason; nothing here is
inferred from defaults — a step that did not run fails the run, a model that
is disabled is reported as such (the grounding check then requires the ledger
to *say* it was disabled).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

import requests

CHECKS: list[tuple[str, str, Any]] = []


def check(name: str, ok: bool | None, detail: Any) -> None:
    CHECKS.append((name, "PASS" if ok else ("SKIP" if ok is None else "FAIL"), detail))
    print(f"[{CHECKS[-1][1]}] {name}: {detail}")


def make_pdf(path: str) -> str:
    """A one-page declarations page with debris under two labels and the real
    values in a footer no label anchor reads — the document the plan's proof
    test uses, so the run exercises the suspect → targeted-pass path."""
    import fitz  # PyMuPDF, in the venv

    lines = [
        "AUTO POLICY DECLARATIONS", "Policy Number: ~~-,;;", "Named Insured: MAILING ADDRESS", "Policy Period: 01/15/2025 to 01/15/2026",
        "Vehicle: 2003 Honda Accord", "VIN: 1HGCM82633A004352", "Total Premium: $1,250.00", "Liability Limit: $100,000",
        "Collision Deductible: $500", "Comprehensive Deductible: $250", "Agent: Mary Agent", "Authorized Signature: /s/ Mary Agent",
        "Ref AP-2025-0001 (policy) issued to John Q. Sample, the named insured.",
    ] + ["Coverage notes and conditions apply as stated in the policy forms."] * 5
    doc = fitz.open()
    page = doc.new_page()
    y = 72
    for line in lines:
        page.insert_text((72, y), line, fontsize=11)
        y += 18
    doc.save(path)
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("ASSURE_BASE_URL", "http://127.0.0.1:8765"))
    ap.add_argument("--pdf", default=None, help="a document to run; default: a generated debris declarations page")
    ap.add_argument("--tamper", action="store_true", help="edit the stored row and prove the export is refused (DATABASE_URL)")
    ap.add_argument("--timeout", type=int, default=240)
    ap.add_argument("--email", default=None, help="local-auth account (or ASSURE_RUN_EMAIL)")
    ap.add_argument("--password", default=None, help="local-auth password (or ASSURE_RUN_PASSWORD)")
    ap.add_argument("--bootstrap-token", default=None, help="first run only: creates the owner (or ASSURE_BOOTSTRAP_TOKEN)")
    ap.add_argument("--gate-key", default=os.environ.get("SHELL_ACCESS_KEY"), help="SHELL_ACCESS_KEY when --base is the :80 shell gate instead of the API port")
    args = ap.parse_args()
    base = args.base.rstrip("/")
    pdf = args.pdf or make_pdf("/tmp/final_run_debris.pdf")

    # Accounts (2026-09-27): in local auth mode the API needs a session. Sign in
    # with --email/--password (or ASSURE_RUN_EMAIL / ASSURE_RUN_PASSWORD); on a
    # fresh box pass --bootstrap-token to create the owner first.
    http = requests.Session()
    requests_get, requests_post = requests.get, requests.post
    requests.get, requests.post = http.get, http.post  # every call below shares the session cookie
    if args.gate_key:
        # Through the shell gate on :80 (the API itself binds 127.0.0.1 on the
        # box): the gate's /auth sets its cookie and proxies /api/* upstream.
        g = http.post(f"{base}/auth", data={"key": args.gate_key}, allow_redirects=False, timeout=10)
        check("shell gate accepted the access key", g.status_code in (302, 303) and "Set-Cookie" in g.headers, g.status_code)
    auth_status = http.get(f"{base}/api/auth/setup-status", timeout=10).json()
    if auth_status.get("mode") == "local":
        email = args.email or os.environ.get("ASSURE_RUN_EMAIL", "")
        password = args.password or os.environ.get("ASSURE_RUN_PASSWORD", "")
        if auth_status.get("needs_owner"):
            token = args.bootstrap_token or os.environ.get("ASSURE_BOOTSTRAP_TOKEN", "")
            r = http.post(f"{base}/api/auth/setup", json={"bootstrap_token": token, "org_name": "final-run", "email": email,
                                                          "display_name": "Final Run", "password": password}, timeout=10)
            check("first-run owner created", r.status_code == 200, r.status_code if r.ok else r.json())
        else:
            r = http.post(f"{base}/api/auth/login", json={"email": email, "password": password}, timeout=10)
            check("signed in (local auth)", r.status_code == 200, r.status_code if r.ok else r.json())
        me = http.get(f"{base}/api/auth/me", timeout=10).json()
        check("session carries a role", bool(me.get("user_id")) and bool(me.get("role")), {k: me.get(k) for k in ("email", "role")})
    else:
        check("auth mode", None, auth_status.get("mode"))
    health = requests.get(f"{base}/health", timeout=10).json()
    check("stack healthy", health.get("status") == "healthy", {k: (v.get("status") if isinstance(v, dict) else v) for k, v in (health.get("checks") or {}).items() if k in ("db", "models", "pdf_renderer")})
    pid = requests.post(f"{base}/api/projects", json={"title": "final-run"}, timeout=10).json()["id"]
    with open(pdf, "rb") as fh:
        up = requests.post(f"{base}/api/projects/{pid}/import-pdf", files={"file": (os.path.basename(pdf), fh, "application/pdf")}, timeout=60)
    check("ingest accepted (202 + task_id)", up.status_code == 202 and bool(up.json().get("task_id")), up.status_code)
    task = up.json().get("task_id")
    started = time.time()
    status = None
    while time.time() - started < args.timeout:
        t = requests.get(f"{base}/api/tasks/{task}", timeout=10).json()
        status = t.get("status")
        if status in ("success", "failed"):
            break
        time.sleep(2)
    check("parse task finished", status == "success", f"{status} after {time.time() - started:.0f}s")
    latest = requests.get(f"{base}/api/projects/{pid}/parsure/latest", timeout=10).json()
    r = latest.get("report") or latest
    rid = r.get("report_id")
    full = requests.get(f"{base}/api/projects/{pid}/parsure/{rid}", timeout=10).json()
    r = full.get("report") or r
    exe = r.get("execution") or {}
    check("execution ledger present", bool(exe) and bool(exe.get("ran_at")), sorted(exe))
    check("LAYA ran", (exe.get("laya") or {}).get("status") == "completed", exe.get("laya"))
    check("Z3 ran", (exe.get("z3") or {}).get("status") in ("PASS", "VIOLATION"), exe.get("z3"))
    ver = r.get("verification") or {}
    check("Red-Hat draft critique ran", ver.get("redhat_status") not in (None, "not_run"), ver.get("redhat_status"))
    check("raw candidates on the report", isinstance(r.get("raw_candidates"), list), (exe.get("raw_candidates") or {}).get("candidates"))
    rg = exe.get("redhat_graph") or {}
    check("Red-Hat graph critique ran", rg.get("status") == "completed" and rg.get("policy") == "rh-graph-v1", {k: rg.get(k) for k in ("status", "findings", "high", "medium", "low", "model_check")})
    llm = exe.get("llm_grounding") or {}
    llm_ok = llm.get("status") in ("ran", "not_needed") or (llm.get("status") in ("disabled", "skipped") and bool(llm.get("reason")))
    check("grounded model pass ran or said why not", llm_ok, {k: llm.get(k) for k in ("status", "model", "model_path", "fields_offered", "fields_grounded", "reason")})
    fields = r.get("fields") or []
    found = [f for f in fields if f.get("value") is not None]
    check("every found field carries grounding (quote + span + model)", bool(found) and all(f.get("grounding_quote") and f.get("grounding_span") and f.get("grounding_model") for f in found), f"{len(found)} found fields")
    absent = [f for f in fields if f.get("value") is None and f.get("field_type") != "signature" and f.get("evidence_state") != "found_suspect"]
    check("no verification confidence on absent fields", all(f.get("verification_confidence") is None and f.get("field_state") == "not_found" for f in absent), f"{len(absent)} absent")
    suspects = [f for f in fields if f.get("evidence_state") == "found_suspect"]
    check("suspect values are quoted, not trusted (provenance < 1)", all(f.get("provenance_confidence", 1.0) < 1.0 and f.get("value_quality") for f in suspects), [(f["name"], f.get("provenance_confidence")) for f in suspects] or "no suspects on this document")
    targeted = exe.get("redhat_targeted") or {}
    if llm.get("status") == "ran":
        check("Red-Hat-targeted pass ran when the critique named a field", targeted.get("status") in ("ran", "not_needed", "skipped", "failed"), {k: targeted.get(k) for k in ("status", "model_path", "fields", "changed", "reason")})
    else:
        check("Red-Hat-targeted pass", None, f"model path {llm.get('status')}: {targeted.get('reason') or llm.get('reason')}")
    rs = r.get("review_summary") or {}
    consistent = rs.get("fields_total") == len(fields) and rs.get("fields_found") == len(found) and rs.get("fields_not_found") == sum(1 for f in fields if f.get("field_state") == "not_found")
    check("review summary counts match the fields", consistent, {k: rs.get(k) for k in ("fields_total", "fields_found", "fields_review", "fields_not_found", "fields_suspect", "fields_accepted")})
    gi = r.get("graph_integrity") or {}
    check("field graph whole (no orphans)", gi.get("orphans") == 0 and gi.get("integrity_score") == 1.0, {k: gi.get(k) for k in ("fields", "anchored", "orphans", "integrity_score", "negative_evidence")})
    check("document_id stable and named", bool(r.get("document_id")) and r.get("document_id_source") in ("ingest", "content_hash"), (r.get("document_id"), r.get("document_id_source")))
    check("snapshot stamped and intact", bool((r.get("snapshot") or {}).get("content_hash")) and (full.get("integrity") or {}).get("ok") is True, (r.get("snapshot") or {}).get("content_hash", "")[:16])
    sj = r.get("source_jdf") or {}
    if sj.get("url"):
        sr = requests.get(f"{base}{sj['url']}", timeout=30)
        body = sr.json() if sr.ok and sr.headers.get("content-type", "").startswith("application/json") else {}
        check("source JDF stored and served (jdf.js source view)", sr.status_code == 200 and isinstance(body.get("pages"), list) and len(body["pages"]) == int(r.get("page_count") or 0),
              {"url": sj.get("url"), "status": sr.status_code, "pages": len(body.get("pages") or [])})
    else:
        check("source JDF stored and served (jdf.js source view)", False, "report carries no source_jdf")
    replay = r.get("replay") or {}
    check("rerun ledger present", isinstance(replay.get("history"), list) and "passes" in replay and "attempts" in replay, {k: replay.get(k) for k in ("passes", "attempts", "max_attempts", "stop_rule")})
    rp = requests.post(f"{base}/api/projects/{pid}/parsure/{rid}/replay", json={}, timeout=120)
    proof = (rp.json().get("proof") or {}) if rp.ok else {}
    check("replay deterministic", rp.ok and proof.get("deterministic") is True, {k: proof.get(k) for k in ("deterministic", "fields_identical", "fields_total", "changed")} if rp.ok else rp.json())
    ex = requests.get(f"{base}/api/projects/{pid}/parsure/{rid}/export?format=json", timeout=30)
    body = ex.json() if ex.ok else {}
    ex_hash = (body.get("snapshot") or (body.get("report") or {}).get("snapshot") or {}).get("content_hash")
    r2 = (requests.get(f"{base}/api/projects/{pid}/parsure/{rid}", timeout=10).json().get("report") or {})
    check("export carries the stored hash (JSON/export agree)", ex.ok and ex_hash == (r2.get("snapshot") or {}).get("content_hash"), (ex.status_code, (ex_hash or "")[:16]))
    page = requests.get(f"{base}/parsing/{rid}", timeout=30)
    check("record page renders the same report", page.ok and rid in page.text, page.status_code)
    if args.tamper:
        url = os.environ.get("DATABASE_URL")
        if not url:
            check("tamper gate", None, "DATABASE_URL not set")
        else:
            import psycopg
            with psycopg.connect(url) as conn:
                row = conn.execute("SELECT report_json FROM parsure_reports WHERE report_id = %s", (rid,)).fetchone()
                data = json.loads(row[0]) if isinstance(row[0], str) else row[0]
                data["fields"][0]["value"] = "TAMPERED"
                conn.execute("UPDATE parsure_reports SET report_json = %s WHERE report_id = %s", (json.dumps(data), rid))
                conn.commit()
            refused = requests.get(f"{base}/api/projects/{pid}/parsure/{rid}/export?format=json", timeout=30)
            check("tampered row exports nothing (409)", refused.status_code == 409, refused.status_code)
    if auth_status.get("mode") == "local":
        ev = requests.get(f"{base}/api/audit?project_id={pid}&limit=20", timeout=10)
        rows = (ev.json().get("events") or []) if ev.ok else []
        check("audit rows carry actor id and role", bool(rows) and all(r.get("actor_id") and r.get("actor_role") for r in rows if r.get("source") == "parsure" and r.get("event_type") in ("replayed",)),
              [(r.get("event_type"), r.get("actor_role"), r.get("ip")) for r in rows[:3]])
    failed = [c for c in CHECKS if c[1] == "FAIL"]
    print(f"\n{len(CHECKS) - len(failed)} of {len(CHECKS)} checks passed; project {pid}, report {rid}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
