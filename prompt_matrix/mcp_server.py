"""Stdio MCP server so Cursor and Claude Desktop can call PEM without copy-paste."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# Force the MCP server to treat the REPO ROOT as its working directory
# so that relative paths like ./history.py resolve correctly.
REPO_ROOT = Path(__file__).resolve().parent.parent
os.chdir(REPO_ROOT)

PROTOCOL = "2024-11-05"

try:
    from .engine import MatrixError, render_prompt_detailed
    from .keys import load_keys
    from .personas import list_personas
    from .pipelines import run_workflow
except ImportError:
    from engine import MatrixError, render_prompt_detailed
    from keys import load_keys
    from personas import list_personas
    from pipelines import run_workflow

TOOLS = [
    {
        "name": "pem_compile",
        "description": (
            "Compile a vague task into a target-specific prompt (Claude XML, Gemini, "
            "DeepSeek, Kimi, Ollama, or Cursor /ask @workspace). Does not call a model."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "The user's vague task."},
                "target_ai": {
                    "type": "string",
                    "description": "claude, gemini, deepseek, kimi, ollama, or cursor.",
                    "default": "claude",
                },
                "intent": {
                    "type": "string",
                    "description": "research, design, comparison, debug, or analysis.",
                    "default": "analysis",
                },
                "context": {"type": "string", "description": "Notes or local file paths to inject."},
                "class_id": {"type": "string", "description": "Optional saved class id."},
            },
            "required": ["task"],
        },
    },
    {
        "name": "pem_combine",
        "description": (
            "Run PEM Combine: two or more models draft, then one merge. "
            "Use this to lock revision copy, compare options, or get a single grounded answer. "
            "Calls live APIs when direct is true."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "The user's vague task."},
                "target_ai": {
                    "type": "string",
                    "description": "Primary model and combiner. gemini, deepseek, claude, kimi, or ollama.",
                    "default": "gemini",
                },
                "extra_targets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Other models to draft. Default: [\"deepseek\"].",
                },
                "intent": {
                    "type": "string",
                    "description": "research, design, comparison, debug, or analysis.",
                    "default": "analysis",
                },
                "context": {"type": "string"},
                "direct": {
                    "type": "boolean",
                    "description": "If true, call the models. If false, return compiled prompts.",
                    "default": True,
                },
                "ground": {
                    "type": "boolean",
                    "description": "Anti-hallucination rewrite rules on the compiled prompts.",
                    "default": True,
                },
                "history": {
                    "type": "boolean",
                    "description": "Append hashes to prompt_matrix/history.sqlite.",
                    "default": True,
                },
                "class_id": {"type": "string"},
            },
            "required": ["task"],
        },
    },
    {
        "name": "pem_critique_rewrite",
        "description": (
            "Run PEM's draft → critique → final rewrite loop. Returns the compiled packet, "
            "the three step prompts, and model replies when direct is true."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "The user's vague task."},
                "target_ai": {
                    "type": "string",
                    "description": "Model that writes the attempt and the final rewrite.",
                    "default": "claude",
                },
                "intent": {
                    "type": "string",
                    "default": "analysis",
                },
                "context": {"type": "string"},
                "critic": {
                    "type": "string",
                    "description": "Second model for the critique, or 'rule' for a local no-API critic. Ignored when local is true unless critic is rule.",
                },
                "persona": {
                    "type": "string",
                    "description": "redhat, security, tokens, schema, or code.",
                    "default": "redhat",
                },
                "local": {
                    "type": "boolean",
                    "description": "Route attempt, critique, and rewrite through Ollama.",
                    "default": False,
                },
                "direct": {
                    "type": "boolean",
                    "description": "If true, call the models. If false, return the three prompts to paste.",
                    "default": True,
                },
                "class_id": {"type": "string"},
            },
            "required": ["task"],
        },
    },
    {
        "name": "pem_dialect_lint",
        "description": (
            "Compile a task then statically check the target dialect "
            "(Claude XML tags, DeepSeek headings, Cursor /ask, etc.). No model call."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string"},
                "target_ai": {"type": "string", "default": "claude"},
                "intent": {"type": "string", "default": "analysis"},
                "context": {"type": "string"},
                "class_id": {"type": "string"},
            },
            "required": ["task"],
        },
    },
    {
        "name": "pem_export",
        "description": "Export a saved PEM class as cursorrules, mdc, fabric, or dspy.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "class_id": {"type": "string"},
                "format": {
                    "type": "string",
                    "enum": ["cursorrules", "mdc", "fabric", "dspy"],
                    "default": "mdc",
                },
            },
            "required": ["class_id"],
        },
    },
    {
        "name": "swarm_develop",
        "description": (
            "Run the development swarm on this repo: architect, developer, reviewer, "
            "red-hat rewrite loop, then tester plus unittest beside documenter. "
            "Calls live APIs through run_swarm(). Returns a quality report. "
            "Developer max_tokens is 16384. Truncated dumps are continued or dropped; "
            "do not paste a cut dump into index.html. "
            "Writes logs/swarm.patch. create_pr only adds a gh attempt, not git add/commit/push. "
            "Developer max_tokens is 16384; truncated dumps are continued or dropped, not applied."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Natural-language feature to build.",
                },
                "context_files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Source file paths to include as context.",
                },
                "target_models": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": (
                        "Role to model override, e.g. {\"developer\": \"deepseek-chat\"}. "
                        "Roles: architect, developer, reviewer, tester, documenter. "
                        "Also accepts a list of ROLE=MODEL strings."
                    ),
                },
                "edition": {
                    "type": "string",
                    "enum": ["free", "pro", "team", "self-hosted"],
                    "description": "Assure edition (sets ASSURE_EDITION for this call). Default from config / env.",
                },
                "max_redhat_iterations": {
                    "type": "integer",
                    "description": "Cap on reviewer-driven red-hat revisions. Maps to run_swarm(max_redhat_iterations) / CLI --max-iterations. Default 3.",
                    "default": 3,
                },
                "min_confidence": {
                    "type": "number",
                    "description": "Merge gate used only when create_pr tries gh. Default 0.8. Not a score floor.",
                    "default": 0.8,
                },
                "skip_tests": {
                    "type": "boolean",
                    "description": "Skip tester and unittest self-test. The report must not say tests passed.",
                    "default": False,
                },
                "skip_docs": {
                    "type": "boolean",
                    "description": "Skip documenter.",
                    "default": False,
                },
                "create_pr": {
                    "type": "boolean",
                    "description": "Try gh pr create if confidence meets min_confidence. Still writes logs/swarm.patch either way. Does not git add, commit, or push.",
                    "default": False,
                },
                "direct": {
                    "type": "boolean",
                    "description": "If true, call the models. If false, compile prompts only (CLI --copy-only).",
                    "default": True,
                },
                "local": {
                    "type": "boolean",
                    "description": "Route every role through the local ollama target.",
                    "default": False,
                },
                "cheap": {
                    "type": "boolean",
                    "description": "Let PEM's cost router pick a cheaper live target.",
                    "default": False,
                },
            },
            "required": ["task"],
        },
    },
    {
        "name": "apply_patch",
        "description": (
            "Apply a unified diff to the workspace root. No live APIs. "
            "Rejects path traversal and truncated dumps (cut fences, PATCH without end, "
            "finish-reason length leftovers). Do not use this to paste a cut HTML dump "
            "into index.html."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "diff": {
                    "type": "string",
                    "description": "Unified diff (git-style ---/+++ / @@ hunks).",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "Parse and check only. Do not write files.",
                    "default": False,
                },
            },
            "required": ["diff"],
        },
    },
]


def serve_stdio() -> int:
    try:
        from .pem_runner import ensure_preflight
    except ImportError:
        from pem_runner import ensure_preflight
    ensure_preflight(announce=False)
    load_keys()
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    sys.stdout = open(os.devnull, "w")
    while True:
        message = _read_message(stdin)
        if message is None:
            return 0
        response = _handle(message)
        if response is not None:
            _write_message(stdout, response)


def _handle(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    msg_id = message.get("id")
    params = message.get("params") or {}

    if method == "initialize":
        client_version = params.get("protocolVersion") or PROTOCOL
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": client_version if str(client_version).startswith("202") else PROTOCOL,
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"listChanged": False},
                    "prompts": {"listChanged": False},
                },
                "serverInfo": {"name": "pem", "version": "0.1.0"},
                "instructions": (
                    "PEM compiles prompts and optionally calls Gemini/DeepSeek/Claude/Kimi/Ollama. "
                    "Standalone model calls have no live web search. "
                    "Use pem_compile to shape a task for a target dialect (no model call). "
                    "Use pem_combine for two-model draft plus merge. "
                    "Use pem_critique_rewrite for attempt, persona critique, and final rewrite. "
        "Use swarm_develop to run the architect/developer/reviewer swarm (live APIs). "
        "Use apply_patch to apply a complete unified diff to the workspace (no live APIs). "
        "MCP tools are stdio-only; there is no pem mcp swarm_develop CLI. "
                    "Intents: research, design, comparison, debug, analysis. "
                    "Personas: " + ", ".join(item["id"] for item in list_personas()) + "."
                ),
            },
        }

    if method == "notifications/initialized" or method == "notifications/cancelled":
        return None

    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}

    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"resources": []}}

    if method == "prompts/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"prompts": []}}

    if method == "logging/setLevel":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            arguments = {}
        try:
            text = _call_tool(name, arguments)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"content": [{"type": "text", "text": text}], "isError": False},
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
            }

    if msg_id is None:
        return None
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Unknown method: {method}"},
    }


def _call_tool(name: str, args: dict[str, Any]) -> str:
    if name == "pem_compile":
        return _compile(args)
    if name == "pem_combine":
        return _combine(args)
    if name == "pem_critique_rewrite":
        return _critique_rewrite(args)
    if name == "pem_dialect_lint":
        return _lint(args)
    if name == "pem_export":
        return _export(args)
    if name == "swarm_develop":
        return _swarm_develop(args)
    if name == "apply_patch":
        return _apply_patch(args)
    raise MatrixError(
        "Unknown tool. Use pem_compile, pem_combine, pem_critique_rewrite, "
        "pem_dialect_lint, pem_export, swarm_develop, or apply_patch."
    )


def _compile(args: dict[str, Any]) -> str:
    task = str(args.get("task") or "").strip()
    if not task:
        raise MatrixError("pem_compile needs a task.")
    rendered = render_prompt_detailed(
        str(args.get("target_ai") or "claude"),
        str(args.get("intent") or "analysis"),
        task,
        str(args.get("context") or ""),
        class_id=str(args.get("class_id") or "").strip() or None,
    )
    files = ""
    if rendered.files_read:
        files = "\nInjected files: " + ", ".join(rendered.files_read)
    return (
        f"target: {rendered.target_ai}\n"
        f"intent: {rendered.intent}\n"
        f"wrapper: {rendered.wrapper}\n"
        f"{files}\n\n"
        f"{rendered.prompt}"
    ).strip()


def _extra_targets(args: dict[str, Any]) -> list[str]:
    raw = args.get("extra_targets")
    if raw is None:
        return ["deepseek"]
    if isinstance(raw, str):
        return [part.strip() for part in raw.split(",") if part.strip()]
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    return ["deepseek"]


def _format_workflow(result: Any) -> str:
    chunks = [
        f"workflow: {result.workflow}",
        f"target: {result.target_ai}",
        f"intent: {result.intent}",
    ]
    if result.persona:
        chunks.append(f"persona: {result.persona}")
    if getattr(result, "local", False):
        chunks.append("local: true")
    if getattr(result, "total_tokens", None):
        chunks.append(
            f"tokens: {result.input_tokens} in + {result.output_tokens} out = {result.total_tokens}"
        )
    if getattr(result, "estimated_cost", None):
        chunks.append(f"estimated_cost_usd: {result.estimated_cost:.6f}")
    if result.note:
        chunks.append(f"note: {result.note}")
    if result.reply:
        chunks.append("===== FINAL =====\n" + result.reply)
    for step in result.steps:
        header = f"===== {step.name} ({step.target_ai}) ====="
        body = step.reply or step.prompt
        err = f"\nerror: {step.error}" if step.error else ""
        chunks.append(f"{header}\n{body}{err}")
    if not result.reply:
        chunks.append("===== PACKET =====\n" + result.prompt)
    return "\n\n".join(chunks)


def _combine(args: dict[str, Any]) -> str:
    task = str(args.get("task") or "").strip()
    if not task:
        raise MatrixError("pem_combine needs a task.")
    extras = _extra_targets(args)
    if not extras:
        raise MatrixError("pem_combine needs at least one extra_targets model besides the primary.")
    result = run_workflow(
        str(args.get("target_ai") or "gemini"),
        str(args.get("intent") or "analysis"),
        task,
        str(args.get("context") or ""),
        workflow="ensemble",
        extra_targets=extras,
        ground=_as_bool(args.get("ground"), True),
        direct=_as_bool(args.get("direct"), True),
        copy=False,
        history=_as_bool(args.get("history"), True),
        class_id=str(args.get("class_id") or "").strip() or None,
        lint=_as_bool(args.get("direct"), True),
    )
    return _format_workflow(result)


def _critique_rewrite(args: dict[str, Any]) -> str:
    task = str(args.get("task") or "").strip()
    if not task:
        raise MatrixError("pem_critique_rewrite needs a task.")
    result = run_workflow(
        str(args.get("target_ai") or "claude"),
        str(args.get("intent") or "analysis"),
        task,
        str(args.get("context") or ""),
        workflow="redhat",
        critic=str(args.get("critic") or "").strip() or None,
        persona=str(args.get("persona") or "redhat"),
        local=_as_bool(args.get("local"), False),
        direct=_as_bool(args.get("direct"), True),
        copy=False,
        class_id=str(args.get("class_id") or "").strip() or None,
        lint=_as_bool(args.get("direct"), True),
    )
    return _format_workflow(result)


def _lint(args: dict[str, Any]) -> str:
    try:
        from .linter import lint_prompt
    except ImportError:
        from linter import lint_prompt
    task = str(args.get("task") or "").strip()
    if not task:
        raise MatrixError("pem_dialect_lint needs a task.")
    target = str(args.get("target_ai") or "claude")
    rendered = render_prompt_detailed(
        target,
        str(args.get("intent") or "analysis"),
        task,
        str(args.get("context") or ""),
        class_id=str(args.get("class_id") or "").strip() or None,
    )
    report = lint_prompt(rendered.target_ai, rendered.prompt)
    status = "ok" if report.ok else "failed"
    extra = report.as_text()
    return f"target: {rendered.target_ai}\nlint: {status}\n{extra}"


def _export(args: dict[str, Any]) -> str:
    try:
        from .exporters import export_class
    except ImportError:
        from exporters import export_class
    class_id = str(args.get("class_id") or "").strip()
    if not class_id:
        raise MatrixError("pem_export needs class_id.")
    fmt = str(args.get("format") or "mdc")
    return export_class(class_id, fmt)


_EDITIONS = ("free", "pro", "team", "self-hosted")
_TRUNC_MARK = "... [truncated]"
_SWARM_DIFF_WARNING = (
    "Apply complete diffs from this report or from the Patch path below, "
    "or call apply_patch with that unified diff. "
    "Truncated dumps are continued or dropped; do not paste a cut dump into index.html."
)


def _apply_patch(args: dict[str, Any]) -> str:
    try:
        from .patch_apply import PatchError, apply_unified_diff
    except ImportError:
        from patch_apply import PatchError, apply_unified_diff
    diff = str(args.get("diff") or "")
    if not diff.strip():
        raise MatrixError("apply_patch needs a unified diff.")
    try:
        return apply_unified_diff(
            diff,
            root=REPO_ROOT,
            dry_run=_as_bool(args.get("dry_run"), False),
        )
    except PatchError as exc:
        raise MatrixError(str(exc)) from exc


def _swarm_develop(args: dict[str, Any]) -> str:
    try:
        from .swarm import MIN_CONFIDENCE, run_swarm
    except ImportError:
        from swarm import MIN_CONFIDENCE, run_swarm
    task = str(args.get("task") or "").strip()
    if not task:
        raise MatrixError("swarm_develop needs a task.")
    edition = str(args.get("edition") or "").strip().lower()
    previous_edition = os.environ.get("ASSURE_EDITION")
    if edition:
        if edition not in _EDITIONS:
            raise MatrixError(
                f"Unknown edition {edition!r}. Use one of: {', '.join(_EDITIONS)}."
            )
        os.environ["ASSURE_EDITION"] = edition
    max_rounds = _as_optional_int(args.get("max_redhat_iterations"))
    min_conf = _as_optional_float(args.get("min_confidence"))
    try:
        result = run_swarm(
            task=task,
            context_files=_as_str_list(args.get("context_files")),
            target_models=_as_target_models(args.get("target_models")),
            create_pr=_as_bool(args.get("create_pr"), False),
            direct=_as_bool(args.get("direct"), True),
            local=_as_bool(args.get("local"), False),
            cheap=_as_bool(args.get("cheap"), False),
            max_redhat_iterations=max_rounds,
            min_confidence=MIN_CONFIDENCE if min_conf is None else min_conf,
            skip_tests=_as_bool(args.get("skip_tests"), False),
            skip_docs=_as_bool(args.get("skip_docs"), False),
        )
    finally:
        if edition:
            if previous_edition is None:
                os.environ.pop("ASSURE_EDITION", None)
            else:
                os.environ["ASSURE_EDITION"] = previous_edition
    return _format_swarm_result(result)


def _format_swarm_result(result: Any) -> str:
    tests = getattr(result, "test_results", None)
    skipped = bool(getattr(tests, "skipped", False))
    passed = getattr(tests, "passed", None)
    skip_reason = getattr(tests, "skip_reason", None)
    if skipped:
        test_line = f"skipped ({skip_reason or 'no reason'})"
    elif passed is True:
        test_line = "Pass"
    elif passed is False:
        test_line = "Fail"
    else:
        test_line = "Not run"
    docs = getattr(result, "documentation", None) or ""
    if docs.strip().lower() == "skipped":
        docs_line = "skipped"
    elif docs.strip():
        docs_line = "present"
    else:
        docs_line = "none"
    files = getattr(result, "files", None) or getattr(result, "code", None) or {}
    file_bits = []
    for path, body in files.items():
        size = len(body or "")
        file_bits.append(f"{path} ({size} bytes)")
    warnings = _truncation_warnings(result)
    chunks = [
        "# swarm_develop",
        "",
        _SWARM_DIFF_WARNING,
        "",
    ]
    if warnings:
        chunks.extend(["WARNING: " + item for item in warnings])
        chunks.append("")
    verdict = getattr(result, "review_verdict", None) or getattr(result, "reviewer_verdict", None) or "(none)"
    rev_conf = getattr(result, "review_confidence", None)
    if rev_conf is None:
        rev_conf = getattr(result, "reviewer_confidence", None)
    overall = getattr(result, "confidence", None)
    if overall is None:
        overall = getattr(result, "confidence_score", None)
    rev_conf_text = "n/a" if rev_conf is None else f"{float(rev_conf):.2f}"
    overall_text = "n/a" if overall is None else f"{float(overall):.3f}"
    chunks.extend(
        [
            f"task: {getattr(result, 'task', '')}",
            f"run_hash: {getattr(result, 'run_hash', '') or '(none)'}",
            f"verdict: {verdict}",
            f"reviewer_confidence: {rev_conf_text}",
            f"overall_confidence: {overall_text}",
            f"self-test: {test_line}",
            f"docs: {docs_line}",
            f"files: {', '.join(file_bits) if file_bits else '(none)'}",
            f"patch: {getattr(result, 'patch_path', None) or '(none)'}",
            f"redhat_rounds: {getattr(result, 'redhat_rounds', 0)}",
        ]
    )
    targets = getattr(result, "targets", None) or {}
    if targets:
        used = ", ".join(f"{role}={target}" for role, target in targets.items())
        chunks.append(f"targets: {used}")
    report = getattr(result, "quality_report", None) or getattr(result, "summary", None) or ""
    chunks.extend(["", "===== QUALITY REPORT =====", report.rstrip()])
    return "\n".join(chunks).rstrip() + "\n"


def _truncation_warnings(result: Any) -> list[str]:
    warnings: list[str] = []
    files = getattr(result, "files", None) or getattr(result, "code", None) or {}
    for path, body in files.items():
        hint = _file_looks_truncated(str(path), body or "")
        if hint:
            warnings.append(hint)
    report = getattr(result, "quality_report", None) or getattr(result, "summary", None) or ""
    implementation = getattr(result, "implementation", None) or ""
    for blob, label in ((report, "quality report"), (implementation, "implementation")):
        if _TRUNC_MARK in blob:
            warnings.append(f"{label} contains '{_TRUNC_MARK}'")
        if blob.count("```") % 2:
            warnings.append(f"{label} has an unclosed code fence")
    notes = getattr(result, "notes", None) or []
    for note in notes:
        if _TRUNC_MARK in str(note):
            warnings.append(str(note))
    seen: list[str] = []
    for item in warnings:
        if item not in seen:
            seen.append(item)
    return seen


def _file_looks_truncated(path: str, body: str) -> str | None:
    try:
        from .patch_apply import truncation_reason_for_body
    except ImportError:
        from patch_apply import truncation_reason_for_body
    return truncation_reason_for_body(path, body)


def _as_str_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        items = [part.strip() for part in value.replace("\n", ",").split(",") if part.strip()]
        return items or None
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
        return items or None
    text = str(value).strip()
    return [text] if text else None


def _as_target_models(value: Any) -> dict[str, str] | None:
    if value is None or value == "" or value == {}:
        return None
    try:
        from .swarm import _parse_role_models
    except ImportError:
        from swarm import _parse_role_models
    if isinstance(value, dict):
        items = [f"{key}={val}" for key, val in value.items() if str(key).strip() and str(val).strip()]
        return _parse_role_models(items) or None
    if isinstance(value, str):
        parts = [part.strip() for part in value.replace(",", " ").split() if part.strip()]
        return _parse_role_models(parts) or None
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                role = str(item.get("role") or item.get("name") or "").strip()
                model = str(item.get("model") or item.get("value") or "").strip()
                if role and model:
                    parts.append(f"{role}={model}")
                continue
            text = str(item).strip()
            if text:
                parts.append(text)
        return _parse_role_models(parts) or None
    return None


def _as_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _as_optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _read_message(stdin) -> dict[str, Any] | None:
    line = stdin.readline()
    if not line:
        return None
    stripped = line.lstrip()
    if stripped.startswith(b"{") or stripped.startswith(b"["):
        return _as_object(json.loads(line.decode("utf-8")))
    headers: dict[str, str] = {}
    while True:
        if line in (b"\r\n", b"\n"):
            break
        decoded = line.decode("utf-8")
        if ":" in decoded:
            key, value = decoded.split(":", 1)
            headers[key.strip().lower()] = value.strip()
        line = stdin.readline()
        if not line:
            return None
    length = int(headers.get("content-length") or "0")
    if length <= 0:
        return None
    body = stdin.read(length)
    if not body:
        return None
    return _as_object(json.loads(body.decode("utf-8")))


def _as_object(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("MCP message must be a JSON object")
    return payload


def _write_message(stdout, payload: dict[str, Any]) -> None:
    # MCP stdio is newline-delimited JSON. Content-Length (LSP) replies leave
    # Cursor stuck on initializing / mcp_auth.
    blob = json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
    stdout.write(blob)
    stdout.flush()


if __name__ == "__main__":
    raise SystemExit(serve_stdio())
