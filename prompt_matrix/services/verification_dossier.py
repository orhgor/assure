"""Formal Verification Certificate export for founder workbench."""

from __future__ import annotations

import hashlib
import html
import json
import os
from datetime import UTC, datetime
from typing import Any

try:
    from ..db.drafts_repository import fetch_draft
    from ..db.redhat_findings_repository import list_findings_for_workspace
    from ..db.runs_repository import list_runs
    from ..exporters.text_ast import jdf_to_html
    from ..services.lock_metadata import lock_hash
    from ..services.macro_verify import detect_cross_run_contradictions
except ImportError:
    from db.drafts_repository import fetch_draft
    from db.redhat_findings_repository import list_findings_for_workspace
    from db.runs_repository import list_runs
    from exporters.text_ast import jdf_to_html
    from services.lock_metadata import lock_hash
    from services.macro_verify import detect_cross_run_contradictions


def _esc(text: str) -> str:
    return html.escape(str(text or ""))


def _collect_lock_ledger(draft: dict[str, Any], runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ledger: list[dict[str, Any]] = []
    seen: set[str] = set()
    for run in runs:
        for lock in run.get("extracted_locks") or []:
            h = str(lock.get("lock_hash") or lock_hash(lock))
            if h in seen:
                continue
            seen.add(h)
            ledger.append(
                {
                    "claim": f"{lock.get('canonical_key') or lock.get('metric')} = {lock.get('value')}",
                    "source_id": lock.get("source_id") or "",
                    "page": (lock.get("page_coordinates") or {}).get("page", 1),
                    "lock_hash": h,
                }
            )
    meta = draft.get("meta") or {}
    if meta.get("lock_ledger"):
        for row in meta.get("lock_ledger") or []:
            h = str(row.get("lock_hash") or "")
            if h and h not in seen:
                seen.add(h)
                ledger.append(row)
    return ledger


def build_verification_certificate_html(workspace_id: str) -> str:
    draft_row = fetch_draft(workspace_id) or {}
    draft = draft_row.get("content") or {
        "document_id": f"draft-{workspace_id}",
        "meta": {"title": "Founder Draft"},
        "body": [],
        "truth_ledger": {},
    }
    runs = list_runs(workspace_id=workspace_id)
    run_ids = [r["id"] for r in runs]
    conflicts = detect_cross_run_contradictions(run_ids) if len(run_ids) >= 2 else []
    findings = list_findings_for_workspace(workspace_id)
    ledger = _collect_lock_ledger(draft, runs)
    exported_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    build_sha = (os.environ.get("ASSURE_BUILD_SHA") or "local").strip()
    content_hash = hashlib.sha256(
        json.dumps(draft, sort_keys=True, default=str).encode()
    ).hexdigest()

    ledger_rows = ""
    for row in ledger:
        ledger_rows += (
            f"<tr><td>{_esc(row.get('claim'))}</td>"
            f"<td><code>{_esc(row.get('source_id'))}</code></td>"
            f"<td>{_esc(row.get('page'))}</td>"
            f"<td><code>{_esc(row.get('lock_hash'))}</code></td></tr>"
        )
    if not ledger_rows:
        ledger_rows = "<tr><td colspan='4'>No locked claims recorded.</td></tr>"

    finding_rows = ""
    for f in findings:
        finding_rows += (
            f"<tr><td>{_esc(f.get('title'))}</td>"
            f"<td>{_esc(f.get('status'))}</td>"
            f"<td>{_esc(f.get('content'))}</td>"
            f"<td>{_esc(f.get('suggested_fix'))}</td>"
            f"<td>{_esc(f.get('dismissal_rationale'))}</td></tr>"
        )
    if not finding_rows:
        finding_rows = "<tr><td colspan='5'>No Red-Hat findings recorded.</td></tr>"

    conflict_rows = ""
    for c in conflicts:
        conflict_rows += (
            f"<li><strong>{_esc(c.get('conflict_type'))}</strong> "
            f"({_esc(c.get('severity'))}): {_esc(c.get('claim_a'))} vs {_esc(c.get('claim_b'))}</li>"
        )
    if not conflict_rows:
        conflict_rows = "<li>No cross-run contradictions detected.</li>"

    doc_html = jdf_to_html(draft)
    title = str((draft.get("meta") or {}).get("title") or workspace_id)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>Formal Verification Certificate — {_esc(title)}</title>
<style>
body {{ font-family: Georgia, serif; margin: 2cm; color: #111; line-height: 1.5; }}
h1 {{ border-bottom: 2px solid #333; padding-bottom: 0.5em; }}
h2 {{ color: #333; margin-top: 1.5em; }}
table {{ border-collapse: collapse; width: 100%; margin: 1em 0; }}
th, td {{ border: 1px solid #ccc; padding: 0.4em 0.6em; text-align: left; font-size: 0.95em; }}
th {{ background: #f3f4f6; }}
code {{ font-family: monospace; font-size: 0.9em; }}
.meta {{ color: #555; font-size: 0.95em; }}
</style>
</head>
<body>
<h1>Formal Verification Certificate</h1>
<p class="meta">Workspace: {_esc(workspace_id)} · Exported {_esc(exported_at)} · Build {_esc(build_sha)}</p>
<p class="meta">Cryptographic lock hash: <code>{_esc(content_hash)}</code></p>

<h2>1. Final Draft</h2>
{doc_html}

<h2>2. Evidence Ledger</h2>
<table>
<thead><tr><th>Claim</th><th>Source</th><th>Page</th><th>Lock hash</th></tr></thead>
<tbody>{ledger_rows}</tbody>
</table>

<h2>3. Cross-Run Verification</h2>
<ul>{conflict_rows}</ul>

<h2>4. Red-Hat Resolution Log</h2>
<table>
<thead><tr><th>Finding</th><th>Status</th><th>Detail</th><th>Fix</th><th>Dismissal rationale</th></tr></thead>
<tbody>{finding_rows}</tbody>
</table>
</body>
</html>"""


try:
    from ..exporters.pdf_ast import export_html_to_pdf
except ImportError:
    from exporters.pdf_ast import export_html_to_pdf


def export_verification_dossier_pdf(workspace_id: str) -> bytes:
    html_doc = build_verification_certificate_html(workspace_id)
    title = workspace_id or "verification-dossier"
    return export_html_to_pdf(html_doc, fallback_title=f"Verification-{title}")
