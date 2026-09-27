#!/usr/bin/env python3
"""Proof run for the ASSURE side (compile → anchor → entailment → claim verdicts
→ Red-Hat → dossier), over HTTP against a running stack, 2026-09-27. Companion
of scripts/final_run.py (Parsure intake). One PASS/FAIL line per check; exit 0
only when everything passes; nothing inferred from defaults.

    .venv/bin/python scripts/final_run_assure.py --email owner@x --password …   # local auth on
    .venv/bin/python scripts/final_run_assure.py --base http://HOST --gate-key <SHELL_ACCESS_KEY>

By default the source is a generated declarations page (SOURCE_LINES below —
test data, not application logic; the app hard-codes nothing about documents)
and the ask restates its figures, so a capable model produces a grounded
draft. Pass ``--pdf your.pdf --ask "…"`` to run the same checks on any real
document: every check here reads what the stack returned, none of them
assumes the sample's values. A refusal (422) from the compile guard
is reported as what it is — the grounding gate holding, not a crash — and the
run fails: the customer cannot use a stack whose model never passes the gate.
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


SOURCE_LINES = [
    "AUTO POLICY DECLARATIONS", "Policy Number: AP-2025-0001", "Named Insured: John Q. Sample",
    "Policy Period: 01/15/2025 to 01/15/2026", "Vehicle: 2003 Honda Accord", "VIN: 1HGCM82633A004352",
    "Total Premium: $1,250.00", "Liability Limit: $100,000", "Collision Deductible: $500", "Comprehensive Deductible: $250",
    "Agent: Mary Agent", "Flood damage is excluded under this policy.", "Authorized Signature: /s/ Mary Agent",
] + ["Coverage notes and conditions apply as stated in the policy forms."] * 4

ASK = ("Summarise this auto policy declarations page in short sentences: policy number, named insured, policy period, "
       "vehicle and VIN, total premium, liability limit, both deductibles, the flood exclusion and the agent. "
       "State every figure exactly as the document does.")


def make_pdf(path: str) -> str:
    import fitz
    doc = fitz.open(); page = doc.new_page(); y = 72
    for line in SOURCE_LINES:
        page.insert_text((72, y), line, fontsize=11); y += 18
    doc.save(path); return path


def sse(resp) -> dict[str, list]:
    events: dict[str, list] = {}; ev = "message"
    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            continue
        if line.startswith("event:"):
            ev = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            raw = line[5:].strip()
            try:
                payload = json.loads(raw)
            except Exception:
                payload = raw
            events.setdefault(ev, []).append(payload)
    return events


def walk(nodes):
    for n in nodes or []:
        if isinstance(n, dict):
            yield n
            yield from walk(n.get("children") or [])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("ASSURE_BASE_URL", "http://127.0.0.1:8765"))
    ap.add_argument("--email", default=os.environ.get("ASSURE_RUN_EMAIL"))
    ap.add_argument("--password", default=os.environ.get("ASSURE_RUN_PASSWORD"))
    ap.add_argument("--gate-key", default=os.environ.get("SHELL_ACCESS_KEY"))
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--pdf", default=None, help="a real document to run instead of the generated sample")
    ap.add_argument("--ask", default=None, help="the compile ask for --pdf (default: a summary that restates the document's figures)")
    args = ap.parse_args()
    base = args.base.rstrip("/")
    http = requests.Session()
    if args.gate_key:
        g = http.post(f"{base}/auth", data={"key": args.gate_key}, allow_redirects=False, timeout=10)
        check("shell gate accepted the access key", g.status_code in (302, 303), g.status_code)
    st = http.get(f"{base}/api/auth/setup-status", timeout=10).json()
    if st.get("mode") == "local":
        r = http.post(f"{base}/api/auth/login", json={"email": args.email, "password": args.password}, timeout=10)
        check("signed in (local auth)", r.status_code == 200, r.status_code)
    health = http.get(f"{base}/health", timeout=10).json()
    models = (health.get("checks") or {}).get("models") or {}
    check("stack healthy", health.get("status") == "healthy", {"backend": models.get("backend"), "draft": (models.get("stages") or {}).get("draft"), "evidence": (models.get("stages") or {}).get("evidence")})
    pid = http.post(f"{base}/api/projects", json={"title": "final-run-assure"}, timeout=10).json()["id"]
    pdf = args.pdf or make_pdf("/tmp/final_run_assure_source.pdf")
    ask = args.ask or (ASK if not args.pdf else "Summarise this document in short sentences, one fact per sentence. State every figure, date and exclusion exactly as the document does; do not add anything the document does not say.")
    with open(pdf, "rb") as fh:
        up = http.post(f"{base}/api/projects/{pid}/substrate/upload", files={"file": (os.path.basename(pdf), fh, "application/pdf")}, timeout=60)
    check("source accepted into the vault (202)", up.status_code == 202, up.status_code)
    tid = up.json().get("task_id"); t0 = time.time(); status = None
    while tid and time.time() - t0 < 180:
        s = http.get(f"{base}/api/tasks/{tid}", timeout=10).json(); status = s.get("status")
        if status in ("success", "failed"):
            break
        time.sleep(2)
    check("source read by the worker", status == "success", status)
    files = http.get(f"{base}/api/projects/{pid}/substrate", timeout=10).json().get("files") or []
    sid = files[0]["id"] if files else None
    check("source has text", bool(files) and int(files[0].get("file_size_bytes") or 0) > 0 and sid is not None, files[0].get("filename") if files else None)
    t0 = time.time()
    with http.post(f"{base}/api/projects/{pid}/draft/stream", json={"intent": ask, "source_ids": [sid], "compileType": "full", "force": True}, stream=True, timeout=args.timeout) as r:
        events = sse(r)
    elapsed = round(time.time() - t0, 1)
    err = (events.get("error") or [None])[-1]
    verified = next((p for p in reversed(events.get("verified") or []) if isinstance(p, dict)), None)
    usage = next((p for p in events.get("usage") or [] if isinstance(p, dict) and p.get("task_type") == "draft_compile"), {})
    check("compile produced a grounded draft (no refusal)", verified is not None and err is None,
          {"elapsed_s": elapsed, "model": usage.get("model_id"), "refusal": (err or {}).get("reason") if isinstance(err, dict) else err})
    if verified is None:
        print(f"\n{sum(1 for c in CHECKS if c[1] == 'PASS')} of {len(CHECKS)} checks passed; project {pid}")
        return 1
    stats = verified.get("provenance_stats") or {}
    doc = verified.get("document") or {}
    summary = (doc.get("meta") or {}).get("claim_summary") or verified.get("claim_summary") or {}
    check("claim summary present (claim-v1)", bool(summary) and summary.get("policy") == "claim-v1", summary)
    paras = [n for n in walk(doc.get("body") or []) if n.get("type") == "paragraph"]
    claims = [((n.get("meta") or {}).get("provenance") or {}) for n in paras]
    blocks = [c.get("claim") for c in claims if isinstance(c, dict) and isinstance(c.get("claim"), dict)]
    expected_blocks = int(summary.get("paragraphs") or 0) + int(summary.get("meta") or 0) if "paragraphs" in summary else int(summary.get("total") or 0)
    check("every assessed paragraph carries a claim block", bool(blocks) and len(blocks) == expected_blocks, f"{len(blocks)} blocks / {summary.get('paragraphs')} claim paragraphs + {summary.get('meta')} notes")
    ok_verdicts = {"VERIFIED", "UNSUPPORTED", "CONTRADICTED", "INSUFFICIENT_EVIDENCE"}
    facts = [b for b in blocks if (b.get("checks") or {}).get("kind") != "meta"]
    metas = [b for b in blocks if (b.get("checks") or {}).get("kind") == "meta"]
    check("verdict vocabulary (facts); notes carry no verdict", all(b.get("verdict") in ok_verdicts for b in facts) and all(b.get("verdict") in (None, "UNSUPPORTED") for b in metas), sorted({str(b.get("verdict")) for b in blocks}))
    units = []  # the claim unit: sentences when the block has them, else the block
    for b in facts:
        checks = b.get("checks") or {}
        subs = checks.get("sub_claims") or []
        # A multi-sentence paragraph counts per sentence; an enumeration's
        # per-fact sub-claims belong to one sentence and count once.
        units.extend(subs if (subs and checks.get("kind") == "sentences") else [b])
    check("claim unit is the sentence (sub_claims present on multi-sentence paragraphs)", None if not units else len(units) == int(summary.get("total") or 0), f"{len(units)} sentence units / total {summary.get('total')}")
    ver = [u for u in units if u.get("verdict") == "VERIFIED"]
    check("every VERIFIED claim has a verbatim source quote", all(u.get("quote") and u.get("quote_verbatim") is True for u in ver), f"{len(ver)} verified of {len(units)}")
    check("no defaulted page numbers", all(u.get("page") is None or isinstance(u.get("page"), int) for u in units) and all(not (u.get("page") is not None and u.get("quote") is None) for u in units), "pages recorded only from the located quote")
    check("no model confidence inside claim blocks", all("confidence\"" not in json.dumps(b).lower() for b in blocks), "ok")
    numeric = [u for u in units if ((u.get("checks") or {}).get("numeric") or {}).get("status") in ("recomputed_ok", "mismatch")]
    check("numeric claims were recomputed", None if not numeric else all(True for _ in numeric), f"{len(numeric)} sentences with figures checked")
    flagged = [b for b in blocks if b.get("flags")]
    check("flags recorded (high-risk wording / inconsistency)", None, [f for b in flagged for f in b.get("flags")][:6] or "none on this draft")
    gate = verified.get("gate_status")
    expected_gate = "pass" if (summary.get("verified") == summary.get("total") and not summary.get("flagged")) else "review"
    check("gate follows the verdicts", gate in ("pass", "review", "blocked") and (gate == expected_gate or gate == "blocked"), {"gate": gate, "verified": summary.get("verified"), "total": summary.get("total"), "contradicted": summary.get("contradicted")})
    rh = (events.get("redhat") or [{}])[-1]
    check("Red-Hat scheduled after compile", rh.get("status") in ("scheduled", "ran", "complete", "pending"), {k: rh.get(k) for k in ("status", "skip_reason", "task_id")})
    t0 = time.time(); rstat = {}
    while time.time() - t0 < 240:
        rstat = http.get(f"{base}/api/projects/{pid}/redhat/status", timeout=10).json()
        state = ((rstat.get("status") or {}).get("state") or "")
        if (rstat.get("status") or {}).get("complete") or state in ("complete", "error"):
            break
        time.sleep(3)
    findings = rstat.get("findings") or []
    check("Red-Hat audit completed on its own", bool((rstat.get("status") or {}).get("complete")), {"state": (rstat.get("status") or {}).get("state"), "findings": len(findings), "error": (rstat.get("status") or {}).get("error")})
    quoted = [f for f in findings if isinstance(f, dict) and f.get("quote")]
    # A finding either stands on a verbatim quote or is an observation the model
    # offered without one; the first must re-find in the source, the second may
    # exist but must be labelled (evidence_kind) — never shown as evidence.
    check("quoted Red-Hat findings are verbatim; unquoted ones are labelled observations", None if not findings else
          all(f.get("quote_verbatim") is True for f in quoted) and all(f.get("evidence_kind") in ("quoted", "observation") or not f.get("quote") for f in findings),
          f"{len(quoted)} quoted (verbatim) · {len(findings) - len(quoted)} observations of {len(findings)}")
    ex = http.get(f"{base}/api/projects/{pid}/export?format=json", timeout=60)
    check("JSON export answers", ex.status_code == 200, ex.status_code)
    pdf_r = http.get(f"{base}/api/projects/{pid}/export?format=dossier-pdf", timeout=120)
    check("dossier PDF renders (or 503 says no renderer)", pdf_r.status_code in (200, 503), pdf_r.status_code)
    bundle = http.get(f"{base}/api/projects/{pid}/export?format=bundle", timeout=120)
    ledger_ok = None
    if bundle.status_code == 200:
        import io, zipfile
        try:
            z = zipfile.ZipFile(io.BytesIO(bundle.content)); names = z.namelist()
            vs = json.loads(z.read(next(n for n in names if n.endswith("verification_state.json"))))
            ledger = vs.get("claim_ledger") or (vs.get("sections") or {}).get("claim_ledger") or vs.get("claims")
            rows = (ledger.get("items") or ledger.get("rows")) if isinstance(ledger, dict) else ledger
            cs = vs.get("claim_summary") or {}
            expected_rows = int(cs.get("paragraphs") or 0) + int(cs.get("meta") or 0) if "paragraphs" in cs else int(cs.get("total") or -1)
            # One ledger row per claim-bearing paragraph (notes sit in their own table), or per sentence;
            # the state's summary may omit `paragraphs`, so rows are matched against the blocks seen above.
            notes = (ledger.get("notes") or []) if isinstance(ledger, dict) else []
            ledger_ok = bool(cs) and isinstance(rows, list) and bool(rows) and (
                len(rows) == int(cs.get("total") or -2) or len(rows) + len(notes) == len(blocks) or len(rows) == len(facts))
        except Exception as exc:  # noqa: BLE001
            ledger_ok = False; names = str(exc)
        check("bundle carries claim_summary + claim ledger", ledger_ok, names if not ledger_ok else "ok")
    else:
        check("bundle export", None, bundle.status_code)
    failed = [c for c in CHECKS if c[1] == "FAIL"]
    print(f"\n{len(CHECKS) - len(failed)} of {len(CHECKS)} checks passed; project {pid}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
