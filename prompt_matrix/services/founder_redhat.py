"""Adversarial Red-Hat audit for founder workbench runs."""

from __future__ import annotations

import json
import re
from typing import Any

try:
    from ..cost_governance import CostGovernor, TaskType
    from ..db.redhat_findings_repository import insert_findings, list_findings_for_run
    from ..db.runs_repository import fetch_run
    from ..models.jdf import flatten_nodes
except ImportError:
    from cost_governance import CostGovernor, TaskType
    from db.redhat_findings_repository import insert_findings, list_findings_for_run
    from db.runs_repository import fetch_run
    from models.jdf import flatten_nodes

_OPPOSITE_MODEL = {
    "gemini": "anthropic/claude-sonnet-4-5",
    "claude": "gemini/gemini-2.0-flash",
}


def adversarial_model_for(original: str) -> str:
    key = (original or "gemini").strip().lower()
    return _OPPOSITE_MODEL.get(key, "anthropic/claude-sonnet-4-5")


def _run_text(run: dict[str, Any]) -> str:
    parts = [str(run.get("directive") or "")]
    content = run.get("content") or {}
    for node in flatten_nodes(content):
        if node.get("type") == "paragraph" and node.get("content"):
            parts.append(str(node["content"]))
    for lock in run.get("extracted_locks") or []:
        key = lock.get("canonical_key") or lock.get("metric")
        val = lock.get("value")
        if key is not None and val is not None:
            parts.append(f"{key} = {val}")
    return "\n\n".join(p for p in parts if p.strip())[:8000]


def _parse_findings(raw: str) -> list[dict[str, Any]]:
    text = (raw or "").strip()
    if not text:
        return []
    match = re.search(r"\[[\s\S]*\]", text)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, list):
                return [f for f in data if isinstance(f, dict)]
        except json.JSONDecodeError:
            pass
    return [
        {
            "title": "Red-Hat review",
            "content": text,
            "severity": "medium",
            "suggested_fix": "",
            "highlight_text": "",
        }
    ]


def run_adversarial_redhat(
    run_id: str,
    *,
    workspace_id: str | None = None,
    model_override: str | None = None,
) -> list[dict[str, Any]]:
    """Run adversarial audit with opposite model; persist findings."""
    run = fetch_run(run_id)
    if not run:
        raise ValueError("run not found")
    ws = workspace_id or run.get("workspace_id") or "default"
    body = _run_text(run)
    if not body.strip():
        return []

    adv_model = model_override or adversarial_model_for(str(run.get("model") or "gemini"))
    prompt = (
        "Adversarial red-team review. Return ONLY a JSON array. Each object must have: "
        'title, content, severity (high|medium|low), suggested_fix, highlight (short phrase from the text).\n\n'
        f"Original model: {run.get('model')}\n\nDocument:\n{body}"
    )
    messages = [{"role": "user", "content": prompt}]

    gov = CostGovernor()
    policy = gov.preflight(ws, TaskType.REDHAT, messages)
    executor = gov.executor or gov._default_executor  # noqa: SLF001
    raw, _in_t, _out_t = executor(
        adv_model,
        messages,
        policy.max_output_tokens,
        policy.caching,
    )
    findings = _parse_findings(raw or "")
    return insert_findings(run_id, findings, model_used=adv_model)


def get_run_with_findings(run_id: str) -> dict[str, Any] | None:
    run = fetch_run(run_id)
    if not run:
        return None
    run["redhat_findings"] = list_findings_for_run(run_id)
    return run
