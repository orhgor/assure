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
except ImportError:
    from db.document_lock_repository import latest_lock
    from db.sign_off_repository import list_sign_offs
    from exporters.text_ast import jdf_to_html, jdf_to_markdown


def _esc(text: str) -> str:
    return html.escape(str(text or ""))


def _walk_nodes(tree: dict[str, Any]):
    for section in tree.get("body") or []:
        if not isinstance(section, dict):
            continue
        yield section
        for child in section.get("children") or []:
            if isinstance(child, dict):
                yield child


def _derive_z3_from_tree(tree: dict[str, Any]) -> str:
    has_viol = False
    has_pass = False
    for node in _walk_nodes(tree):
        for z in (node.get("annotations") or {}).get("z3") or []:
            if (z or {}).get("status") == "violation":
                has_viol = True
            elif (z or {}).get("status") == "pass":
                has_pass = True
    if has_viol:
        return "VIOLATION"
    if has_pass:
        return "PASS"
    return "SKIPPED"


def _derive_redhat_count(tree: dict[str, Any]) -> int:
    total = 0
    for node in _walk_nodes(tree):
        total += len((node.get("annotations") or {}).get("redhat") or [])
    return total


def _normalize_gate(g: dict[str, Any]) -> dict[str, Any]:
    stats = g.get("provenance_stats") or g
    eligible = int(stats.get("eligible") or 0)
    anchored = int(stats.get("anchored") or 0)
    supported = int(stats.get("supported") or 0)
    partial = int(stats.get("partial") or 0)
    unsupported = int(stats.get("unsupported") or 0)
    unverified_claims = int(stats.get("unverified") or 0)
    unanchored = int(stats.get("unanchored") or (eligible - anchored))
    return {
        "gate_status": str(g.get("gate_status") or "review"),
        "z3_status": str(g.get("z3_status") or "SKIPPED"),
        "redhat_count": int(g.get("redhat_count") or 0),
        "unverified": bool(g.get("unverified")),
        "unverified_reason": str(g.get("unverified_reason") or ""),
        "eligible": eligible,
        "anchored": anchored,
        "supported": supported,
        "unsupported": unsupported,
        "unanchored": unanchored,
        "has_substrate": bool(g.get("has_substrate", False)),
        "provenance_stats": {
            "eligible": eligible,
            "anchored": anchored,
            "supported": supported,
            "partial": partial,
            "unsupported": unsupported,
            "unanchored": unanchored,
            "unverified": unverified_claims,
        },
    }


