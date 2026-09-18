#!/usr/bin/env python3
"""THROWAWAY — build the two Math Check pages for the before/after screenshots.

Not product code. Both pages carry the same DOM as index.html's compile panel and
load the *real* assets (static/style.css and static/audit_gate.js). The "before"
page loads the audit_gate.js that shipped before this work (from git), so the
comparison is the same payload rendered by the old component and the new one —
not a mock of either.

usage: python /tmp/make_render_harness.py <payload.json>
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path("/Users/og/Untitled")
OUT = Path("/tmp/z3-harness")
PREV_COMMIT = "f674370"  # the commit before the Math Check tier work

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Math Check — {label}</title>
<link rel="stylesheet" href="{static}/style.css">
<style>
  body {{ margin: 0; padding: 24px; background: #f6f7f9; font-family: -apple-system, system-ui, sans-serif; }}
  .workbench-slice {{ max-width: 900px; }}
  .panel-label {{ font-size: 0.75rem; letter-spacing: .08em; text-transform: uppercase; color: #667; margin: 0 0 8px; }}
  .compile-status-bar {{ background: #fff; border: 1px solid #e3e6ea; border-radius: 12px; padding: 14px; }}
  .compile-status-row {{ display: flex; align-items: flex-start; gap: 16px; flex-wrap: wrap; }}
  .compile-summary {{ display: flex; gap: 14px; font-size: .85rem; }}
  #harness-note {{ font-size: .8rem; color: #556; margin-top: 10px; }}
</style>
</head>
<body>
<div class="workbench-slice">
  <p class="panel-label">Compile panel — Math Check surface ({label})</p>
  <div id="compilation-summary" class="generate-summary compile-status-bar workbench-stage-verify-only">
    <div class="compile-status-row">
      <div class="compile-summary">
        <span><span>Nodes</span>: <strong id="generate-node-count">{nodes}</strong></span>
        <span><span>Locks</span>: <strong id="generate-lock-count">{locks}</strong></span>
      </div>
      <div id="z3-status" class="gate-z3-status verification-badge" hidden></div>
    </div>
  </div>
  <p id="harness-note">Same payload, same markup, {label} audit_gate.js ({js_source}).</p>
</div>
<script src="{static}/audit_gate.js"></script>
<script>
  var PAYLOAD = {payload};
  var el = document.getElementById("z3-status");
  window.AssureAuditGate.renderWorkbenchAudit(PAYLOAD, {{
    z3El: el,
    redhatEl: null,
    gateBanner: null,
    gateText: null
  }});
  window.__harnessDone = true;
</script>
</body>
</html>
"""


def main() -> int:
    payload = json.loads(Path(sys.argv[1]).read_text())
    OUT.mkdir(parents=True, exist_ok=True)

    old_js = subprocess.run(
        ["git", "show", f"{PREV_COMMIT}:prompt_matrix/static/audit_gate.js"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    for label, js, source in (
        ("before", old_js, f"{PREV_COMMIT}:static/audit_gate.js"),
        ("after", (REPO / "prompt_matrix/static/audit_gate.js").read_text(), "working tree"),
    ):
        target = OUT / label
        target.mkdir(parents=True, exist_ok=True)
        (target / "audit_gate.js").write_text(js)
        (target / "style.css").write_text((REPO / "prompt_matrix/static/style.css").read_text())
        (target / "index.html").write_text(
            PAGE.format(
                label=label,
                static=".",
                js_source=source,
                nodes=payload.get("node_count") or 0,
                locks=payload.get("lock_count") or 0,
                payload=json.dumps(payload),
            )
        )
        print(f"wrote {target/'index.html'}")

    print("\npayload z3_results:", json.dumps(payload.get("z3_results"), indent=1)[:800])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
