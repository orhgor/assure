"""Dump the claims + provenance of a compile on the demo project.
usage: _probe_claims.py <project_id> <substrate_id> [intent-file]
"""
import json, os, re, sys, urllib.request

BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8891")

def gate_key():
    raw = open("/etc/assure/shell-access.env").read()
    m = re.search(r"SHELL_ACCESS_KEY\s*=\s*[\"']?([^\"'\n]+)", raw)
    return m.group(1).strip() if m else ""

def post_stream(path, body, key, timeout=420.0):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-Shell-Key": key}, method="POST")
    frames = []
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for line in resp:
            t = line.decode("utf-8", "replace").rstrip("\n")
            if t.startswith("data: "):
                try: frames.append(json.loads(t[6:]))
                except Exception: pass
    return frames

pid = sys.argv[1]; sub = sys.argv[2]
intent_file = sys.argv[3] if len(sys.argv) > 3 else "/home/ubuntu/probes/_demo_intent.txt"
intent = open(intent_file).read().strip()
frames = post_stream(f"/api/projects/{pid}/draft/stream",
                     {"intent": intent, "substrate_file_ids": [sub]}, gate_key())
ver = [f for f in frames if f.get("type") == "verified"]
payload = ver[-1] if ver else {}
print("cache_hit:", payload.get("cache_hit"), "omp_cached:", payload.get("omp_cached"))
print("provenance_stats:", json.dumps(payload.get("provenance_stats")))
claims = payload.get("claims") or []
print("claims:", len(claims))
for c in claims:
    print(json.dumps(c, ensure_ascii=False)[:1000])
    print("---")
json.dump(frames, open("/tmp/claims_frames.json", "w"), indent=1)
print("wrote /tmp/claims_frames.json")
