"""Full-context scan of the Main document AST — Z3 + Red-Hat heuristics."""

from __future__ import annotations

import copy
import re
from typing import Any

try:
    from ..ledger.truth_engine import TruthLedgerEngine
    from ..lib.sanitize import sanitize_jdf_node
    from ..models.jdf import flatten_nodes, parse_document
    from ..routers.inquire_stream import _parse_metrics
except ImportError:
    from ledger.truth_engine import TruthLedgerEngine
    from lib.sanitize import sanitize_jdf_node
    from models.jdf import flatten_nodes, parse_document
    from routers.inquire_stream import _parse_metrics

_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}
_DOLLAR_RE = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)")
_LOCK_TOKEN_RE = re.compile(r"\[🔒 #\d+\]")


def _node_visible_text(node: dict[str, Any]) -> str:
    ntype = str(node.get("type") or "")
    if ntype == "callout":
        title = str(node.get("title") or "").strip()
        content = str(node.get("content") or "")
        return f"{title}\n{content}".strip() if title else content
    if ntype == "table":
        parts = [str(node.get("caption") or "")]
        for row in node.get("rows") or []:
            parts.extend(str(cell) for cell in row)
        return "\n".join(p for p in parts if p.strip())
    return str(node.get("content") or "")


def _parse_dollar_amounts(text: str) -> list[float]:
    amounts: list[float] = []
    for match in _DOLLAR_RE.finditer(text or ""):
        raw = match.group(1).replace(",", "")
        try:
            amounts.append(float(raw))
        except ValueError:
            continue
    return amounts


def _human_key_label(key: str) -> str:
    cleaned = str(key or "").replace("_", " ").strip()
    if not cleaned:
        return "baseline"
    return cleaned.title()


def _issue(
    issues: list[dict[str, Any]],
    *,
    severity: str,
    category: str,
    description: str,
    node_id: str | None = None,
) -> None:
    desc = str(description or "").strip()
    if not desc:
        return
    for existing in issues:
        if (
            existing.get("category") == category
            and existing.get("description") == desc
            and existing.get("node_id") == node_id
        ):
            return
    idx = len(issues) + 1
    row: dict[str, Any] = {
        "id": f"issue-{idx:02d}",
        "severity": severity if severity in _SEVERITY_RANK else "medium",
        "category": category,
        "description": desc,
    }
    if node_id:
        row["node_id"] = node_id
    issues.append(row)


def _scan_z3_document(
    issues: list[dict[str, Any]],
    *,
    document: dict[str, Any],
    full_text: str,
) -> None:
    engine = TruthLedgerEngine()
    try:
        engine.load_from_document(document)
        ledger = document.get("truth_ledger") or {}
        for key, raw in ledger.items():
            try:
                engine.lock_metric(str(key), float(raw))
            except (TypeError, ValueError):
                continue

        metrics = _parse_metrics(full_text)
        if metrics:
            ok, violations = engine.validate_entities(metrics)
            if not ok:
                for msg in violations:
                    _issue(
                        issues,
                        severity="high",
                        category="Z3 Contradiction",
                        description=str(msg),
                    )

        for node in flatten_nodes(document):
            node_id = str(node.get("id") or "")
            text = _node_visible_text(node)
            entity_values: dict[str, float] = {}
            for key, val in _parse_metrics(text):
                entity_values[key] = val
            ok, msg = engine.verify_node(node, metric_values=entity_values)
            if not ok and msg:
                _issue(
                    issues,
                    severity="high",
                    category="Z3 Contradiction",
                    description=str(msg),
                    node_id=node_id or None,
                )

            for ann in (node.get("annotations") or {}).get("z3") or []:
                if str(ann.get("status") or "") != "violation":
                    continue
                message = str(ann.get("message") or "Z3 constraint violation").strip()
                _issue(
                    issues,
                    severity="high",
                    category="Z3 Contradiction",
                    description=message,
                    node_id=node_id or None,
                )
    finally:
        engine.close()