def _read_persisted_gate(project_id: str) -> dict[str, Any] | None:
    try:
        from ..history import get_db
        from ..db.connection import init_db
    except ImportError:
        from history import get_db
        from db.connection import init_db
    try:
        init_db()
        db = get_db()
        row = db.execute(
            "SELECT last_compiled_json FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if not row or not row[0]:
            return None
        data = json.loads(row[0])
        gate = (data or {}).get("gate")
        return _normalize_gate(gate) if isinstance(gate, dict) else None
    except Exception:
        return None


def compute_export_gate(
    project_id: str,
    tree: dict[str, Any],
    *,
    z3_results: dict[str, Any] | None = None,
    redhat_critiques: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Recompute the verification gate for an export. No persistence added."""
    # Prefer the gate block persisted at compile time; compute only as a
    # fallback for revisions (pre-fix docs) without a persisted block.
    persisted = _read_persisted_gate(project_id)
    if persisted is not None:
        return persisted
    try:
        from ..services.audit_summary import _provenance_counts, compute_gate_status
    except ImportError:
        from services.audit_summary import _provenance_counts, compute_gate_status
    try:
        from ..db.substrate_repository import list_substrate_for_project
    except ImportError:
        from db.substrate_repository import list_substrate_for_project

    counts = _provenance_counts(tree)
    eligible = counts["eligible"]
    anchored = counts["anchored"]
    supported = counts["supported"]
    partial = counts["partial"]
    unsupported = counts["unsupported"]
    unverified_claims = counts["unverified"]
    z3_status = str((z3_results or {}).get("status") or _derive_z3_from_tree(tree) or "SKIPPED")
    redhat_count = (
        len(redhat_critiques) if redhat_critiques is not None else _derive_redhat_count(tree)
    )
    has_substrate = bool(list_substrate_for_project(project_id))

    # Two layers, as in build_audit_summary: `anchored` is the grounding, the
    # verdicts are the truthfulness. The gate reads the verdict.
    if supported == 0:
        gate_status = "review"
        unverified = True
        if partial or unsupported or unverified_claims:
            bits = []
            if partial:
                bits.append(f"{partial} supported only in part")
            if unsupported:
                bits.append(f"{unsupported} contradicted by their source")
            if unverified_claims:
                bits.append(f"{unverified_claims} could not be checked")
            reason = (
                f"0 of {eligible} claims were entailed by their matched source sentence "
                f"({', '.join(bits)})."
            )
        elif counts["unchecked"]:
            reason = (
                f"0 of {eligible} claims were entailment-checked against their matched "
                f"source sentence ({counts['unchecked']} anchored but never checked)."
            )
        elif has_substrate:
            reason = f"0 of {eligible} claims matched any source sentence."
        else:
            reason = "No sources included in this compile — output is ungrounded."
    else:
        gate_status = compute_gate_status(z3_status, redhat_count)
        unverified = False
        reason = ""

    return {
        "gate_status": gate_status,
        "z3_status": z3_status,
        "redhat_count": redhat_count,
        "unverified": unverified,
        "unverified_reason": reason,
        "eligible": eligible,
        "anchored": anchored,
        "supported": supported,
        "unsupported": unsupported,
        "unanchored": counts["unanchored"],
        "has_substrate": has_substrate,
        "provenance_stats": {
            "eligible": eligible,
            "anchored": anchored,
            "supported": supported,
            "partial": partial,
            "unsupported": unsupported,
            "unanchored": counts["unanchored"],
            "unverified": unverified_claims,
        },
    }


def gate_markdown(project_id: str, tree: dict[str, Any], gate: dict[str, Any]) -> str:
    lines = [f"Verification Gate: {gate.get('gate_status')}"]
    if gate.get("unverified"):
        lines.append(gate.get("unverified_reason") or "")
        lines.append(f"Claims anchored: {gate.get('anchored')} of {gate.get('eligible')}")
        lines.append(
            f"Claims verified against their source: {gate.get('supported', 0)} "
            f"of {gate.get('eligible')}"
        )
    return "\n".join(l for l in lines if l)


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
    redhat = redhat_critiques or []

    # FIX 4 — TOC: toc_items carry no numbers; the <ol> is the single source
    # of numbering. Entries are newline-separated so they don't mash together.
    toc_items = [
        "Executive Summary",
        "Document Body",
        "Z3 Verification Results",
        "Red-Hat Critique",
        "Sign-Offs",
        "Document Lock",
        "Appendix",
    ]
    toc_html = "".join(f"<li>{_esc(item)}</li>\n" for item in toc_items)

    # Gate / Z3 data comes solely from the persisted last_compiled_json row via
    # the existing helpers (the route passes only (project_id, tree)). Use the
    # fallback recompute only for revisions without a persisted gate block.
    persisted_gate = _read_persisted_gate(project_id)
    gate_has_block = persisted_gate is not None
    gate = (
        persisted_gate
        if gate_has_block
        else compute_export_gate(
            project_id, tree, z3_results=z3_results, redhat_critiques=redhat_critiques
        )
    )

    # FIX 5 — Document Body: say so when there is no body (never fall back to
    # the project id / a title).
    body_nodes = tree.get("body") or []
    doc_html = jdf_to_html(tree) if body_nodes else "<p><em>No document body recorded.</em></p>"

    # FIX 3 — Verification Gate: always populated, never empty.
    if not gate_has_block:
        gate_html = (
            "<h1>0. Verification Gate</h1>\n" "<p>No verification run for this document.</p>"
        )
    else:
        gate_status = gate.get("gate_status") or "review"
        anchored = gate.get("anchored", 0)
        supported = gate.get("supported", 0)
        eligible = gate.get("eligible", 0)
        gate_html = (
            "<h1>0. Verification Gate</h1>\n"
            f"<p><strong>Gate: {_esc(str(gate_status))}</strong></p>\n"
            f"<p>Claims anchored: {_esc(str(anchored))} of {_esc(str(eligible))}</p>\n"
            f"<p>Claims verified against their source: {_esc(str(supported))} "
            f"of {_esc(str(eligible))}</p>"
        )
        reason = gate.get("unverified_reason") or ""
        if reason:
            gate_html += f"\n<p>{_esc(reason)}</p>"

    # FIX 2 — Z3 Verification Results: gate-level status, not a confidence span
    # table. Lock / metric / violation detail is rendered only when the persisted
    # gate actually carries it (the persisted gate stores z3_status today).
    if not gate_has_block:
        z3_render = "<p>No Z3 run recorded for this document.</p>"
    else:
        z3_status = gate.get("z3_status") or "N/A"
        z3_render = f"<p><strong>Status:</strong> {_esc(str(z3_status))}</p>"
        z3_metrics = [
            ("Locks verified", gate.get("locks_verified")),
            ("Locks rejected", gate.get("locks_rejected")),
            ("Metrics checked", gate.get("metrics_checked")),
        ]
        present = [(label, val) for label, val in z3_metrics if val is not None]
        if present:
            z3_render += (
                "\n<ul>\n"
                + "\n".join(f"<li>{_esc(label)}: {_esc(str(val))}</li>" for label, val in present)
                + "\n</ul>"
            )
        violations = gate.get("violations") or []
        if violations:
            z3_render += (
                "\n<ul>\n" + "\n".join(f"<li>{_esc(str(v))}</li>" for v in violations) + "\n</ul>"
            )

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

{gate_html}

<h1>1. Executive Summary</h1>
<p>This report bundles the verified JDF document, Z3 verification results, Red-Hat critique,
sign-off records, and document lock hash for compliance review.</p>
<p>Z3 status: <strong>{_esc(gate.get('z3_status') or 'N/A')}</strong>.
Red-Hat items: <strong>{len(redhat)}</strong>.
Sign-offs: <strong>{len(sign_offs)}</strong>.</p>

<h1>2. Document Body</h1>
{doc_html}

<h1>3. Z3 Verification Results</h1>
{z3_render}

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
    # Fallback text PDF — surface the gate at the top so the exported artifact
    # reflects verification state even without an HTML renderer.
    gate = compute_export_gate(
        project_id, tree, z3_results=z3_results, redhat_critiques=redhat_critiques
    )
    prefix = gate_markdown(project_id, tree, gate)
    md = jdf_to_markdown(tree)
    body = (prefix + "\n\n" + md) if prefix else md
    try:
        from ..history import _simple_pdf
    except ImportError:
        from history import _simple_pdf
    return _simple_pdf(f"Audit-{project_id}", body)
