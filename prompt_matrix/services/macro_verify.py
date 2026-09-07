"""Cross-run contradiction detection for the founder workbench."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Literal

try:
    from ..db.runs_repository import fetch_run, list_runs
    from ..models.jdf import flatten_nodes
except ImportError:
    from db.runs_repository import fetch_run, list_runs
    from models.jdf import flatten_nodes

ConflictType = Literal["numeric", "temporal", "logical"]
Severity = Literal["high", "medium", "low"]

_NUMBER = re.compile(r"\b(\d+(?:\.\d+)?)\b")
_YEAR = re.compile(r"\b((?:19|20)\d{2})\b")


@dataclass
class Conflict:
    claim_a: str
    claim_b: str
    conflict_type: ConflictType
    severity: Severity
    run_id_a: str = ""
    run_id_b: str = ""


def _extract_claims(run: dict[str, Any]) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    content = run.get("content") or {}
    for node in flatten_nodes(content):
        if node.get("type") != "paragraph":
            continue
        text = str(node.get("content") or "").strip()
        if text:
            claims.append({"text": text, "run_id": run["id"]})
    for lock in run.get("extracted_locks") or []:
        key = str(lock.get("canonical_key") or lock.get("metric") or "").strip()
        val = lock.get("value")
        if key and val is not None:
            claims.append(
                {
                    "text": f"{key} = {val}",
                    "run_id": run["id"],
                    "metric": key,
                    "value": float(val) if _is_float(val) else None,
                }
            )
    return claims


def _is_float(v: Any) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def _metric_values(claims: list[dict[str, Any]]) -> dict[str, list[tuple[str, float, str]]]:
    """Map metric key -> [(run_id, value, claim_text), ...]."""
    out: dict[str, list[tuple[str, float, str]]] = {}
    for c in claims:
        if c.get("metric") and c.get("value") is not None:
            key = str(c["metric"]).lower()
            out.setdefault(key, []).append((c["run_id"], float(c["value"]), c["text"]))
        for num in _NUMBER.findall(c.get("text") or ""):
            try:
                val = float(num)
            except ValueError:
                continue
            key = f"number:{num}"
            out.setdefault(key, []).append((c["run_id"], val, c["text"]))
    return out


def _temporal_conflicts(claims: list[dict[str, Any]]) -> list[Conflict]:
    by_run: dict[str, set[str]] = {}
    for c in claims:
        years = _YEAR.findall(c.get("text") or "")
        if years:
            by_run.setdefault(c["run_id"], set()).update(years)
    conflicts: list[Conflict] = []
    run_ids = list(by_run.keys())
    for i, ra in enumerate(run_ids):
        for rb in run_ids[i + 1 :]:
            only_a = by_run[ra] - by_run[rb]
            only_b = by_run[rb] - by_run[ra]
            if only_a and only_b:
                conflicts.append(
                    Conflict(
                        claim_a=f"Years {sorted(by_run[ra])} (run {ra})",
                        claim_b=f"Years {sorted(by_run[rb])} (run {rb})",
                        conflict_type="temporal",
                        severity="medium",
                        run_id_a=ra,
                        run_id_b=rb,
                    )
                )
    return conflicts


def detect_cross_run_contradictions(run_ids: list[str]) -> list[dict[str, Any]]:
    """Compare claims across runs; return serializable conflict dicts."""
    runs: list[dict[str, Any]] = []
    for rid in run_ids:
        row = fetch_run(rid)
        if row:
            runs.append(row)
    if len(runs) < 2:
        return []

    all_claims: list[dict[str, Any]] = []
    for run in runs:
        all_claims.extend(_extract_claims(run))

    conflicts: list[Conflict] = []

    metrics = _metric_values(all_claims)
    for _key, entries in metrics.items():
        if len(entries) < 2:
            continue
        values = {(e[0], e[1]) for e in entries}
        distinct_vals = {v for _, v in values}
        if len(distinct_vals) > 1:
            a = entries[0]
            b = next(e for e in entries[1:] if e[1] != a[1])
            conflicts.append(
                Conflict(
                    claim_a=a[2],
                    claim_b=b[2],
                    conflict_type="numeric",
                    severity="high",
                    run_id_a=a[0],
                    run_id_b=b[0],
                )
            )

    conflicts.extend(_temporal_conflicts(all_claims))

    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, Any]] = []
    for c in conflicts:
        sig = (c.run_id_a, c.run_id_b, c.conflict_type)
        if sig in seen:
            continue
        seen.add(sig)
        out.append(asdict(c))
    return out


def contradictions_for_run(
    run_id: str,
    *,
    workspace_id: str | None = None,
    compare_run_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Conflicts between *run_id* and peer runs in the workspace."""
    target = fetch_run(run_id)
    if not target:
        return []
    peers = compare_run_ids or []
    if not peers:
        ws = workspace_id or target.get("workspace_id") or "default"
        peers = [r["id"] for r in list_runs(workspace_id=ws) if r["id"] != run_id]
    ids = [run_id] + [p for p in peers if p != run_id]
    return detect_cross_run_contradictions(ids)
