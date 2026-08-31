"""Stdio MCP server so Cursor and Claude Desktop can call PEM without copy-paste."""

from __future__ import annotations

import json
import os
import sys
from typing import Any

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
]


def serve_stdio() -> int:
    try:
        from .pem_runner import ensure_preflight
    except ImportError:
        from pem_runner import ensure_preflight
    ensure_preflight()
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
    raise MatrixError(
        "Unknown tool. Use pem_compile, pem_combine, pem_critique_rewrite, "
        "pem_dialect_lint, or pem_export."
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
    blob = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(blob)}\r\n\r\n".encode("ascii")
    stdout.write(header + blob)
    stdout.flush()
