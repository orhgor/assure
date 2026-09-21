"""2D.2 shape probe — one ask, streamed, with the shape it produced and each claim's state.

usage: _probe_2d_shape.py <project_id> <ask> <substrate_id> [...]

Goes through the public shell proxy on 127.0.0.1:8891 so the request carries the gate
key. Prints:

  CACHE_KEY <key>            when the stream replayed a cached compile
  DRAFT (<n> chars): ...     the draft text the model wrote, verbatim
  SHAPE nodes=<n>            paragraph nodes in the compiled body
  CLAIM <id> [<state>] ...   one line per paragraph: verification state + first 60 chars
  STATS {..}                 provenance_stats from the verified frame
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request

BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8891")


def gate_key() -> str:
    raw = open("/etc/assure/shell-access.env").read()
    m = re.search(r"SHELL_ACCESS_KEY\s*=\s*[\"']?([^\"'\n]+)", raw)
    return m.group(1).strip() if m else ""


def state_of(node: dict) -> str:
    prov = (node.get("meta") or {}).get("provenance") or {}
    verdict = (prov.get("entailment") or {}).get("verdict") or ""
    anchored = any(
        isinstance(row, dict) and str(row.get("extracted_quote") or "").strip()
        for row in node.get("provenance") or []
    )
    if verdict == "yes":
        return "supported"
    if verdict == "partial":
        return "partial"
    if verdict == "no":
        return "unsupported"
    if verdict == "unverified":
        return "unverified"
    return "anchored" if anchored else "unanchored"


def main() -> int:
    pid, ask = sys.argv[1], sys.argv[2]
    subs = sys.argv[3:]
    body = json.dumps({"intent": ask, "substrate_file_ids": subs}).encode()
    req = urllib.request.Request(
        f"{BASE}/api/projects/{pid}/draft/stream",
        data=body,
        headers={"Content-Type": "application/json", "X-Shell-Key": gate_key()},
        method="POST",
    )
    frames: list[dict] = []
    events: list[str] = []
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            print("HTTP", resp.status, resp.headers.get("Content-Type"))
            for line in resp:
                text = line.decode("utf-8", "replace").rstrip("\n")
                if text.startswith("event: "):
                    events.append(text[7:].strip())
                if text.startswith("data: "):
                    try:
                        frames.append(json.loads(text[6:]))
                    except Exception:
                        pass
    except Exception as exc:
        print("TRANSPORT ERROR", type(exc).__name__, exc)
    print("EVENTS", json.dumps(events))
    types = [f.get("type") for f in frames]
    seen = []
    for t in types:
        if not seen or seen[-1] != t:
            seen.append(t)
    print("FRAME SEQUENCE", json.dumps(seen))
    print(f"ASK {ask!r}  elapsed {time.time() - t0:.1f}s  frames {len(frames)}")
    for f in frames:
        if f.get("type") == "status" and f.get("cache_key"):
            print(f"CACHE_KEY {f['cache_key']}  omp_cached={f.get('omp_cached')}")
    for f in frames:
        if f.get("type") == "error":
            print("ERROR", json.dumps(f)[:500])
        if f.get("type") == "complete":
            print("COMPLETE", json.dumps(f)[:300])
    compiled = next((f for f in frames if f.get("type") == "compiled"), None)
    tokens = "".join(str(f.get("delta") or "") for f in frames if f.get("type") == "token")
    if tokens and not compiled:
        print(f"TOKENS ({len(tokens)} chars):")
        print(tokens)
    if compiled:
        draft = str(compiled.get("draft_text") or "")
        print(f"DRAFT ({len(draft)} chars):")
        print(draft)
    verified = next((f for f in frames if f.get("type") == "verified"), None)
    if verified:
        doc = verified.get("document") or {}
        paragraphs = [
            n
            for s in doc.get("body") or []
            for n in (s.get("children") or [])
            if n.get("type") == "paragraph"
        ]
        print(f"SHAPE sections={len(doc.get('body') or [])} paragraphs={len(paragraphs)}")
        for node in paragraphs:
            print(
                "CLAIM %-22s [%-11s] %s"
                % (
                    str(node.get("id"))[:22],
                    state_of(node),
                    str(node.get("content") or "")[:60].replace("\n", " "),
                )
            )
        print("STATS", json.dumps(verified.get("provenance_stats") or {}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
