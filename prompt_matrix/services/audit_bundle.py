"""Formal compliance audit report PDF bundle."""

from __future__ import annotations

import html
import json
import os
from datetime import UTC, datetime
from typing import Any

try:
    from ..db.document_lock_repository import latest_lock
    from ..db.sign_off_repository import list_sign_offs
    from ..exporters.text_ast import jdf_to_html, jdf_to_markdown
    from ..services.confidence_spans import build_confidence_spans
except ImportError:
    from db.document_lock_repository import latest_lock
    from db.sign_off_repository import list_sign_offs
    from exporters.text_ast import jdf_to_html, jdf_to_markdown
    from services.confidence_spans import build_confidence_spans


def _esc(text: str) -> str:
    return html.escape(str(text or ""))


def build_audit_bundle_html(
    project_id: str,
    tree: dict[str, Any],
    *,
    z3_results: dict[str, Any] | None = None,
    redhat_critiques: list[dict[str, Any]] | None = None,
) -> str:
    """Build structured HTML for compliance PDF export."""
    title = str((tree.get("meta") or {}).get("title") or project_id)
    exported_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    build_sha = (os.environ.get("ASSURE_BUILD_SHA") or "local").strip()
    sign_offs = list_sign_offs(project_id)
    lock = latest_lock(project_id)
    z3 = z3_results or {}
    redhat = redhat_critiques or []
    spans = build_confidence_spans(tree, z3_results=z3)

    toc_items = [
        "1. Executive Summary",
        "2. Document Body",
        "3. Z3 Verification Results",
        "4. Red-Hat Critique",
        "5. Sign-Offs",
        "6. Document Lock",
        "7. Appendix",
    ]
    toc_html = "".join(f"<li>{_esc(item)}</li>" for item in toc_items)

    doc_html = jdf_to_html(tree)
    z3_rows = ""
    for span in spans[:50]:
        z3_rows += (
            f"<tr><td>{_esc(span.get('text', '')[:80])}</td>"
            f"<td>{span.get('confidence', '')}</td>"
            f"<td>{_esc(span.get('reason', ''))}</td></tr>"
        )
    if not z3_rows:
        z3_rows = f"<tr><td colspan='3'>Status: {_esc(z3.get('status', 'N/A'))}</td></tr>"

    redhat_html = ""
    for item in redhat[:20]:
        redhat_html += f"<li><strong>{_esc(item.get('severity', 'info'))}</strong>: {_esc(item.get('message') or item.get('critique') or '')}</li>"
    if not redhat_html:
        redhat_html = "<li>No Red-Hat critiques recorded.</li>"

    signoff_html = ""
    for so in sign_offs:
        signoff_html += (
            f"<li>{_esc(so.get('reviewer_name_display'))} — "
            f"{_esc(so.get('status'))} "
            f"({_esc(so.get('timestamp'))})"
            f": {_esc(so.get('comment', ''))}</li>"
        )
    if not signoff_html:
        signoff_html = "<li>No sign-offs recorded.</li>"

    lock_html = "Document is not locked."
    if lock:
        lock_html = (
            f"Locked by {_esc(lock.get('locked_by'))} at {_esc(lock.get('locked_at'))}. "
            f"Version {lock.get('version')}. Hash: <code>{_esc(lock.get('content_hash', '')[:16])}…</code>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>Compliance Audit Report — {_esc(title)}</title>
<style>
body {{ font-family: Georgia, serif; margin: 2cm; color: #111; line-height: 1.5; }}
h1 {{ page-break-before: always; border-bottom: 2px solid #333; padding-bottom: 0.5em; }}
h1:first-of-type {{ page-break-before: avoid; }}
h2 {{ color: #333; margin-top: 1.5em; }}
.toc li {{ margin: 0.3em 0; }}
.meta {{ color: #666; font-size: 0.9em; }}
table {{ border-collapse: collapse; width: 100%; margin: 1em 0; }}
th, td {{ border: 1px solid #ccc; padding: 0.4em 0.6em; text-align: left; font-size: 0.85em; }}
th {{ background: #f5f5f5; }}
.cover {{ text-align: center; padding: 4cm 0; }}
.cover h1 {{ border: none; font-size: 2em; }}
</style>
</head>
<body>
<section class="cover">
  <h1>Compliance Audit Report</h1>
  <p class="meta"><strong>{_esc(title)}</strong></p>
  <p class="meta">Project: {_esc(project_id)}</p>
  <p class="meta">Generated: {exported_at}</p>
  <p class="meta">Build: {_esc(build_sha)}</p>
</section>

<h1>Table of Contents</h1>
<ol class="toc">{toc_html}</ol>

<h1>1. Executive Summary</h1>
<p>This report bundles the verified JDF document, Z3 verification results, Red-Hat critique,
sign-off records, and document lock hash for compliance review.</p>
<p>Z3 status: <strong>{_esc(z3.get('status', 'N/A'))}</strong>.
Red-Hat items: <strong>{len(redhat)}</strong>.
Sign-offs: <strong>{len(sign_offs)}</strong>.</p>

<h1>2. Document Body</h1>
{doc_html}

<h1>3. Z3 Verification Results</h1>
<table>
<thead><tr><th>Span</th><th>Confidence</th><th>Explanation</th></tr></thead>
<tbody>{z3_rows}</tbody>
</table>

<h1>4. Red-Hat Critique</h1>
<ul>{redhat_html}</ul>

<h1>5. Sign-Offs</h1>
<ul>{signoff_html}</ul>

<h1>6. Document Lock</h1>
<p>{lock_html}</p>

<h1>7. Appendix</h1>
<pre>{_esc(json.dumps({'truth_ledger': tree.get('truth_ledger') or {}}, indent=2)[:4000])}</pre>
</body>
</html>"""


def export_audit_bundle_pdf(
    project_id: str,
    tree: dict[str, Any],
    *,
    z3_results: dict[str, Any] | None = None,
    redhat_critiques: list[dict[str, Any]] | None = None,
) -> bytes:
    """Render compliance audit bundle as PDF bytes."""
    html_body = build_audit_bundle_html(
        project_id,
        tree,
        z3_results=z3_results,
        redhat_critiques=redhat_critiques,
    )
    try:
        from ..exporters.pdf_ast import _pdf_via_weasyprint, _pdf_via_playwright, _pdf_via_simple
    except ImportError:
        from exporters.pdf_ast import _pdf_via_weasyprint, _pdf_via_playwright, _pdf_via_simple

    pdf = _pdf_via_weasyprint(html_body)
    if pdf:
        return pdf
    pdf = _pdf_via_playwright(html_body)
    if pdf:
        return pdf
    md = jdf_to_markdown(tree)
    return _pdf_via_simple({"meta": {"title": f"Audit-{project_id}"}, "body": []})
