"""B4: the anchored-ratio distribution — 3 real documents x 3 questions each.

usage: SHELL_KEY=<gate key> _probe_b4_distribution.py

Reports, per (document, question), the four counters and the anchored ratio
(anchored / eligible). The floor for B5 is derived from this distribution, which
is why this runs BEFORE any threshold is chosen: the numbers decide it, not the
other way round.

Nothing is hardcoded about which ratio is "good". A refusal comes back as HTTP
422 with no stats, and is reported as REFUSED rather than as zero.
"""
import json
import os
import time
import urllib.error
import urllib.request

BASE = os.environ.get("PROBE_BASE", "https://app.getassureai.com")
KEY = os.environ["SHELL_KEY"]

DOCS = [
    ("D1 naic-underwriting-policy-redacted.md (1726 chars, markdown policy)", "client-table-b4-naic-006d20", "sub-02a1eccc65d7424f", [
        ("specific", "What is the wind/hail deductible percentage?"),
        ("medium", "Summarize the Massachusetts underwriting obligations and the coverage limits."),
        ("broad", "How does property insurance underwriting work?"),
    ]),
    ("D2 9-Physiological-Demands-...-Combat-Flight.pdf (43167 chars, 8pp paper)", "physiological-arousals-8b848a", "sub-90ef1e5227d54558", [
        ("brief-ask", "what is physiology can show to human"),
        ("specific", "Which physiological variables were measured in the fighter pilots?"),
        ("medium", "Summarize the physiological demands of real and simulated combat flight."),
    ]),
    ("D3 CPT_90834_vs_90837_Final_Version.md (5808 chars, clinical coding)", "shell-proto-2a5c3a", "sub-2db754ac4c9b4c7f", [
        ("specific", "What is the time difference between CPT 90834 and CPT 90837?"),
        ("medium", "Summarize the documentation requirements for these two CPT codes."),
        ("broad", "How should a clinician choose between these codes?"),
    ]),
]


def compile_one(pid: str, sid: str, intent: str) -> dict:
    body = json.dumps({"intent": intent, "substrate_file_ids": [sid]}).encode()
    req = urllib.request.Request(
        f"{BASE}/api/projects/{pid}/draft/stream", data=body,
        headers={"Content-Type": "application/json", "Accept": "text/event-stream",
                 # Cloudflare 403s the default Python-urllib agent; this is the
                 # same agent curl presents, and the request is authorised by the
                 # gate key either way.
                 "User-Agent": "curl/8.7.1",
                 "Authorization": f"Bearer {KEY}"}, method="POST")
    t0 = time.time()
    frames = []
    status = None
    try:
        with urllib.request.urlopen(req, timeout=420) as resp:
            status = resp.status
            for line in resp:
                text = line.decode("utf-8", "replace").rstrip("\n")
                if text.startswith("data: "):
                    try:
                        frames.append(json.loads(text[6:]))
                    except Exception:
                        pass
    except urllib.error.HTTPError as e:
        status = e.code
    stats = None
    err = None
    for f in frames:
        if "provenance_stats" in f and stats is None:
            stats = f["provenance_stats"]
        if f.get("type") == "error":
            err = f.get("reason") or f.get("error")
        if f.get("type") == "complete" and f.get("ok") is False and err is None:
            err = f.get("error")
    return {"status": status, "elapsed": round(time.time() - t0, 1), "stats": stats, "error": err}


def main() -> None:
    rows = []
    for doc_label, pid, sid, questions in DOCS:
        print(f"\n=== {doc_label}  [{pid}] ===", flush=True)
        for kind, q in questions:
            r = compile_one(pid, sid, q)
            s = r["stats"] or {}
            elig = s.get("eligible", 0)
            anch = s.get("anchored", 0)
            ratio = (anch / elig * 100.0) if elig else 0.0
            if r["status"] == 422 or r["error"]:
                print(f"  {kind:<10} REFUSED (http {r['status']}, {r['elapsed']}s) reason={str(r['error'])[:80]}", flush=True)
                rows.append((doc_label, kind, q, None, None, None, "REFUSED"))
                continue
            if s.get("eligible") is None:
                # A transport failure must never be reported as a 0% ratio.
                print(f"  {kind:<10} TRANSPORT-FAILURE http={r['status']} ({r['elapsed']}s)", flush=True)
                rows.append((doc_label, kind, q, None, None, None, "TRANSPORT"))
                continue
            print(f"  {kind:<10} eligible={elig} anchored={anch} "
                  f"supported={s.get('supported')} partial={s.get('partial')} "
                  f"unsupported={s.get('unsupported')} unanchored={s.get('unanchored')} "
                  f"ratio={ratio:.0f}%  ({r['elapsed']}s)", flush=True)
            rows.append((doc_label, kind, q, elig, anch, ratio, "ok"))

    print("\n\n=== DISTRIBUTION (anchored / eligible) ===")
    print(f"{'document':<52} {'ask':<10} {'elig':>4} {'anch':>4} {'ratio':>6}")
    vals = []
    for doc, kind, q, elig, anch, ratio, state in rows:
        if state == "ok":
            print(f"{doc[:50]:<52} {kind:<10} {elig:>4} {anch:>4} {ratio:>5.0f}%")
            vals.append(ratio)
        else:
            print(f"{doc[:50]:<52} {kind:<10} {'-':>4} {'-':>4} {'REFUSED':>6}")
    if vals:
        vals_sorted = sorted(vals)
        n = len(vals_sorted)
        median = vals_sorted[n // 2] if n % 2 else (vals_sorted[n // 2 - 1] + vals_sorted[n // 2]) / 2
        print(f"\nok compiles={n}  min={min(vals):.0f}%  median={median:.0f}%  max={max(vals):.0f}%")
        print("ratios sorted:", [f"{v:.0f}%" for v in vals_sorted])


if __name__ == "__main__":
    main()
