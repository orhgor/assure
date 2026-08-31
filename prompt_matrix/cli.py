#!/usr/bin/env python3
"""Interactive and argument-driven entry point for Assure (PEM engine)."""

from __future__ import annotations

import argparse
import os
import sys

from rich.console import Console
from rich.panel import Panel
from rich.prompt import IntPrompt, Prompt
from rich.syntax import Syntax
from rich.table import Table
from rich.theme import Theme

try:
    from .engine import (
        MatrixError,
        execute,
        list_intents,
        list_targets,
        load_matrix,
        normalize_target,
        render_prompt_detailed,
    )
    from .models import ExecutionResult, MatrixConfig
except ImportError:
    from engine import (
        MatrixError,
        execute,
        list_intents,
        list_targets,
        load_matrix,
        normalize_target,
        render_prompt_detailed,
    )
    from models import ExecutionResult, MatrixConfig

THEME = Theme(
    {
        "banner": "bold cyan",
        "ok": "bold green",
        "warn": "bold yellow",
        "err": "bold red",
        "muted": "dim",
    }
)


def build_parser(config: MatrixConfig | None = None) -> argparse.ArgumentParser:
    targets = sorted(config.targets) if config else ["claude", "gemini", "deepseek", "kimi", "ollama", "cursor"]
    intents = sorted(config.intents) if config else [
        "research",
        "design",
        "comparison",
        "debug",
        "analysis",
    ]
    parser = argparse.ArgumentParser(
        prog="assure",
        description=(
            "Assure: ask once, get a checked answer. Engine is PEM. "
            "Default action copies the compiled prompt to the clipboard."
        ),
        epilog='Example: assure claude research "Compare AWS vs GCP" --copy',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("target", nargs="?", help=f"Target AI ({', '.join(targets)})")
    parser.add_argument("intent", nargs="?", help=f"Intent ({', '.join(intents)})")
    parser.add_argument("task", nargs="?", help="Vague task in quotes")
    parser.add_argument(
        "-c",
        "--context",
        default="",
        help="Extra context, or a file path / glob that will be read automatically",
    )
    parser.add_argument(
        "--copy",
        action="store_true",
        help="Copy the rendered prompt to the clipboard (default when --direct is omitted)",
    )
    parser.add_argument(
        "--direct",
        action="store_true",
        help="Send the rendered prompt through LiteLLM instead of only copying it",
    )
    parser.add_argument(
        "--save",
        metavar="PATH",
        help="Write prompt (and reply, if --direct) to a JSON file. Uses instructor when possible.",
    )
    parser.add_argument(
        "--model",
        help="Override the LiteLLM model id (or set PEM_MODEL)",
    )
    parser.add_argument(
        "--cheap",
        action="store_true",
        help="Pick the cheapest live model that fits prompt length and intent (PEM_COST_ROUTE)",
    )
    parser.add_argument(
        "--edition",
        choices=["free", "pro", "team", "self-hosted"],
        help="Assure edition (or ASSURE_EDITION). Default free.",
    )
    parser.add_argument(
        "--config",
        dest="config_path",
        help="Path to config.json (defaults to the file next to this package)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print known targets and intents, then exit",
    )
    parser.add_argument(
        "--print",
        dest="show_prompt",
        action="store_true",
        help="Print the full rendered prompt to stdout",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Skip the preview panel",
    )
    parser.add_argument(
        "--workflow",
        choices=["single", "ensemble", "redhat"],
        default="single",
        help="single (default), ensemble, or redhat (draft → critique → final)",
    )
    parser.add_argument(
        "--critic",
        help="Second model for --workflow redhat, or 'rule' for a local no-API critic (PEM_CRITIC_MODE=rule)",
    )
    parser.add_argument(
        "--persona",
        default="redhat",
        help="Critic persona: redhat, security, tokens, schema, code",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Route the loop through Ollama. No API key, no network.",
    )
    parser.add_argument(
        "--extra",
        action="append",
        default=[],
        metavar="TARGET",
        help="Additional target for --workflow ensemble (repeatable)",
    )
    parser.add_argument("--class-id", help="Saved class id (also used with --export)")
    parser.add_argument(
        "--export",
        choices=["cursorrules", "mdc", "fabric", "dspy"],
        help="Print a class as .cursorrules, .mdc, Fabric, or a DSPy signature",
    )
    parser.add_argument(
        "--lint",
        action="store_true",
        help="Fail if the compiled dialect is missing required tags (also on Send)",
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help="Append this run to prompt_matrix/history.sqlite (or set PEM_ENABLE_HISTORY=1)",
    )
    parser.add_argument(
        "--store-prompts",
        action="store_true",
        help="Store compiled prompts and finals in prompt_versions (PEM_STORE_PROMPTS)",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        metavar="N",
        help="LiteLLM max_tokens (PEM_MAX_TOKENS, default 4096)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        metavar="SECONDS",
        help="LiteLLM timeout in seconds (PEM_TIMEOUT_SECONDS, default 60)",
    )
    parser.add_argument(
        "--auth-user",
        "--http-user",
        dest="auth_user",
        help="Web UI Basic Auth user (PEM_HTTP_USER, default admin)",
    )
    parser.add_argument(
        "--ci",
        action="store_true",
        help="Print one JSON object (prompt, reply, scores) and exit 1 on lint or red-team errors",
    )
    parser.add_argument(
        "--redteam",
        action="store_true",
        help="Run local injection, PII, and citation checks on the compiled prompt (and reply if sent)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    try:
        from .pem_runner import ensure_preflight
    except ImportError:
        from pem_runner import ensure_preflight
    ensure_preflight()
    if raw[:1] == ["mcp"]:
        try:
            from .mcp_server import serve_stdio
        except ImportError:
            from mcp_server import serve_stdio
        return serve_stdio()
    if raw[:1] == ["eval"]:
        try:
            from .eval_run import main as eval_main
        except ImportError:
            from eval_run import main as eval_main
        return eval_main(raw[1:])
    if raw[:1] == ["monitor"]:
        try:
            from .monitor import main as monitor_main
        except ImportError:
            from monitor import main as monitor_main
        return monitor_main(raw[1:])
    if _should_serve(raw):
        try:
            from .web import serve
        except ImportError:
            from web import serve
        serve_args = list(raw)
        if serve_args and serve_args[0] in {"serve", "web"}:
            serve_args = serve_args[1:]
        return serve(serve_args)
    if raw[:1] == ["--cli"]:
        raw = raw[1:]
    if raw[:1] in (["-h"], ["--help"]):
        _print_top_help()
        return 0

    console = Console(theme=THEME)
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", dest="config_path", default=None)
    pre_args, _ = pre.parse_known_args(raw)
    argv = raw

    try:
        config = load_matrix(pre_args.config_path)
    except MatrixError as exc:
        console.print(f"[err]{exc}[/err]")
        return 1

    parser = build_parser(config)
    args = parser.parse_args(argv)
    if args.ci:
        args.quiet = True
    try:
        from .engine import apply_runtime_options
    except ImportError:
        from engine import apply_runtime_options
    apply_runtime_options(
        config,
        store_prompts=bool(getattr(args, "store_prompts", False)),
        max_tokens=getattr(args, "max_tokens", None),
        timeout=getattr(args, "timeout", None),
        critic=getattr(args, "critic", None),
        auth_user=getattr(args, "auth_user", None),
        auth_pass=getattr(args, "auth_pass", None),
        cheap=bool(getattr(args, "cheap", False)),
        model=getattr(args, "model", None),
        edition=getattr(args, "edition", None),
    )

    if args.list:
        _print_catalog(console, config)
        return 0

    if args.export:
        try:
            from .exporters import export_class
        except ImportError:
            from exporters import export_class
        if not args.class_id:
            parser.error("--export needs --class-id")
        try:
            console.print(export_class(args.class_id, args.export), markup=False)
        except MatrixError as exc:
            console.print(f"[err]{exc}[/err]")
            return 1
        return 0

    interactive = args.target is None or args.intent is None or args.task is None
    if interactive and not sys.stdin.isatty():
        parser.error("target, intent, and task are required when stdin is not a TTY")

    try:
        if interactive:
            _print_banner(console)
            target, intent, task, context = _interactive_collect(
                console, config, args
            )
        else:
            target = normalize_target(args.target, config)
            intent = args.intent.strip().lower()
            if intent not in config.intents:
                raise MatrixError(
                    f"Unknown intent '{args.intent}'. Available: {', '.join(sorted(config.intents))}."
                )
            task = args.task
            context = args.context
            if not sys.stdin.isatty():
                piped = sys.stdin.read()
                if piped.strip():
                    context = f"{context}\n\n{piped}".strip() if context else piped
            if target not in config.targets:
                raise MatrixError(
                    f"Unknown target '{args.target}'. Available: {', '.join(sorted(config.targets))}."
                )

        if args.local:
            target = "ollama"

        if not args.quiet:
            preview = render_prompt_detailed(
                target, intent, task, context, config=config, config_path=args.config_path,
                class_id=args.class_id,
            )
            _print_preview(console, preview)
            if preview.files_read:
                console.print(
                    f"[muted]Injected file(s): {', '.join(preview.files_read)}[/muted]"
                )
            try:
                from .linter import lint_prompt
            except ImportError:
                from linter import lint_prompt
            report = lint_prompt(target, preview.prompt)
            if report.errors and (args.direct or args.lint):
                raise MatrixError(
                    "ERROR: Lint failed. Fix structural issues before sending.\n"
                    f"{report.as_text()}"
                )
            if report.warnings:
                console.print(f"[muted]{report.as_text()}[/muted]")
        elif args.direct or args.lint:
            preview = render_prompt_detailed(
                target, intent, task, context, config=config, config_path=args.config_path,
                class_id=args.class_id,
            )
            try:
                from .linter import lint_prompt
            except ImportError:
                from linter import lint_prompt
            report = lint_prompt(target, preview.prompt)
            if report.errors:
                raise MatrixError(
                    "ERROR: Lint failed. Fix structural issues before sending.\n"
                    f"{report.as_text()}"
                )

        if args.workflow != "single" or args.cheap:
            try:
                from .pipelines import run_workflow
            except ImportError:
                from pipelines import run_workflow
            pipe = run_workflow(
                target,
                intent,
                task,
                context,
                workflow=args.workflow,
                extra_targets=args.extra,
                critic=args.critic,
                persona=args.persona,
                local=args.local,
                direct=args.direct,
                copy=args.copy or not args.direct,
                class_id=args.class_id,
                lint=args.lint,
                history=args.history,
                cheap=bool(args.cheap),
            )
            if not args.ci:
                if pipe.reply:
                    console.print()
                    console.print(Panel(pipe.reply, title="final", border_style="magenta"))
                elif args.show_prompt or args.quiet:
                    console.print(pipe.prompt)
                if pipe.note:
                    console.print(f"[muted]{pipe.note}[/muted]")
                if pipe.total_tokens:
                    cost = ""
                    if pipe.estimated_cost:
                        cost = f" (~${pipe.estimated_cost:.6f} est.)"
                    console.print(
                        f"[muted]{pipe.input_tokens} in + {pipe.output_tokens} out = {pipe.total_tokens} tokens{cost}[/muted]"
                    )
                if pipe.copied:
                    console.print("[ok]Copied to the clipboard.[/ok]")
            if args.redteam or args.ci:
                code = _emit_checks(
                    console,
                    prompt=pipe.prompt,
                    reply=pipe.reply,
                    ci=args.ci,
                    redteam=args.redteam,
                    pipeline=pipe,
                )
                if code:
                    return code
            return 0

        result = execute(
            target,
            intent,
            task,
            context,
            direct=args.direct,
            copy=args.copy or not args.direct,
            save_path=args.save,
            model=args.model,
            config=config,
            config_path=args.config_path,
            class_id=args.class_id,
        )
        try:
            from .history import history_enabled, record_run
        except ImportError:
            from history import history_enabled, record_run
        if history_enabled(args.history):
            record_run(
                target_ai=result.rendered.target_ai,
                intent=result.rendered.intent,
                workflow="single",
                task=task,
                prompt=result.rendered.prompt,
                reply=result.reply,
                note=result.note,
            )
    except KeyboardInterrupt:
        console.print("\n[warn]Cancelled.[/warn]")
        return 130
    except MatrixError as exc:
        console.print(f"[err]{exc}[/err]")
        return 1

    if not getattr(args, "ci", False):
        _print_result(console, result, show_prompt=args.show_prompt or args.quiet)
    if args.redteam or args.ci:
        return _emit_checks(
            console,
            prompt=result.rendered.prompt,
            reply=result.reply,
            ci=args.ci,
            redteam=args.redteam,
            execution=result,
        )
    return 0


def _emit_checks(
    console: Console,
    *,
    prompt: str,
    reply: str | None,
    ci: bool,
    redteam: bool,
    pipeline=None,
    execution=None,
) -> int:
    lint_payload = None
    try:
        from .linter import lint_prompt
    except ImportError:
        from linter import lint_prompt
    target = None
    if pipeline is not None:
        target = pipeline.target_ai
    elif execution is not None and execution.rendered:
        target = execution.rendered.target_ai
    if target:
        report = lint_prompt(target, prompt or "")
        lint_payload = {"ok": report.ok, "errors": report.errors, "warnings": report.warnings}
    red = None
    if redteam or ci:
        try:
            from .agents.redteam import scan_prompt_and_reply
        except ImportError:
            from agents.redteam import scan_prompt_and_reply
        red = scan_prompt_and_reply(prompt or "", reply)
    if ci:
        try:
            from .ci_report import emit, failed, from_execution, from_pipeline
        except ImportError:
            from ci_report import emit, failed, from_execution, from_pipeline
        if pipeline is not None:
            payload = from_pipeline(pipeline, lint=lint_payload, redteam=red)
        else:
            payload = from_execution(execution, lint=lint_payload, redteam=red)
        if payload.get("quality") is None and payload.get("reply"):
            try:
                from .quality import score_run
            except ImportError:
                from quality import score_run
            payload["quality"] = score_run(
                reply=payload["reply"] or "",
                context="",
                drafts=[],
                total_tokens=0,
            ).as_dict()
        emit(payload)
        return 1 if failed(payload) else 0
    if redteam and red:
        for item in red.get("errors") or []:
            console.print(f"[err]{item}[/err]")
        for item in red.get("warnings") or []:
            console.print(f"[warn]{item}[/warn]")
        if red.get("ok"):
            console.print("[ok]Red-team checks passed.[/ok]")
        return 0 if red.get("ok") else 1
    return 0


def _print_banner(console: Console) -> None:
    console.print(
        Panel(
            "[banner]Assure[/banner]\n"
            "[muted]Ask once. Get a checked answer. Engine: PEM.[/muted]",
            border_style="cyan",
        )
    )


def _print_catalog(console: Console, config: MatrixConfig) -> None:
    targets = Table(title="Targets", show_header=True, header_style="bold")
    targets.add_column("#", style="cyan", width=3)
    targets.add_column("Name")
    targets.add_column("Wrapper")
    targets.add_column("LiteLLM model")
    for index, (name, target) in enumerate(config.targets.items(), start=1):
        targets.add_row(
            str(index),
            target.label or name,
            target.wrapper,
            target.model or "clipboard only",
        )

    intents = Table(title="Intents", show_header=True, header_style="bold")
    intents.add_column("#", style="cyan", width=3)
    intents.add_column("Name")
    intents.add_column("Default role")
    for index, (name, intent) in enumerate(config.intents.items(), start=1):
        intents.add_row(str(index), name, intent.role)

    console.print(targets)
    console.print()
    console.print(intents)


def _interactive_collect(
    console: Console,
    config: MatrixConfig,
    args: argparse.Namespace,
) -> tuple[str, str, str, str]:
    target = (
        normalize_target(args.target, config)
        if args.target
        else _pick_numbered(
            console,
            "Target AI",
            list_targets(config),
        )
    )
    intent = (
        args.intent.strip().lower()
        if args.intent
        else _pick_numbered(
            console,
            "Intent",
            list_intents(config),
        )
    )
    if args.intent and intent not in config.intents:
        raise MatrixError(
            f"Unknown intent '{args.intent}'. Available: {', '.join(sorted(config.intents))}."
        )

    task = args.task or Prompt.ask("[bold]Vague task[/bold]")
    if not task.strip():
        raise MatrixError("Task cannot be empty.")

    if args.context:
        context = args.context
    else:
        context = Prompt.ask(
            "[bold]Extra context[/bold] [muted](file path, glob, or text; Enter to skip)[/muted]",
            default="",
        )
    return target, intent, task, context


def _pick_numbered(console: Console, title: str, rows: list[tuple[str, str]]) -> str:
    table = Table(title=title, show_header=True, header_style="bold")
    table.add_column("#", style="cyan", justify="right", width=3)
    table.add_column("Key")
    table.add_column("Detail")
    for index, (key, detail) in enumerate(rows, start=1):
        table.add_row(str(index), key, detail)
    console.print(table)

    while True:
        choice = IntPrompt.ask(
            f"Select {title.lower()}",
            default=1,
        )
        if 1 <= choice <= len(rows):
            return rows[choice - 1][0]
        console.print(f"[warn]Pick a number between 1 and {len(rows)}.[/warn]")


def _print_preview(console: Console, preview) -> None:
    lexer = {"xml": "xml", "markdown": "markdown", "plain": "text"}.get(
        preview.wrapper, "text"
    )
    syntax = Syntax(
        preview.prompt.rstrip() + "\n",
        lexer,
        theme="monokai",
        word_wrap=True,
        line_numbers=False,
    )
    console.print(
        Panel(
            syntax,
            title=f"{preview.target_ai} / {preview.intent}",
            subtitle="preview",
            border_style="green",
        )
    )


def _print_result(console: Console, result: ExecutionResult, *, show_prompt: bool) -> None:
    rendered = result.rendered
    if show_prompt and not result.reply:
        sys.stdout.write(rendered.prompt)
        if not rendered.prompt.endswith("\n"):
            sys.stdout.write("\n")
        sys.stdout.flush()

    if result.note:
        console.print(f"[warn]{result.note}[/warn]")

    if result.copied:
        n = len(rendered.prompt)
        console.print(f"[ok]Copied {n} characters to the clipboard.[/ok]")

    if result.direct and result.reply:
        console.print()
        console.print(Panel(result.reply, title="model reply", border_style="magenta"))

    if result.saved_to:
        console.print(f"[ok]Saved JSON to {result.saved_to}[/ok]")

    if not result.copied and not result.direct:
        console.print("[warn]Nothing copied or sent. Re-run with --copy or --direct.[/warn]")


def _should_serve(argv: list[str]) -> bool:
    if not argv:
        return True
    if "--web" in argv:
        return True
    if argv[0] in {"serve", "web"}:
        return True
    flags = {
        "--host",
        "--port",
        "--no-browser",
        "--web",
        "--auth-user",
        "--auth-pass",
        "--http-user",
        "--http-pass",
    }
    first = argv[0]
    if first in flags:
        return True
    prefixes = ("--host=", "--port=", "--auth-user=", "--auth-pass=", "--http-user=", "--http-pass=")
    return first.startswith(prefixes)


def _print_top_help() -> None:
    print(
        """Assure (PEM engine)

  assure              open http://127.0.0.1:8765
  pem                 same
  assure --web --edition pro
  pem --web --host 0.0.0.0 --auth-user admin --auth-pass PASS
  pem --store-prompts --max-tokens 4096 --timeout 60
  pem --workflow redhat --critic rule
  pem mcp             stdio MCP (compile, combine, critique-rewrite, lint, export)
  pem --export mdc --class-id comparison
  cat notes.md | pem claude research "Summarize" --direct
  pem --host 0.0.0.0  also reachable on your LAN
  pem eval --dataset path.json --output results.json
  pem --ci gemini analysis "Summarize" --copy
  pem --redteam gemini analysis "Check this prompt"
  pem monitor --show-cost --show-success-rate
  pem --cli           terminal menu
  pem claude research "Compare AWS vs GCP" --copy
  pem ollama debug "fix the login 500" --local --workflow redhat --persona security --direct
"""
    )


if __name__ == "__main__":
    sys.exit(main())
