"""Multi-pass Red-Hat adversarial audit — cache-first Celery pipeline."""

from __future__ import annotations

import html
import json
import re
import uuid
from typing import Any

from prompt_matrix.celery_app import celery_app

try:
    from prompt_matrix.cost_governance import CostGovernor, ModelPolicy, TaskType
    from prompt_matrix.db.redhat_audit_lock_repository import is_stale, set_active_task
    from prompt_matrix.db.redhat_cache_repository import fetch_cache, save_cache
    from prompt_matrix.db.redhat_findings_repository import insert_findings
    from prompt_matrix.db.runs_repository import list_runs
    from prompt_matrix.lib.ast_diff import get_ast_deltas, hash_block
    from prompt_matrix.services.founder_redhat import _parse_findings
except ImportError:
    from cost_governance import CostGovernor, ModelPolicy, TaskType
    from db.redhat_audit_lock_repository import is_stale, set_active_task
    from db.redhat_cache_repository import fetch_cache, save_cache
    from db.redhat_findings_repository import insert_findings
    from db.runs_repository import list_runs
    from lib.ast_diff import get_ast_deltas, hash_block
    from services.founder_redhat import _parse_findings

PASS1_MODEL = "deepseek/deepseek-chat"
PASS2_MODEL = "anthropic/claude-sonnet-4-5"
HIGH_LIABILITY_KEYWORDS = ("liability", "indemnify", "termination", "$")
SEVERITY_RANK = {"high": 3, "medium": 2, "low": 1}


def _patch_html(suggested_fix: str) -> str:
    fix = (suggested_fix or "").strip()
    if not fix:
        return ""
    return f'<span class="diff-add">{html.escape(fix)}</span>'


def _telemetry_finding(
    finding: dict[str, Any],
    *,
    pass_num: int,
    block_hash: str,
    node_id: str = "",
    cache_hit: bool = False,
) -> dict[str, Any]:
    payload = dict(finding)
    payload.setdefault("id", f"rh_{uuid.uuid4().hex[:12]}")
    payload["pass"] = pass_num
    payload["block_hash"] = block_hash
    payload["node_id"] = node_id
    payload["cache_hit"] = cache_hit
    payload["patch_html"] = _patch_html(str(payload.get("suggested_fix") or ""))
    return payload


def _telemetry_update(project_id: str, **kwargs: Any) -> None:
    try:
        from prompt_matrix.db.redhat_telemetry_repository import upsert_telemetry
    except ImportError:
        from db.redhat_telemetry_repository import upsert_telemetry
    try:
        upsert_telemetry(project_id, **kwargs)
    except Exception:
        # Drawer telemetry is best-effort; never fail the audit pipeline.
        pass


def _resolve_run_id(project_id: str, run_id: str | None) -> str | None:
    if run_id:
        return run_id
    runs = list_runs(workspace_id=project_id, limit=1)
    return runs[0]["id"] if runs else None


def _scrutinizer_prompt(delta: dict[str, Any]) -> str:
    parent = delta.get("parent") or {}
    node = delta.get("node") or {}
    return (
        "Pass 1 Scrutinizer. Scan for surface contradictions, numeric discrepancies, "
        "and missing citations. Return ONLY a JSON array. Each object: "
        'title, content, severity (high|medium|low), suggested_fix, highlight.\n\n'
        f"Change: {delta.get('change')}\n"
        f"Section: {parent.get('section_title') or parent.get('section_id') or 'unknown'}\n"
        f"Block type: {node.get('type')}\n\n"
        f"Text:\n{(delta.get('text') or '')[:6000]}"
    )


def _adversarial_prompt(
    block_hash: str, pass1_findings: list[dict[str, Any]], block_text: str
) -> str:
    return (
        "Pass 2 Adversarial Stress-Test. Cross-examine Pass 1 findings for legal and "
        "logical gaps. Return ONLY a JSON array with the same schema.\n\n"
        f"Block hash: {block_hash}\n\n"
        f"Pass 1 findings:\n{json.dumps(pass1_findings, indent=2)[:4000]}\n\n"
        f"Block text:\n{block_text[:6000]}"
    )