def _scan_redhat_annotations(issues: list[dict[str, Any]], document: dict[str, Any]) -> None:
    for node in flatten_nodes(document):
        node_id = str(node.get("id") or "")
        for ann in (node.get("annotations") or {}).get("redhat") or []:
            if str(ann.get("status") or "open") != "open":
                continue
            text = str(ann.get("text") or "").strip()
            if not text:
                continue
            lowered = text.lower()
            severity = (
                "high"
                if any(w in lowered for w in ("hallucin", "unsupported", "contradict"))
                else "medium"
            )
            _issue(
                issues,
                severity=severity,
                category="Red-Hat Finding",
                description=text,
                node_id=node_id or None,
            )


def _scan_unverified_numbers(issues: list[dict[str, Any]], document: dict[str, Any]) -> None:
    ledger = document.get("truth_ledger") or {}
    baseline_entries: list[tuple[str, float]] = []
    for key, raw in ledger.items():
        try:
            baseline_entries.append((str(key), float(raw)))
        except (TypeError, ValueError):
            continue

    for node in flatten_nodes(document):
        node_id = str(node.get("id") or "")
        text = _node_visible_text(node)
        if not text.strip():
            continue
        amounts = _parse_dollar_amounts(text)
        if not amounts:
            continue

        meta = node.get("meta") or {}
        lock_pills = meta.get("lock_pills") or []
        has_lock = bool(lock_pills) or bool(_LOCK_TOKEN_RE.search(text))
        has_provenance = bool(node.get("provenance"))

        if not has_lock:
            largest = max(amounts)
            formatted = f"${largest:,.0f}".replace(".00", "")
            if not has_provenance:
                _issue(
                    issues,
                    severity="high",
                    category="Unverified Number",
                    description=(
                        f"Numeric claim {formatted} in the Main document has no verified lock pill "
                        "or substrate citation."
                    ),
                    node_id=node_id or None,
                )
            else:
                _issue(
                    issues,
                    severity="medium",
                    category="Unverified Number",
                    description=(
                        f"Numeric claim {formatted} is cited but not bound to a verified lock pill."
                    ),
                    node_id=node_id or None,
                )

        for baseline_key, baseline_val in baseline_entries:
            for amount in amounts:
                if amount <= baseline_val:
                    continue
                delta = amount - baseline_val
                label = _human_key_label(baseline_key)
                _issue(
                    issues,
                    severity="high",
                    category="Unverified Number",
                    description=(
                        f"Aggregate liability limit exceeds {label} by " f"${delta:,.0f}."
                    ),
                    node_id=node_id or None,
                )


def _scan_missing_citations(issues: list[dict[str, Any]], document: dict[str, Any]) -> None:
    for node in flatten_nodes(document):
        if str(node.get("type") or "") != "paragraph":
            continue
        node_id = str(node.get("id") or "")
        text = _node_visible_text(node)
        if not re.search(r"\d", text):
            continue
        meta = node.get("meta") or {}
        if meta.get("lock_pills") or _LOCK_TOKEN_RE.search(text):
            continue
        if node.get("provenance"):
            continue
        snippet = text.strip()
        if len(snippet) > 120:
            snippet = snippet[:117] + "..."
        _issue(
            issues,
            severity="medium",
            category="Missing Citation",
            description=f'Paragraph contains verifiable numbers but no citation: "{snippet}"',
            node_id=node_id or None,
        )


def _renumber_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(
        issues,
        key=lambda row: (
            _SEVERITY_RANK.get(str(row.get("severity") or "medium"), 9),
            row.get("id") or "",
        ),
    )
    out: list[dict[str, Any]] = []
    for idx, row in enumerate(ranked, start=1):
        item = dict(row)
        item["id"] = f"issue-{idx:02d}"
        out.append(item)
    return out


def run_full_context_scan(document: dict[str, Any]) -> dict[str, Any]:
    """Scan all AST nodes with Z3 ledger checks and Red-Hat annotation review."""
    tree = sanitize_jdf_node(copy.deepcopy(document))
    parse_document(tree)
    nodes = flatten_nodes(tree)
    full_text = "\n\n".join(_node_visible_text(n) for n in nodes if _node_visible_text(n).strip())

    issues: list[dict[str, Any]] = []
    _scan_z3_document(issues, document=tree, full_text=full_text)
    _scan_redhat_annotations(issues, tree)
    _scan_unverified_numbers(issues, tree)
    _scan_missing_citations(issues, tree)
    ranked = _renumber_issues(issues)

    return {
        "status": "success",
        "issues": ranked,
        "issue_count": len(ranked),
        "scanned_nodes": len(nodes),
    }
