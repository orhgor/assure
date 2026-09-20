"""2D.1 live verification — export the demo's .jdf, load it into a fresh project, compare.

Read-only against demo-3235f5. Prints, in order:

  EXPORT ...        the sidecar's declared contents (hash, chain head, model, manifest, states)
  NODE ...          one line per node: state + the anchor it resolved to
  CHAIN ...         the last links of the version chain with their tree hashes
  IMPORT ...        the fresh project's round_trip report
  LOADED ...        the fresh project's own tree, compared node-by-node with the export
  BUNDLE ...        the zip's members
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
import zipfile
from io import BytesIO

BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8891")
DEMO = "demo-3235f5"


def key() -> str:
    raw = open("/etc/assure/shell-access.env").read()
    m = re.search(r"SHELL_ACCESS_KEY\s*=\s*[\"']?([^\"'\n]+)", raw)
    return m.group(1).strip() if m else ""


def call(path: str, body: dict | None = None, method: str = "GET"):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", "X-Shell-Key": key()},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        raw = resp.read()
        ctype = resp.headers.get("Content-Type", "")
        if "json" in ctype:
            return resp.status, json.loads(raw), ctype
        return resp.status, raw, ctype


def main() -> int:
    db = sqlite3.connect("prompt_matrix/history.sqlite")
    before = db.execute("SELECT current_version FROM projects WHERE id = ?", (DEMO,)).fetchone()
    print("DEMO current_version before:", before[0] if before else None)

    status, sidecar, ctype = call(f"/api/projects/{DEMO}/export?format=jdf")
    print(f"EXPORT status={status} ctype={ctype} bytes={len(json.dumps(sidecar))}")
    print("EXPORT format=", sidecar.get("format"), "sidecar_version=", sidecar.get("sidecar_version"))
    print("EXPORT document_sha256=", sidecar.get("document_sha256"))
    print("EXPORT chain_head_sha256=", sidecar.get("chain_head_sha256"))
    print("EXPORT drafting_model=", json.dumps(sidecar.get("drafting_model")))
    print("EXPORT verification=", json.dumps(sidecar.get("verification")))
    print("EXPORT source_manifest=", json.dumps(sidecar.get("source_manifest")))
    chain = sidecar.get("version_chain") or {}
    print(
        "CHAIN revision_count=%s truncated=%s ordered=%s"
        % (chain.get("revision_count"), chain.get("truncated"), chain.get("ordered"))
    )
    for link in (chain.get("revisions") or [])[-3:]:
        print("CHAIN", json.dumps(link))

    doc = sidecar.get("document") or {}
    demo_nodes = {}
    for section in doc.get("body") or []:
        for node in section.get("children") or []:
            demo_nodes[str(node.get("id"))] = node
    index = {str(e["node_id"]): e for e in sidecar.get("nodes") or []}
    print("EXPORT body sections=%d leaf nodes=%d" % (len(doc.get("body") or []), len(demo_nodes)))
    for node_id, node in demo_nodes.items():
        entry = index.get(node_id) or {}
        anchor = entry.get("anchor") or {}
        print(
            "NODE %-22s %-11s anchor=%.60s p=%s quote=%d chars  meta.provenance=%s"
            % (
                node_id,
                entry.get("verification_state"),
                str(anchor.get("source_name")),
                anchor.get("page_number"),
                len(str(anchor.get("quote") or "")),
                "yes" if (node.get("meta") or {}).get("provenance") else "no",
            )
        )

    # ---- round trip into a fresh project ---------------------------------
    stamp = time.strftime("%H%M%S")
    _, created, _ = call("/api/projects", {"title": f"2D Round Trip {stamp}"}, method="POST")
    fresh = created.get("id")
    print("FRESH project:", fresh)
    status, imported, _ = call(f"/api/projects/{fresh}/import-jdf", sidecar, method="POST")
    print(f"IMPORT status={status} ok={imported.get('ok')} version={imported.get('version')}")
    print("IMPORT round_trip=", json.dumps(imported.get("round_trip"), indent=1))

    status, loaded_payload, _ = call(f"/api/projects/{fresh}/jdf")
    loaded = loaded_payload.get("document") or {}
    fresh_nodes = {}
    for section in loaded.get("body") or []:
        for node in section.get("children") or []:
            fresh_nodes[str(node.get("id"))] = node

    same_ids = set(fresh_nodes) == set(demo_nodes)
    print("LOADED same node ids=%s (%d)" % (same_ids, len(fresh_nodes)))
    matched_anchors = 0
    for node_id, node in fresh_nodes.items():
        src = demo_nodes.get(node_id) or {}
        a = (src.get("provenance") or [{}])[0]
        b = (node.get("provenance") or [{}])[0]
        ok = (
            a.get("source_id") == b.get("source_id")
            and str(a.get("extracted_quote")) == str(b.get("extracted_quote"))
            and str(a.get("page_number")) == str(b.get("page_number"))
        )
        verdict_a = ((src.get("meta") or {}).get("provenance") or {}).get("entailment") or {}
        verdict_b = ((node.get("meta") or {}).get("provenance") or {}).get("entailment") or {}
        ok_verdict = verdict_a.get("verdict") == verdict_b.get("verdict")
        matched_anchors += 1 if ok else 0
        print(
            "LOADED %-22s anchor=%s verdict=%s (%s -> %s) meta.provenance=%s"
            % (
                node_id,
                "same" if ok else "DIFFERENT",
                "same" if ok_verdict else "DIFFERENT",
                verdict_a.get("verdict"),
                verdict_b.get("verdict"),
                "yes" if (node.get("meta") or {}).get("provenance") else "no",
            )
        )
    print("LOADED anchors identical: %d/%d" % (matched_anchors, len(fresh_nodes)))

    # ---- bundle ----------------------------------------------------------
    status, blob, ctype = call(f"/api/projects/{DEMO}/export?format=bundle")
    names = zipfile.ZipFile(BytesIO(blob)).namelist() if status == 200 else []
    print(f"BUNDLE status={status} ctype={ctype} members={json.dumps(names)}")
    if names:
        inner = zipfile.ZipFile(BytesIO(blob)).read([n for n in names if n.endswith(".jdf")][0])
        inner_doc = json.loads(inner)
        print(
            "BUNDLE inner jdf format=%s sha=%s nodes=%d"
            % (inner_doc.get("format"), inner_doc.get("document_sha256"), len(inner_doc.get("nodes") or []))
        )

    after = sqlite3.connect("prompt_matrix/history.sqlite").execute(
        "SELECT current_version FROM projects WHERE id = ?", (DEMO,)
    ).fetchone()
    print("DEMO current_version after export:", after[0] if after else None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
