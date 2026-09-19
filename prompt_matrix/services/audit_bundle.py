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
    from ..exporters.text_ast import jdf_to_html
    from .source_carry import source_carry_for
except ImportError:
    from db.document_lock_repository import latest_lock
    from db.sign_off_repository import list_sign_offs
    from exporters.text_ast import jdf_to_html
    from source_carry import source_carry_for


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


def _opt_int(value: Any) -> int | None:
    """An optional count: None when the gate never stored it, so the report can
    print only the rows this document actually has."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_gate(g: dict[str, Any], tree: dict[str, Any] | None = None) -> dict[str, Any]:
    stats = g.get("provenance_stats") or g
    eligible = int(stats.get("eligible") or 0)
    anchored = int(stats.get("anchored") or 0)
    supported = int(stats.get("supported") or 0)
    partial = int(stats.get("partial") or 0)
    unsupported = int(stats.get("unsupported") or 0)
    unverified_claims = int(stats.get("unverified") or 0)
    unanchored = int(stats.get("unanchored") or (eligible - anchored))
    gate = {
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
        # The Math Check's own numbers. `_read_persisted_gate` feeds the export
        # report, whose "Metrics checked" row read `gate.get("metrics_checked")`
        # while this whitelist dropped the field on the way in — so the row was
        # silently absent on every exported document. These are carried through
        # verbatim now, and the tier counts come with them so the report can say
        # what was checked and what was not.
        "metrics_checked": _opt_int(g.get("metrics_checked")),
        "locks_verified": _opt_int(g.get("locks_verified")),
        "locks_rejected": _opt_int(g.get("locks_rejected")),
        "verified": _opt_int(g.get("verified")),
        "violated": _opt_int(g.get("violated")),
        "checked_by_value": _opt_int(g.get("checked_by_value")),
        "checked_by_relational": _opt_int(g.get("checked_by_relational")),
        "z3_unverified": _opt_int(g.get("z3_unverified")),
        "z3_unverified_reason": str(g.get("z3_unverified_reason") or ""),
        "z3_version": str(g.get("z3_version") or ""),
        "violations": [str(v) for v in (g.get("violations") or [])],
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
    if isinstance(tree, dict):
        _recount_gate_provenance(gate, tree)
    return gate


def _recount_gate_provenance(gate: dict[str, Any], tree: dict[str, Any]) -> None:
    """Replace a gate's provenance counters with the tree's, in place.

    A persisted gate describes the compile that wrote it, and the tree in hand
    may be a later revision — so every stats number shown beside that tree is
    recounted from it. ``services.audit_summary._provenance_counts`` is THE
    counter and ``provenance_gate_fields`` its only writer; there ``supported``
    counts a verdict of ``yes`` OR ``partial``, because a claim the sentences it
    cites carry in part, with nothing in them contradicting it, is grounded.
    The gate's flat copies of the counters are synced from the same stats dict so
    the two cannot disagree.
    """
    try:
        from ..services.audit_summary import provenance_gate_fields
    except ImportError:
        from services.audit_summary import provenance_gate_fields
    layer = provenance_gate_fields(
        document=tree,
        z3_status=str(gate.get("z3_status") or "SKIPPED"),
        redhat_count=int(gate.get("redhat_count") or 0),
        has_substrate=bool(gate.get("has_substrate")),
    )
    # The recount's verdict replaces the persisted one outright, refusal
    # included: a gate that read "unverified" under the old counting must not
    # keep refusing once the recount finds the claims supported.
    gate["unverified"] = bool(layer.get("unverified"))
    gate["unverified_reason"] = str(layer.get("unverified_reason") or "")
    gate["gate_status"] = layer["gate_status"]
    stats = layer["provenance_stats"]
    gate["provenance_stats"] = stats
    gate.update(
        eligible=stats["eligible"],
        anchored=stats["anchored"],
        supported=stats["supported"],
        unsupported=stats["unsupported"],
        unanchored=stats["unanchored"],
    )


def _read_persisted_gate(
    project_id: str, tree: dict[str, Any] | None = None
) -> dict[str, Any] | None:
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
        return _normalize_gate(gate, tree) if isinstance(gate, dict) else None
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
    # fallback for revisions (pre-fix docs) without a persisted block. The
    # persisted block supplies the Math Check's own numbers and the Red-Hat
    # count; its provenance counters are recounted against `tree` on the way in
    # (`_normalize_gate`), because the tree being exported may be newer than the
    # compile that wrote the block.
    persisted = _read_persisted_gate(project_id, tree)
    if persisted is not None:
        return persisted
    try:
        from ..services.audit_summary import provenance_gate_fields
    except ImportError:
        from services.audit_summary import provenance_gate_fields
    try:
        from ..db.substrate_repository import list_substrate_for_project
    except ImportError:
        from db.substrate_repository import list_substrate_for_project

    z3_status = str((z3_results or {}).get("status") or _derive_z3_from_tree(tree) or "SKIPPED")
    redhat_count = (
        len(redhat_critiques) if redhat_critiques is not None else _derive_redhat_count(tree)
    )
    has_substrate = bool(list_substrate_for_project(project_id))

    # Same provenance layer as the compile (`build_audit_summary`): `anchored` is
    # the grounding, `supported` is the verdict — `yes` or `partial`, a claim the
    # sentences it cites carry in part with nothing contradicting it, which is
    # grounded. No persisted block here, so the tree is the only source.
    layer = provenance_gate_fields(
        document=tree,
        z3_status=z3_status,
        redhat_count=redhat_count,
        has_substrate=has_substrate,
    )
    stats = layer["provenance_stats"]
    return {
        "gate_status": layer["gate_status"],
        "z3_status": z3_status,
        "redhat_count": redhat_count,
        "unverified": bool(layer.get("unverified")),
        "unverified_reason": str(layer.get("unverified_reason") or ""),
        "eligible": stats["eligible"],
        "anchored": stats["anchored"],
        "supported": stats["supported"],
        "unsupported": stats["unsupported"],
        "unanchored": stats["unanchored"],
        "has_substrate": has_substrate,
        "provenance_stats": stats,
    }


def _node_label(node: dict[str, Any], limit: int = 60) -> str:
    raw = str(node.get("title") or node.get("content") or "").strip()
    return raw[:limit]


def _tree_redhat_items(tree: dict[str, Any]) -> list[dict[str, Any]]:
    """Every critique the document itself carries, in document order.

    A node-scoped Red-Hat audit writes into ``annotations.redhat`` on its target
    node (``models/jdf.attach_redhat_annotation``), so these are the findings that
    belong to the document rather than to a run.
    """
    items: list[dict[str, Any]] = []
    for node in _walk_nodes(tree):
        for annotation in (node.get("annotations") or {}).get("redhat") or []:
            if not isinstance(annotation, dict):
                continue
            text = str(annotation.get("text") or annotation.get("content") or "").strip()
            if not text:
                continue
            items.append(
                {
                    "severity": str(annotation.get("severity") or "info"),
                    "title": str(annotation.get("title") or "") or _node_label(node),
                    "message": text,
                    "status": str(annotation.get("status") or "open"),
                    "node_id": str(annotation.get("node_id") or node.get("id") or ""),
                    "run_id": "",
                    "source": "annotations.redhat",
                }
            )
    return items


def _run_redhat_items(project_id: str) -> list[dict[str, Any]]:
    """The project's persisted findings (``redhat_findings``, keyed by workspace).

    The run/founder path stores findings here against a workspace id; for a
    project that workspace is the project id (``routers/redhat_routes.py``).
    """
    try:
        from ..db.redhat_findings_repository import list_findings_for_workspace
    except ImportError:
        from db.redhat_findings_repository import list_findings_for_workspace
    try:
        rows = list_findings_for_workspace(project_id)
    except Exception:
        return []
    items: list[dict[str, Any]] = []
    for row in rows:
        message = str(row.get("content") or "").strip()
        if not message:
            continue
        items.append(
            {
                "severity": str(row.get("severity") or "info"),
                "title": str(row.get("title") or ""),
                "message": message,
                "status": str(row.get("status") or "open"),
                "node_id": "",
                "run_id": str(row.get("run_id") or ""),
                "source": "redhat_findings",
            }
        )
    return items


def _redhat_pass_recorded(project_id: str) -> bool:
    """Whether any Red-Hat pass left a record for this project, findings or not.

    A pass that ran and found nothing and a pass that never ran are different
    facts and the report has to say which it is; this is the evidence that
    separates them. Three records count, because the three paths that run a pass
    leave different ones: ``routers/draft.py`` writes ``DRAFT_STREAM_REDHAT`` to
    the audit log, the run path writes ``redhat_audit_telemetry``, and a
    node-scoped audit saves the revision it annotated
    (``mutation_type="redhat_audit"``) — which was the record a project held when
    neither of the other two was written, and the one that made "no pass is
    recorded" false.
    """
    try:
        from ..db.connection import init_db
        from ..db.jdf_repository import list_jdf_revisions
        from ..db.redhat_telemetry_repository import fetch_telemetry
        from ..history import get_db
    except ImportError:
        from db.connection import init_db
        from db.jdf_repository import list_jdf_revisions
        from db.redhat_telemetry_repository import fetch_telemetry
        from history import get_db
    try:
        init_db()
        if fetch_telemetry(project_id):
            return True
        row = get_db().execute(
            "SELECT 1 FROM audit_log WHERE project_id = ? AND success = 1 "
            "AND action LIKE '%REDHAT%' LIMIT 1",
            (project_id,),
        ).fetchone()
        if row is not None:
            return True
        return any(
            str(revision.get("mutation_type") or "") == "redhat_audit"
            for revision in list_jdf_revisions(project_id, limit=_REDHAT_REVISION_SCAN)
        )
    except Exception:
        return False


def _redhat_skip_reason(project_id: str) -> str:
    """Why no run is recorded, in the compile's own words when it left them."""
    try:
        from ..db.connection import init_db
        from ..history import get_db
    except ImportError:
        from db.connection import init_db
        from history import get_db
    try:
        init_db()
        row = get_db().execute(
            "SELECT last_compiled_json FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        data = json.loads(row[0]) if (row and row[0]) else {}
        payload = ((data or {}).get("gate") or {}).get("redhat")
        if isinstance(payload, dict):
            status = str(payload.get("status") or "")
            if status in ("failed", "error"):
                return f"The Red-Hat pass failed: {payload.get('error') or 'no reason recorded'}."
            reason = str(payload.get("skip_reason") or "").strip()
            if reason:
                return f"{reason}."
    except Exception:
        pass
    return "Red-Hat has not been run for this project."


#: How many revisions back the Red-Hat section reads a stored tree. Bounded on
#: purpose: only the revisions that can hold an annotation are read (see
#: ``_revision_redhat_items``), and this section is a list of findings, not a
#: history browser.
_REDHAT_REVISION_SCAN = 20


def _revision_redhat_items(
    project_id: str, seen: set[tuple[str, str]]
) -> list[dict[str, Any]]:
    """Findings recorded in the project's revisions, tagged with the revision.

    Two sets of revisions are read, both bounded, and neither is the whole
    history: the newest revision (the document as it stands, when that is not the
    tree in hand) and the revisions a Red-Hat audit saved — a node-scoped audit
    writes its critique into the revision it saves under
    ``mutation_type="redhat_audit"`` (``routers/draft.py``), and the next compile
    replaces that tree. Measured on the box: ``default`` holds four findings in
    revision 4 while its newest revision carries none, so an export of the newest
    revision said "Red-Hat ran for this document and recorded no findings" — the
    sentence a regulator reads — about a project that holds the finding.
    ``seen`` is what the caller already has, so a finding the tree in hand or a
    ``redhat_findings`` row already carries is not listed twice.
    """
    try:
        from ..db.jdf_repository import fetch_jdf_at_version, list_jdf_revisions
    except ImportError:
        from db.jdf_repository import fetch_jdf_at_version, list_jdf_revisions
    out: list[dict[str, Any]] = []
    try:
        revisions = list_jdf_revisions(project_id, limit=100)
    except Exception:
        return out
    if not revisions:
        return out
    latest_version = int(revisions[0]["version"])
    audited = [
        revision
        for revision in revisions
        if "redhat" in str(revision.get("mutation_type") or "").lower()
    ][:_REDHAT_REVISION_SCAN]
    wanted = {latest_version} | {int(revision["version"]) for revision in audited}
    for version in sorted(wanted, reverse=True):
        try:
            tree = fetch_jdf_at_version(project_id, version)
        except Exception:
            continue
        if not isinstance(tree, dict):
            continue
        for item in _tree_redhat_items(tree):
            key = (str(item.get("node_id") or ""), str(item.get("message") or ""))
            if key in seen:
                continue
            seen.add(key)
            item["revision"] = version
            out.append(item)
    return out


def project_redhat_findings(project_id: str, tree: dict[str, Any]) -> dict[str, Any]:
    """This project's Red-Hat findings, and whether Red-Hat has run at all.

    The export's Red-Hat section was empty on every document because it rendered
    only its ``redhat_critiques`` argument and no caller passed one
    (``routers/export_routes.py`` calls the builders with ``(project_id, tree)``).
    The findings that do exist are read here instead: the document's own
    ``annotations.redhat``, the project's ``redhat_findings`` rows, and — because a
    later compile replaces the tree a node-scoped audit wrote into — the project's
    revisions (``_revision_redhat_items``), each tagged with the revision holding
    it. ``ran`` is reported separately so a section with nothing to list can say
    whether a pass happened and found nothing or never happened; and it is only
    reachable as "found nothing" once no revision holds a finding either.

    ``other_revisions`` names the revisions a listed finding came from when the
    tree in hand carries none of its own (``in_export``), which is what the section
    states beside the list.
    """
    tree_items = _tree_redhat_items(tree)
    items = tree_items + _run_redhat_items(project_id)
    seen = {(str(i.get("node_id") or ""), str(i.get("message") or "")) for i in items}
    carried = _revision_redhat_items(project_id, seen)
    items = items + carried
    other_revisions = sorted({int(i["revision"]) for i in carried})
    if items:
        return {
            "items": items,
            "count": len(items),
            "ran": True,
            "reason": "",
            "other_revisions": other_revisions,
            "in_export": bool(tree_items),
        }
    if _redhat_pass_recorded(project_id):
        return {
            "items": [],
            "count": 0,
            "ran": True,
            "reason": "",
            "other_revisions": [],
            "in_export": False,
        }
    return {
        "items": [],
        "count": 0,
        "ran": False,
        "reason": _redhat_skip_reason(project_id),
        "other_revisions": [],
        "in_export": False,
    }


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

    # The Red-Hat section's own data. An explicit list is a caller stating the
    # findings; with none, the project's real ones are read (the document's
    # `annotations.redhat` and the `redhat_findings` rows), because the section
    # used to render only the argument and every export called it without one.
    if redhat_critiques is None:
        redhat_view = project_redhat_findings(project_id, tree)
    else:
        redhat_view = {
            "items": [
                {
                    "severity": str(item.get("severity") or "info"),
                    "title": str(item.get("title") or ""),
                    "message": str(
                        item.get("message") or item.get("critique") or item.get("content") or ""
                    ),
                    "status": str(item.get("status") or "open"),
                    "node_id": str(item.get("node_id") or ""),
                    "run_id": str(item.get("run_id") or ""),
                    "source": str(item.get("source") or "caller"),
                }
                for item in redhat_critiques
                if isinstance(item, dict)
            ],
            "count": len(redhat_critiques),
            "ran": bool(redhat_critiques),
            "reason": "",
        }

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
    # fallback recompute only for revisions without a persisted gate block. The
    # tree is handed in so the persisted block's provenance counters are
    # recounted from the document this report actually renders.
    persisted_gate = _read_persisted_gate(project_id, tree)
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

    # FIX 3 — Verification Gate: always populated, never empty. The counters come
    # from the persisted block when there is one, and from the same counter the
    # compile runs (recounted against the document this report renders) when there
    # is not — a pre-fix revision still reports its gate and its counts.
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
    if not gate_has_block:
        gate_html += (
            "\n<p>No gate block was persisted at compile time; the counts above are "
            "recounted from the document in this report.</p>"
        )

    # FIX 2 — Z3 Verification Results: gate-level status, not a confidence span
    # table. Lock / metric / violation detail is rendered only when the persisted
    # gate actually carries it.
    if not gate_has_block:
        z3_render = "<p>No Z3 run recorded for this document.</p>"
    else:
        z3_status = gate.get("z3_status") or "N/A"
        z3_render = f"<p><strong>Status:</strong> {_esc(str(z3_status))}</p>"
        z3_metrics = [
            ("Locks verified", gate.get("locks_verified")),
            ("Locks rejected", gate.get("locks_rejected")),
            ("Numbers checked", gate.get("metrics_checked")),
            ("Checked by value", gate.get("checked_by_value")),
            ("Checked by relationship", gate.get("checked_by_relational")),
            ("Verified", gate.get("verified")),
            ("Violated", gate.get("violated")),
            ("Unverified", gate.get("z3_unverified")),
        ]
        present = [(label, val) for label, val in z3_metrics if val is not None]
        if present:
            z3_render += (
                "\n<ul>\n"
                + "\n".join(f"<li>{_esc(label)}: {_esc(str(val))}</li>" for label, val in present)
                + "\n</ul>"
            )
        unverified_why = gate.get("z3_unverified_reason") or ""
        if unverified_why:
            z3_render += f"\n<p>Not checked: {_esc(unverified_why)}</p>"
        violations = gate.get("violations") or []
        if violations:
            z3_render += (
                "\n<ul>\n" + "\n".join(f"<li>{_esc(str(v))}</li>" for v in violations) + "\n</ul>"
            )

    redhat_html = ""
    for item in redhat_view["items"][:20]:
        where = str(item.get("node_id") or item.get("run_id") or "")
        revision = item.get("revision")
        if revision:
            # A finding the document in this export does not carry: it is recorded
            # on the revision that holds it, and the reader is told which.
            where = f"{where} · recorded on revision {int(revision)}" if where else (
                f"recorded on revision {int(revision)}"
            )
        placement = f" <span class='meta'>[{_esc(where)}]</span>" if where else ""
        redhat_html += (
            f"<li><strong>{_esc(str(item.get('severity') or 'info'))}</strong>: "
            f"{_esc(str(item.get('message') or ''))}{placement}</li>"
        )
    if redhat_view["count"] > 20:
        redhat_html += f"<li>… and {redhat_view['count'] - 20} more recorded finding(s).</li>"
    other_revisions = redhat_view.get("other_revisions") or []
    if other_revisions and not redhat_view.get("in_export"):
        # The findings above are real and recorded; they are not in the document
        # this report renders. Saying so is the difference between "recorded on an
        # earlier revision" and the denial this section used to make.
        listed = ", ".join(str(int(v)) for v in other_revisions)
        redhat_html += (
            f"<li>Recorded on revision {_esc(listed)} of this document; the version in "
            "this export does not carry them.</li>"
        )
    if not redhat_html:
        if redhat_view["ran"]:
            redhat_html = "<li>Red-Hat ran for this document and recorded no findings.</li>"
        elif redhat_view["reason"]:
            reason = str(redhat_view["reason"])
            # The compile records its skip reason as a clause ("no Red-Hat audit
            # was requested for this compile."), so it is set as its own sentence.
            reason = reason[:1].upper() + reason[1:]
            redhat_html = (
                "<li>No Red-Hat critique is recorded for this document. "
                f"{_esc(reason)}</li>"
            )
        else:
            redhat_html = "<li>No Red-Hat critique is recorded for this document.</li>"

    # What the compile carried of the sources it was handed. The prompt cannot
    # hold every attachment, and nothing said which ones it left behind: the
    # dossier is where a client reads coverage, so the counts and the names of the
    # sources that did not reach the model are stated beside every other number.
    carry = source_carry_for(project_id)
    carry_html = ""
    if carry.get("attached"):
        dropped = [item for item in carry.get("sources") or [] if not item.get("included")]
        carry_html = (
            f"<p>Sources carried into the model: "
            f"<strong>{int(carry.get('carried') or 0)} of {int(carry.get('attached') or 0)}</strong> "
            f"({int(carry.get('carried_chars') or 0):,} of {int(carry.get('limit_chars') or 0):,} "
            "characters)."
        )
        if dropped:
            listed = dropped[:12]
            carry_html += " Not carried:</p>\n<ul>\n" + "\n".join(
                f"<li>{_esc(str(item.get('filename') or ''))} — "
                f"{_esc(str(item.get('dropped_reason') or ''))}</li>"
                for item in listed
            ) + "\n</ul>"
            if len(dropped) > len(listed):
                carry_html += f"<p>… and {len(dropped) - len(listed)} more.</p>"
        else:
            carry_html += "</p>"
        if carry.get("derived"):
            carry_html += (
                '<p class="meta">This compile did not record which sources it carried; '
                "the counts are recomputed from the sources in the vault.</p>"
            )

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
Red-Hat items: <strong>{redhat_view['count'] if redhat_view['ran'] else 'not run'}</strong>.
Sign-offs: <strong>{len(sign_offs)}</strong>.</p>
{carry_html}

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
        from ..exporters.pdf_ast import (
            _pdf_via_playwright,
            _pdf_via_weasyprint,
            pdf_bytes_from_html,
        )
    except ImportError:
        from exporters.pdf_ast import (
            _pdf_via_playwright,
            _pdf_via_weasyprint,
            pdf_bytes_from_html,
        )

    pdf = _pdf_via_weasyprint(html_body)
    if pdf:
        return pdf
    pdf = _pdf_via_playwright(html_body)
    if pdf:
        return pdf
    # No HTML engine here. Measured on the deployment box: its venv carries
    # neither WeasyPrint nor Playwright, so both probes return None (and neither
    # is installable from the repo — see exporters/pdf_ast.py). The bundle's
    # sections still travel: this is the same HTML just built, reduced to
    # paginated text, so the gate, the counters, the Z3 rows, the Red-Hat
    # findings, the sign-offs, the lock hash, the appendix and the document body
    # all reach the file. The engines that could not be used are named on page 1.
    title = str((tree.get("meta") or {}).get("title") or project_id)
    return pdf_bytes_from_html(html_body, title=f"Compliance Audit Report - {title}")