def _invoke_model(
    project_id: str,
    model: str,
    prompt: str,
    *,
    task_type: TaskType = TaskType.REDHAT,
    max_output: int | None = None,
) -> tuple[str, str]:
    gov = CostGovernor()
    messages = [{"role": "user", "content": prompt}]
    policy = gov.preflight(project_id, task_type, messages)
    if model:
        policy = ModelPolicy(
            model_id=model,
            max_input_tokens=policy.max_input_tokens,
            max_output_tokens=max_output or policy.max_output_tokens,
            caching=policy.caching,
            litellm_model=model,
        )
    executor = gov.executor or gov._default_executor  # noqa: SLF001
    raw, _in_t, _out_t = executor(
        model or policy.model_id,
        messages,
        policy.max_output_tokens,
        policy.caching,
    )
    return raw or "", model or policy.model_id


def should_run_pass2(pass1_findings: list[dict[str, Any]], block_text: str) -> bool:
    """Gate Pass 2 on high severity or high-liability keywords."""
    if any(str(f.get("severity") or "").lower() == "high" for f in pass1_findings):
        return True
    lower = (block_text or "").lower()
    return any(keyword in lower for keyword in HIGH_LIABILITY_KEYWORDS)


def synthesize_redhat_findings(
    pass1_findings: list[dict[str, Any]],
    pass2_findings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Pass 3 — local merge, dedupe, severity rank."""
    seen: set[tuple[str, str]] = set()
    merged: list[dict[str, Any]] = []
    for finding in pass1_findings + pass2_findings:
        if not isinstance(finding, dict):
            continue
        key = (
            str(finding.get("title") or "").strip().lower(),
            re.sub(r"\s+", " ", str(finding.get("content") or "").strip().lower())[:240],
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(dict(finding))
    merged.sort(
        key=lambda item: SEVERITY_RANK.get(str(item.get("severity") or "low").lower(), 0),
        reverse=True,
    )
    return merged


def run_redhat_pass1(
    delta_nodes: list[dict[str, Any]],
    project_id: str,
    *,
    run_id: str | None = None,
    generation: int | None = None,
) -> dict[str, Any]:
    """
    Pass 1 Scrutinizer — cache-first block audit.
    Returns per-block pass1 findings and metadata for Pass 2 gating.
    """
    if generation is not None and is_stale(project_id, generation):
        return {"ok": False, "stale": True, "findings": [], "blocks": []}

    resolved_run = _resolve_run_id(project_id, run_id)
    block_results: list[dict[str, Any]] = []
    all_findings: list[dict[str, Any]] = []

    for delta in delta_nodes:
        if delta.get("change") == "deleted":
            continue
        block_hash = str(delta.get("block_hash") or hash_block(delta.get("node") or {}))
        node_id = str(delta.get("node_id") or "")
        cached = fetch_cache(block_hash)
        if cached:
            findings = list(cached.get("findings") or [])
            block_results.append(
                {
                    "block_hash": block_hash,
                    "node_id": node_id,
                    "findings": findings,
                    "pass1_model": cached.get("pass1_model") or PASS1_MODEL,
                    "cached": True,
                    "text": delta.get("text") or "",
                }
            )
            all_findings.extend(findings)
            continue

        if generation is not None and is_stale(project_id, generation):
            return {"ok": False, "stale": True, "findings": all_findings, "blocks": block_results}

        raw, model_used = _invoke_model(
            project_id,
            PASS1_MODEL,
            _scrutinizer_prompt(delta),
            task_type=TaskType.SEMANTIC_VALIDATION,
            max_output=1200,
        )
        findings = _parse_findings(raw)
        save_cache(block_hash, findings, pass1_model=model_used, pass2_model="")
        block_results.append(
            {
                "block_hash": block_hash,
                "node_id": node_id,
                "findings": findings,
                "pass1_model": model_used,
                "cached": False,
                "text": delta.get("text") or "",
            }
        )
        all_findings.extend(findings)

    persisted: list[dict[str, Any]] = []
    if resolved_run and all_findings:
        persisted = insert_findings(resolved_run, synthesize_redhat_findings(all_findings, []))

    return {
        "ok": True,
        "stale": False,
        "run_id": resolved_run,
        "findings": all_findings,
        "blocks": block_results,
        "persisted": persisted,
    }


def run_redhat_pass2(
    block_hash: str,
    pass1_findings: list[dict[str, Any]],
    project_id: str,
    *,
    block_text: str = "",
    run_id: str | None = None,
    pass1_model: str = PASS1_MODEL,
    generation: int | None = None,
) -> dict[str, Any]:
    """Pass 2 Adversarial Stress-Test — conditionally gated deep reasoning."""
    if generation is not None and is_stale(project_id, generation):
        return {"ok": False, "stale": True, "findings": [], "skipped": True}

    if not should_run_pass2(pass1_findings, block_text):
        return {"ok": True, "skipped": True, "findings": [], "pass2_model": ""}

    raw, model_used = _invoke_model(
        project_id,
        PASS2_MODEL,
        _adversarial_prompt(block_hash, pass1_findings, block_text),
        task_type=TaskType.REDHAT,
        max_output=2048,
    )
    findings = _parse_findings(raw)

    cached = fetch_cache(block_hash)
    merged_cache = synthesize_redhat_findings(
        (cached or {}).get("findings") or pass1_findings,
        findings,
    )
    save_cache(
        block_hash,
        merged_cache,
        pass1_model=pass1_model,
        pass2_model=model_used,
    )

    resolved_run = _resolve_run_id(project_id, run_id)
    persisted: list[dict[str, Any]] = []
    if resolved_run and findings:
        persisted = insert_findings(resolved_run, findings, model_used=model_used)

    return {
        "ok": True,
        "skipped": False,
        "findings": findings,
        "pass2_model": model_used,
        "persisted": persisted,
    }


def run_redhat_pass3(
    pass1_blocks: list[dict[str, Any]],
    pass2_results: list[dict[str, Any]],
    *,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    """Pass 3 Synthesizer — merge, dedupe, rank; persist final set."""
    pass1_all: list[dict[str, Any]] = []
    for block in pass1_blocks:
        pass1_all.extend(block.get("findings") or [])

    pass2_all: list[dict[str, Any]] = []
    for result in pass2_results:
        if result.get("skipped"):
            continue
        pass2_all.extend(result.get("findings") or [])

    final = synthesize_redhat_findings(pass1_all, pass2_all)
    if run_id and final:
        insert_findings(run_id, final, model_used="redhat-multipass-synth")
    return final


def run_redhat_multipass_audit(
    project_id: str,
    current_jdf: dict[str, Any],
    previous_jdf: dict[str, Any] | None,
    *,
    run_id: str | None = None,
    generation: int | None = None,
) -> dict[str, Any]:
    """Orchestrate Pass 1 → conditional Pass 2 → Pass 3 synthesis."""
    if generation is not None and is_stale(project_id, generation):
        return {"ok": False, "stale": True}

    deltas = get_ast_deltas(current_jdf, previous_jdf)
    if not deltas:
        _telemetry_update(
            project_id,
            status="complete",
            pass1_complete=True,
            pass2_running=False,
            findings=[],
        )
        return {"ok": True, "deltas": 0, "findings": []}

    _telemetry_update(
        project_id,
        status="pass1_running",
        pass1_complete=False,
        pass2_running=False,
        run_id=run_id or "",
    )

    pass1 = run_redhat_pass1(
        deltas,
        project_id,
        run_id=run_id,
        generation=generation,
    )
    if pass1.get("stale"):
        _telemetry_update(project_id, status="error", error="stale_generation", pass2_running=False)
        return {"ok": False, "stale": True}

    pass1_feed: list[dict[str, Any]] = []
    for block in pass1.get("blocks") or []:
        for finding in block.get("findings") or []:
            if not isinstance(finding, dict):
                continue
            sev = str(finding.get("severity") or "medium").lower()
            if sev == "high":
                continue
            pass1_feed.append(
                _telemetry_finding(
                    finding,
                    pass_num=1,
                    block_hash=str(block.get("block_hash") or ""),
                    node_id=str(block.get("node_id") or ""),
                    cache_hit=bool(block.get("cached")),
                )
            )

    _telemetry_update(
        project_id,
        status="pass1_complete",
        pass1_complete=True,
        pass2_running=False,
        findings=pass1_feed,
    )

    needs_pass2 = any(
        should_run_pass2(block.get("findings") or [], str(block.get("text") or ""))
        for block in pass1.get("blocks") or []
    )
    if needs_pass2:
        _telemetry_update(
            project_id, status="pass2_running", pass2_running=True, pass1_complete=True
        )

    pass2_results: list[dict[str, Any]] = []
    pass2_feed: list[dict[str, Any]] = list(pass1_feed)
    for block in pass1.get("blocks") or []:
        pass2_result = run_redhat_pass2(
            block["block_hash"],
            block.get("findings") or [],
            project_id,
            block_text=str(block.get("text") or ""),
            run_id=pass1.get("run_id") or run_id,
            pass1_model=str(block.get("pass1_model") or PASS1_MODEL),
            generation=generation,
        )
        pass2_results.append(pass2_result)
        if pass2_result.get("skipped"):
            continue
        for finding in pass2_result.get("findings") or []:
            if not isinstance(finding, dict):
                continue
            pass2_feed.append(
                _telemetry_finding(
                    finding,
                    pass_num=2,
                    block_hash=str(block.get("block_hash") or ""),
                    node_id=str(block.get("node_id") or ""),
                    cache_hit=False,
                )
            )
        if pass2_feed != pass1_feed:
            _telemetry_update(
                project_id,
                status="pass2_running" if needs_pass2 else "pass1_complete",
                pass1_complete=True,
                pass2_running=needs_pass2,
                findings=pass2_feed,
            )

    final = run_redhat_pass3(
        pass1.get("blocks") or [],
        pass2_results,
        run_id=pass1.get("run_id") or run_id,
    )

    _telemetry_update(
        project_id,
        status="complete",
        pass1_complete=True,
        pass2_running=False,
        findings=pass2_feed,
        run_id=str(pass1.get("run_id") or run_id or ""),
    )

    return {
        "ok": True,
        "stale": False,
        "deltas": len(deltas),
        "pass1": pass1,
        "pass2": pass2_results,
        "findings": final,
    }


@celery_app.task(name="assure.run_redhat_pass1", bind=True)
def run_redhat_pass1_task(
    self,
    delta_nodes: list[dict[str, Any]],
    project_id: str,
    run_id: str | None = None,
    generation: int | None = None,
) -> dict[str, Any]:
    if generation is not None:
        set_active_task(project_id, self.request.id or "", generation)
    return run_redhat_pass1(delta_nodes, project_id, run_id=run_id, generation=generation)


@celery_app.task(name="assure.run_redhat_pass2", bind=True)
def run_redhat_pass2_task(
    self,
    block_hash: str,
    pass1_findings: list[dict[str, Any]],
    project_id: str,
    block_text: str = "",
    run_id: str | None = None,
    pass1_model: str = PASS1_MODEL,
    generation: int | None = None,
) -> dict[str, Any]:
    return run_redhat_pass2(
        block_hash,
        pass1_findings,
        project_id,
        block_text=block_text,
        run_id=run_id,
        pass1_model=pass1_model,
        generation=generation,
    )


@celery_app.task(name="assure.run_redhat_multipass", bind=True)
def run_redhat_multipass_task(
    self,
    project_id: str,
    current_jdf: dict[str, Any],
    previous_jdf: dict[str, Any] | None,
    run_id: str | None = None,
    generation: int | None = None,
) -> dict[str, Any]:
    if generation is not None:
        set_active_task(project_id, self.request.id or "", generation)
    return run_redhat_multipass_audit(
        project_id,
        current_jdf,
        previous_jdf,
        run_id=run_id,
        generation=generation,
    )
