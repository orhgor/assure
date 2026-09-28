#!/usr/bin/env python3
"""Run a folder of real documents through both sides of the running stack and
print what the customer will see — no assertions, no fixtures (2026-09-27).

    .venv/bin/python scripts/demo_check.py demo/ --email … --password …
    .venv/bin/python scripts/demo_check.py demo/ --base http://HOST --gate-key <SHELL_ACCESS_KEY>
    .venv/bin/python scripts/demo_check.py demo/ --skip-assure          # Parsure only

Per file: Parsure (type, confidence, flags, fields found / review / not found /
suspect, discovered fields, model pass, Red-Hat findings) and Assure (compile
refusal reason or claim_summary with every claim's verdict and reason). Nothing
here is inferred from defaults — every line is read from the API.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

import requests

ASK = ("Summarise this document in short sentences, one fact per sentence. State every figure, date and exclusion "
       "exactly as the document does; do not add anything the document does not say.")


def sse(resp):
    events: dict[str, list] = {}; ev = "message"
    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            continue
        if line.startswith("event:"):
            ev = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            raw = line[5:].strip()
            try:
                events.setdefault(ev, []).append(json.loads(raw))
            except Exception:
                events.setdefault(ev, []).append(raw)
    return events


def walk(nodes):
    for n in nodes or []:
        if isinstance(n, dict):
            yield n
            yield from walk(n.get("children") or [])


def wait_task(http, base, task_id, limit=600):
    t0 = time.time(); status = None
    while task_id and time.time() - t0 < limit:
        t = http.get(f"{base}/api/tasks/{task_id}", timeout=10).json(); status = t.get("status")
        if status in ("success", "failed"):
            return status, t.get("error")
        time.sleep(3)
    return status, "timeout"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--base", default=os.environ.get("ASSURE_BASE_URL", "http://127.0.0.1:8765"))
    ap.add_argument("--email", default=os.environ.get("ASSURE_RUN_EMAIL"))
    ap.add_argument("--password", default=os.environ.get("ASSURE_RUN_PASSWORD"))
    ap.add_argument("--gate-key", default=os.environ.get("SHELL_ACCESS_KEY"))
    ap.add_argument("--skip-assure", action="store_true")
    ap.add_argument("--ask", default=ASK)
    args = ap.parse_args()
    base = args.base.rstrip("/")
    http = requests.Session()
    if args.gate_key:
        http.post(f"{base}/auth", data={"key": args.gate_key}, allow_redirects=False, timeout=10)
    if http.get(f"{base}/api/auth/setup-status", timeout=10).json().get("mode") == "local":
        r = http.post(f"{base}/api/auth/login", json={"email": args.email, "password": args.password}, timeout=10)
        if r.status_code != 200:
            print("sign-in failed:", r.status_code, r.text[:200]); return 2
    models = ((http.get(f"{base}/health", timeout=10).json().get("checks") or {}).get("models") or {})
    print(f"stack: backend {models.get('backend')} · stages {models.get('stages')}\n")
    files = sorted(glob.glob(os.path.join(args.folder, "*.pdf")) + glob.glob(os.path.join(args.folder, "*.png")) + glob.glob(os.path.join(args.folder, "*.jpg")) + glob.glob(os.path.join(args.folder, "*.jpeg")) + glob.glob(os.path.join(args.folder, "*.tif")) + glob.glob(os.path.join(args.folder, "*.tiff")))
    if not files:
        print("no documents in", args.folder); return 2
    for path in files:
        name = os.path.basename(path)
        pid = http.post(f"{base}/api/projects", json={"title": f"demo · {name[:40]}"}, timeout=10).json()["id"]
        print(f"=== {name}  (project {pid})")
        # --- Parsure ---
        t0 = time.time()
        with open(path, "rb") as fh:
            up = http.post(f"{base}/api/projects/{pid}/import-pdf", files={"file": (name, fh, "application/octet-stream")}, timeout=120)
        if up.status_code != 202:
            print(f"  parsure: upload {up.status_code} {up.text[:160]}")
        else:
            status, err = wait_task(http, base, up.json().get("task_id"))
            rep = http.get(f"{base}/api/projects/{pid}/parsure/latest", timeout=10).json(); rep = rep.get("report") or rep
            if not rep.get("fields") and not rep.get("classification"):
                print(f"  parsure: task {status} ({err}) — no report")
            else:
                rs = rep.get("review_summary") or {}; c = rep.get("classification") or {}; ex = rep.get("execution") or {}
                print(f"  parsure: {time.time()-t0:.0f}s · {rep.get('page_count')} p · {rep.get('material_type')}/{rep.get('modality')} · quality {rep.get('document_quality_score')} · flags {rep.get('quality_flags')}")
                print(f"    type {c.get('document_type')} (conf {c.get('confidence')}, family {(c.get('family') or {}).get('family')}, mismatch {c.get('schema_mismatch')})"
                      + (f" · uncertainty {(c.get('uncertainty') or {}).get('reason_codes')}" if c.get("uncertainty") else ""))
                print(f"    fields {rs.get('fields_found')}/{rs.get('fields_total')} · review {rs.get('fields_review')} · not found {rs.get('fields_not_found')} · suspect {rs.get('fields_suspect')} · accepted {rs.get('fields_accepted')} · discovered {len(rep.get('discovered_fields') or [])}")
                print(f"    model pass {(ex.get('llm_grounding') or {}).get('status')} ({(ex.get('llm_grounding') or {}).get('fields_grounded')} grounded, {(ex.get('llm_grounding') or {}).get('model')}) · Red-Hat {(ex.get('redhat_graph') or {}).get('findings')} findings · targeted {(ex.get('redhat_targeted') or {}).get('status')} · tables {(ex.get('tables') or {}).get('tables')} · vision {(ex.get('vision') or {}).get('status')}")
                for f in rep.get("fields") or []:
                    if f.get("value") is not None:
                        print(f"    ✓ {f['name']}: {str(f.get('value'))[:40]!r}  [{f.get('extraction_method')}, {f.get('field_state')}]")
                    elif f.get("evidence_state") == "found_suspect":
                        print(f"    ? {f['name']}: raw {str(f.get('raw'))[:30]!r} → {(f.get('value_quality') or {}).get('quality')} ({(f.get('value_quality') or {}).get('basis')})")
                for n in (rep.get("extraction_notes") or [])[:3]:
                    print(f"    note: {n[:140]}")
                print(f"    record: {base}/parsing/{rep.get('report_id')}")
        if args.skip_assure:
            print(); continue
        # --- Assure ---
        t0 = time.time()
        with open(path, "rb") as fh:
            up = http.post(f"{base}/api/projects/{pid}/substrate/upload", files={"file": (name, fh, "application/octet-stream")}, timeout=120)
        if up.status_code != 202:
            print(f"  assure: source upload {up.status_code} {up.text[:160]}\n"); continue
        wait_task(http, base, up.json().get("task_id"))
        rows = http.get(f"{base}/api/projects/{pid}/substrate", timeout=10).json().get("files") or []
        if not rows:
            print("  assure: no source row\n"); continue
        with http.post(f"{base}/api/projects/{pid}/draft/stream", json={"intent": args.ask, "source_ids": [rows[0]["id"]], "compileType": "full", "force": True}, stream=True, timeout=900) as r:
            ev = sse(r)
        err = next((p for p in ev.get("error") or [] if isinstance(p, dict)), None)
        ver = next((p for p in reversed(ev.get("verified") or []) if isinstance(p, dict)), None)
        usage = next((p for p in ev.get("usage") or [] if isinstance(p, dict) and p.get("task_type") == "draft_compile"), {})
        if err or not ver:
            print(f"  assure: {time.time()-t0:.0f}s · refused — {(err or {}).get('reason')}: {(err or {}).get('error', '')[:200]}"
                  + (f" · field report {(err or {}).get('parsure_url')}" if isinstance(err, dict) and err.get("parsure_url") else "") + "\n")
            continue
        doc = ver.get("document") or {}; summ = (doc.get("meta") or {}).get("claim_summary") or {}
        print(f"  assure: {time.time()-t0:.0f}s · model {usage.get('model_id')} · gate {ver.get('gate_status')} · z3 {ver.get('z3_status')} · claims {summ.get('verified')}/{summ.get('total')} verified · {summ.get('unsupported')} unsupported · {summ.get('contradicted')} contradicted · {summ.get('insufficient')} insufficient · {summ.get('flagged')} flagged"
              + (f" · {summ.get('meta')} notes" if summ.get("meta") is not None else ""))
        for n in walk(doc.get("body") or []):
            c = ((n.get("meta") or {}).get("provenance") or {}).get("claim") if n.get("type") == "paragraph" else None
            if not c:
                continue
            kind = (c.get("checks") or {}).get("kind")
            subs = (c.get("checks") or {}).get("sub_claims") or []
            print(f"    [{(c.get('verdict') or 'note')[:12]:<12}] {(n.get('content') or '').strip()[:80]!r} — {(c.get('reason') or '')[:90]}" + (f" ({len(subs)} sentences)" if subs else "") + (f" [{kind}]" if kind and kind != "fact" else ""))
            for sc in subs[:6]:
                print(f"         · {(sc.get('verdict') or '')[:12]:<12} {(sc.get('text') or '')[:70]!r}" + (f" ← {sc.get('quote')[:50]!r}" if sc.get("quote") else ""))
        rh = (ev.get("redhat") or [{}])[-1]
        print(f"    Red-Hat: {rh.get('status')} {rh.get('task_id') or rh.get('skip_reason') or ''}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
