"""Assure development swarm.

Phase 1 is sequential: architect, developer, reviewer.
Phase 2 is a red-hat rewrite loop until Keep or the round cap.
Phase 3 runs two lanes at once: tester then unittest on one, documenter on the
other. ``run_workflow`` is blocking, so those calls go through
``asyncio.to_thread``.

Each role uses a PEM intent. Live targets and edition persona clamps stay in
the pipeline. Domain and case come only from the user's task and attached
files. Before Keep, a local lint pass runs py_compile, SQL paren checks,
import vs requirements, and patch integrity. It does not Send, hit
``/api/render``, or call Supabase/Stripe.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from contextvars import ContextVar, Token
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, Field

try:
    from .cost_router import (
        MODEL_ALIASES,
        TARGET_FOR,
        model_override,
        role_output_limits,
        send_model_id,
    )
    from .engine import MAX_FILE_BYTES, MatrixError, TARGET_ALIASES
    from .litellm_runner import last_completion_meta, reset_completion_meta
    from .patch_apply import (
        dump_is_truncated,
        parse_planned_paths,
        stitch_continuation,
        truncation_reason_for_body,
        write_workspace_files,
        PatchError,
    )
    from .swarm_lint import LintReport, local_lint
    from .pipelines import PipelineResult, run_workflow
    from .route import live_targets
except ImportError:
    from cost_router import (
        MODEL_ALIASES,
        TARGET_FOR,
        model_override,
        role_output_limits,
        send_model_id,
    )
    from engine import MAX_FILE_BYTES, MatrixError, TARGET_ALIASES
    from litellm_runner import last_completion_meta, reset_completion_meta
    from patch_apply import (
        dump_is_truncated,
        parse_planned_paths,
        stitch_continuation,
        truncation_reason_for_body,
        write_workspace_files,
        PatchError,
    )
    from pipelines import PipelineResult, run_workflow
    from route import live_targets
    from swarm_lint import LintReport, local_lint

ROLES = ("architect", "developer", "reviewer", "tester", "documenter")

DEFAULT_ROLE_MODELS = {
    "architect": "gemini/gemini-3.6-flash",
    "developer": "deepseek/deepseek-chat",
    "reviewer": "anthropic/claude-sonnet-4-5",
    "tester": "gemini/gemini-3.6-flash",
    "documenter": "anthropic/claude-sonnet-4-5",
}

ROLE_INTENTS = {
    "architect": "design",
    "developer": "debug",
    "reviewer": "analysis",
    "tester": "debug",
    "documenter": "research",
}

PEM_TARGETS = ("claude", "gemini", "deepseek", "kimi", "ollama", "cursor")
MAX_REDHAT_ROUNDS = 3
MAX_FIX_ROUNDS = 3
MIN_CONFIDENCE = 0.8
# Developer writes files. debug intent otherwise caps at 512 via cap_output_tokens.
ROLE_MAX_TOKENS = {"developer": 16384}
ROLE_TIMEOUT_SECONDS = {"developer": 180}
MAX_CONTINUES = 4
MAX_DEV_PARALLEL = 1
# Tester must write unittest. pytest is not in this venv.
TEST_COMMAND = "python -m unittest discover -s tests"
LOG_NAME = "prompt_matrix.swarm"
COVERAGE_UNAVAILABLE = "Data not available in current context."
_PROGRESS_HOOK: ContextVar[Callable[[str], None] | None] = ContextVar(
    "swarm_progress_hook", default=None
)
_TEST_FILE = re.compile(r"(?:^|/)(?:test_[^/]+\.py|[^/]+_test\.py)$")

_VERDICT_LINE = re.compile(
    r"^\s*verdict\s*[:\-]\s*(keep|revise|reject)\b", re.IGNORECASE | re.MULTILINE
)
_CONFIDENCE_LINE = re.compile(
    r"confidence\s*[:\-]\s*(\d+(?:\.\d+)?)\s*(%|/\s*5|/\s*10)?",
    re.IGNORECASE,
)
_LABELED_FENCE = re.compile(
    r"```[\w+-]*[:=\s]+(?P<path>[^\s`]+\.[A-Za-z0-9]{1,12})\s*\n(?P<body>.*?)```",
    re.DOTALL,
)
_HEADED_FENCE = re.compile(
    r"(?:^|\n)(?:#{1,6}\s+|FILE:\s+|Path:\s+)(?P<path>[^\s`]+\.[A-Za-z0-9]{1,12})\s*\n+```[^\n]*\n(?P<body>.*?)```",
    re.DOTALL,
)
_COVERAGE_TOTAL = re.compile(r"^TOTAL\s+\d+\s+\d+(?:\s+\d+)?\s+(\d+)%", re.MULTILINE)


class SwarmStep(BaseModel):
    role: str
    intent: str
    workflow: str = "single"
    target_ai: str
    model: str | None = None
    reply: str | None = None
    note: str | None = None
    error: str | None = None
    round: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: float | None = None


class SwarmConfig(BaseModel):
    task: str
    context_files: list[str] = Field(default_factory=list)
    target_models: dict[str, str] = Field(default_factory=dict)
    create_pr: bool = False
    direct: bool = True
    local: bool = False
    cheap: bool = False
    max_redhat_rounds: int = MAX_REDHAT_ROUNDS
    max_fix_rounds: int = MAX_FIX_ROUNDS
    min_confidence: float = MIN_CONFIDENCE
    skip_tests: bool = False
    skip_docs: bool = False
    apply_workspace: bool = False


class TestResults(BaseModel):
    passed: bool | None = None
    skipped: bool = False
    skip_reason: str | None = None
    stdout: str = ""
    stderr: str = ""
    coverage: str = COVERAGE_UNAVAILABLE
    returncode: int | None = None
    fix_rounds: int = 0


class SwarmResult(BaseModel):
    task: str
    spec: str = ""
    implementation: str = ""
    files: dict[str, str] = Field(default_factory=dict)
    diffs: dict[str, str] = Field(default_factory=dict)
    review: str = ""
    review_verdict: str = ""
    review_confidence: float | None = None
    tests: str = ""
    test_results: TestResults = Field(default_factory=TestResults)
    documentation: str = ""
    summary: str = ""
    steps: list[SwarmStep] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    targets: dict[str, str] = Field(default_factory=dict)
    redhat_rounds: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: float | None = None
    confidence: float = 0.0
    run_hash: str = ""
    patch_path: str | None = None
    lint_ok: bool | None = None
    lint_errors: list[str] = Field(default_factory=list)
    applied: bool = False

    @property
    def code(self) -> dict[str, str]:
        return self.files

    @property
    def reviewer_verdict(self) -> str:
        return self.review_verdict

    @property
    def reviewer_confidence(self) -> float | None:
        return self.review_confidence

    @property
    def confidence_score(self) -> float:
        return self.confidence

    @property
    def quality_report(self) -> str:
        return self.summary


def run_swarm(
    task: str | None = None,
    context_files: list[str] | None = None,
    target_models: dict[str, str] | None = None,
    create_pr: bool = False,
    *,
    task_description: str | None = None,
    direct: bool = True,
    local: bool = False,
    cheap: bool = False,
    max_redhat_rounds: int = MAX_REDHAT_ROUNDS,
    max_redhat_iterations: int | None = None,
    max_fix_rounds: int = MAX_FIX_ROUNDS,
    min_confidence: float = MIN_CONFIDENCE,
    skip_tests: bool = False,
    skip_docs: bool = False,
    apply_workspace: bool = False,
) -> SwarmResult:
    """Synchronous wrapper. Phase 1-2 sequential. Phase 3: tester+unittest beside documenter."""
    rounds = max_redhat_rounds if max_redhat_iterations is None else max_redhat_iterations
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            run_swarm_async(
                task=task,
                context_files=context_files,
                target_models=target_models,
                create_pr=create_pr,
                task_description=task_description,
                direct=direct,
                local=local,
                cheap=cheap,
                max_redhat_rounds=rounds,
                max_fix_rounds=max_fix_rounds,
                min_confidence=min_confidence,
                skip_tests=skip_tests,
                skip_docs=skip_docs,
                apply_workspace=apply_workspace,
            )
        )
    raise MatrixError(
        "run_swarm() cannot nest inside a running event loop. Await run_swarm_async()."
    )


async def run_swarm_async(
    task: str | None = None,
    context_files: list[str] | None = None,
    target_models: dict[str, str] | None = None,
    create_pr: bool = False,
    *,
    task_description: str | None = None,
    direct: bool = True,
    local: bool = False,
    cheap: bool = False,
    max_redhat_rounds: int = MAX_REDHAT_ROUNDS,
    max_redhat_iterations: int | None = None,
    max_fix_rounds: int = MAX_FIX_ROUNDS,
    min_confidence: float = MIN_CONFIDENCE,
    skip_tests: bool = False,
    skip_docs: bool = False,
    apply_workspace: bool = False,
) -> SwarmResult:
    """Architect → developer → reviewer (then red-hat). Tester+unittest beside documenter."""
    if max_redhat_iterations is not None:
        max_redhat_rounds = max_redhat_iterations
    description = (task or task_description or "").strip()
    if not description:
        raise MatrixError("run_swarm needs a task.")

    cfg = SwarmConfig(
        task=description,
        context_files=list(context_files or []),
        target_models=dict(target_models or {}),
        create_pr=create_pr,
        direct=direct,
        local=local,
        cheap=cheap,
        max_redhat_rounds=max_redhat_rounds,
        max_fix_rounds=max_fix_rounds,
        min_confidence=min_confidence,
        skip_tests=skip_tests,
        skip_docs=skip_docs,
        apply_workspace=apply_workspace,
    )
    log = _logger()
    file_context, originals, file_notes = load_context(cfg.context_files)
    notes = list(file_notes)
    bindings = resolve_role_targets(cfg.target_models, live=live_targets(), local=cfg.local)
    for role, (_target, _model, note) in bindings.items():
        if note:
            notes.append(note)
            log.info("%s: %s", role, note)

    result = SwarmResult(
        task=cfg.task,
        targets={role: pair[0] for role, pair in bindings.items()},
        notes=notes,
        run_hash=_run_hash(cfg.task),
    )
    context = file_context
    log.info("start hash=%s task=%s", result.run_hash, cfg.task.replace("\n", " ")[:200])
    _progress("phase 1: spec, code, review")

    arch = run_architect(cfg.task, context, bindings, cfg)
    _ingest(result, arch)
    result.spec = arch.reply or ""
    if arch.error and not result.spec:
        result.notes.append(arch.error)
        _finalize(result, originals, cfg, log)
        return result

    planned = parse_planned_paths(result.spec)
    if planned:
        result.notes.append("developer subtasks: " + ", ".join(planned))
        log.info("developer subtasks=%s", planned)
    for step in run_developer_jobs(cfg.task, result.spec, context, bindings, cfg, paths=planned):
        _ingest(result, step)
        if step.reply:
            result.implementation = _join_impl(result.implementation, step.reply)
    result.files, skipped = accepted_files(result.implementation)
    for note in skipped:
        result.notes.append(note)
    log.info("code files=%s", list(result.files) or "(raw implementation, no path fences)")

    result = run_redhat_loop(cfg, result, context, bindings, originals)

    _progress("phase 3: tester plus self-test, documenter in parallel")
    await _phase3_tests_and_docs(cfg, result, context, bindings)

    _finalize(result, originals, cfg, log)
    return result


async def _phase3_tests_and_docs(
    cfg: SwarmConfig,
    result: SwarmResult,
    context: str,
    bindings,
) -> None:
    """Tester then unittest on one lane, documenter on the other. run_workflow is blocking."""
    code = result.implementation

    async def test_lane() -> SwarmStep | None:
        if cfg.skip_tests:
            result.test_results = TestResults(skipped=True, skip_reason="skipped by --skip-tests")
            result.tests = "Skipped"
            return None
        tes = await asyncio.to_thread(run_tester, cfg.task, code, context, bindings, cfg)
        _ingest(result, tes)
        result.tests = tes.reply or ""
        files, skipped = accepted_files(result.tests)
        for note in skipped:
            result.notes.append(note)
        for path, body in files.items():
            result.files.setdefault(path, body)
        _self_test_loop(cfg, result, context, bindings)
        return tes

    async def docs_lane() -> SwarmStep | None:
        if cfg.skip_docs:
            result.documentation = "Skipped"
            return None
        return await asyncio.to_thread(run_documenter, cfg.task, code, context, bindings, cfg)

    _tes, docs = await asyncio.gather(test_lane(), docs_lane())
    if docs is not None:
        _ingest(result, docs)
        result.documentation = docs.reply or ""
        files, skipped = accepted_files(result.documentation)
        for note in skipped:
            result.notes.append(note)
        for path, body in files.items():
            result.files.setdefault(path, body)


def run_architect(task: str, context: str, bindings, cfg: SwarmConfig) -> SwarmStep:
    _progress(f"architect on {bindings['architect'][0]}")
    return _call("architect", _architect_task(task), context, bindings, cfg=cfg)


def run_developer(task: str, spec: str, context: str, bindings, cfg: SwarmConfig) -> SwarmStep:
    """One-shot helper. Multi-file tasks use run_developer_jobs()."""
    steps = run_developer_jobs(task, spec, context, bindings, cfg)
    return steps[-1] if steps else _empty_developer_step(bindings)


def run_developer_jobs(
    task: str,
    spec: str,
    context: str,
    bindings,
    cfg: SwarmConfig,
    *,
    paths: list[str] | None = None,
    kind: str = "implement",
    review: str = "",
    unittest_output: str = "",
    current_files: dict[str, str] | None = None,
    cycle: int = 0,
    workflow: str = "single",
    persona: str | None = None,
    critic: str | None = None,
) -> list[SwarmStep]:
    """One developer completion per architect path. Sequential (safer than a pool)."""
    planned = list(paths if paths is not None else parse_planned_paths(spec))
    target = bindings["developer"][0]
    if len(planned) > MAX_DEV_PARALLEL:
        _progress(
            f"developer on {target}: {len(planned)} files, sequential (cap {MAX_DEV_PARALLEL})"
        )
    else:
        _progress(f"developer on {target}")
    if not planned:
        prompt = _developer_prompt(
            kind, task, spec, path=None, review=review, unittest_output=unittest_output
        )
        return [
            _complete_developer(
                prompt,
                context,
                bindings,
                cfg,
                workflow=workflow,
                persona=persona,
                critic=critic,
                cycle=cycle,
            )
        ]
    steps: list[SwarmStep] = []
    done: list[str] = []
    current_files = current_files or {}
    for path in planned:
        prompt = _developer_prompt(
            kind,
            task,
            spec,
            path=path,
            review=review,
            unittest_output=unittest_output,
            done=done,
            current_body=current_files.get(path, ""),
        )
        steps.append(
            _complete_developer(
                prompt,
                context,
                bindings,
                cfg,
                workflow=workflow,
                persona=persona,
                critic=critic,
                cycle=cycle,
                path=path,
            )
        )
        done.append(path)
    return steps


def run_reviewer(
    task: str,
    spec: str,
    code: str,
    context: str,
    bindings,
    cfg: SwarmConfig,
    *,
    lint: LintReport | None = None,
) -> SwarmStep:
    _progress(f"reviewer on {bindings['reviewer'][0]}")
    return _call(
        "reviewer", _reviewer_task(task, spec, code, lint=lint), context, bindings, cfg=cfg
    )


def run_redhat_loop(
    cfg: SwarmConfig,
    result: SwarmResult,
    context: str,
    bindings,
    originals: dict[str, str] | None = None,
) -> SwarmResult:
    """Review, then red-hat rewrite until Keep or max rounds.

    Local lint runs before the reviewer Send. A lint failure is Revise with no
    reviewer model call.
    """
    originals = originals or {}
    cap = max(0, int(cfg.max_redhat_rounds))
    rounds = 0
    _progress("phase 2: review and red-hat if needed")
    while True:
        diffs = file_diffs(originals, result.files)
        lint = local_lint(
            result.files,
            spec=result.spec,
            implementation=result.implementation,
            diffs=diffs,
        )
        result.lint_ok = lint.ok
        result.lint_errors = list(lint.errors)
        if lint.ok:
            _progress("local lint pass")
            rev = run_reviewer(
                cfg.task, result.spec, result.implementation, context, bindings, cfg, lint=lint
            )
            _ingest(result, rev)
            review_text = rev.reply or ""
            verdict, confidence = parse_review(review_text)
            result.review = review_text
            result.review_verdict = verdict
            result.review_confidence = confidence
            if rev.error and not review_text:
                result.notes.append(rev.error or "Reviewer did not reply.")
        else:
            _progress("local lint fail; skipping reviewer Send")
            review_text = _lint_revise_review(lint)
            verdict, confidence = parse_review(review_text)
            result.review = review_text
            result.review_verdict = "Revise"
            result.review_confidence = 0.0
            result.notes.append("local lint failed; Keep blocked")
        _logger().info("review verdict=%s confidence=%s lint=%s", verdict, confidence, lint.ok)
        if result.review_verdict == "Keep" and not lint.ok:
            result.review_verdict = "Revise"
        if result.review_verdict == "Keep" or rounds >= cap:
            break
        if lint.ok and not review_text:
            break
        rounds += 1
        result.redhat_rounds = rounds
        paths = list(result.files) or parse_planned_paths(result.spec)
        _progress(f"red-hat {rounds}/{cap} on {bindings['developer'][0]} (security)")
        revised_any = False
        last_error = None
        for revised in run_developer_jobs(
            cfg.task,
            result.spec,
            context,
            bindings,
            cfg,
            paths=paths,
            kind="revise",
            review=review_text,
            current_files=result.files,
            workflow="redhat",
            persona="security",
            critic=bindings["reviewer"][0],
            cycle=rounds,
        ):
            _ingest(result, revised)
            files, skipped = accepted_files(revised.reply or "")
            for note in skipped:
                result.notes.append(note)
            if files:
                result.files.update(files)
                revised_any = True
            elif revised.error:
                last_error = revised.error
        if revised_any:
            result.implementation = _files_to_impl(result.files)
        elif last_error:
            result.notes.append(last_error)
            break
    return result


def run_tester(task: str, code: str, context: str, bindings, cfg: SwarmConfig) -> SwarmStep:
    _progress(f"tester on {bindings['tester'][0]}")
    return _call("tester", _tester_task(task, code), context, bindings, cfg=cfg)


def run_documenter(task: str, code: str, context: str, bindings, cfg: SwarmConfig) -> SwarmStep:
    _progress(f"documenter on {bindings['documenter'][0]}")
    return _call("documenter", _documenter_task(task, code), context, bindings, cfg=cfg)


def run_developer_fix(
    cfg: SwarmConfig,
    result: SwarmResult,
    context: str,
    bindings,
    unittest_output: str,
    cycle: int,
) -> SwarmStep:
    _progress(f"self-test fix {cycle}/{cfg.max_fix_rounds} on {bindings['developer'][0]}")
    paths = [path for path in result.files if not _TEST_FILE.search(path.replace("\\", "/"))]
    steps = run_developer_jobs(
        cfg.task,
        result.spec,
        context,
        bindings,
        cfg,
        paths=paths[:1] if paths else None,
        kind="fix",
        unittest_output=unittest_output,
        current_files=result.files,
        cycle=cycle,
    )
    return steps[-1] if steps else _empty_developer_step(bindings)


def load_context(paths: list[str] | None) -> tuple[str, dict[str, str], list[str]]:
    """Read optional paths and return (context blob, originals, notes)."""
    originals: dict[str, str] = {}
    notes: list[str] = []
    chunks: list[str] = []
    root = Path(__file__).resolve().parent.parent
    for raw in paths or []:
        label = str(raw).strip()
        if not label:
            continue
        resolved = _resolve_context_path(label, root)
        if resolved is None:
            notes.append(f"Missing context file: {label}")
            continue
        try:
            data = resolved.read_bytes()
            if len(data) > MAX_FILE_BYTES:
                data = data[:MAX_FILE_BYTES] + b"\n... [truncated]\n"
            text = data.decode("utf-8", errors="replace")
        except OSError as exc:
            notes.append(f"Could not read {label}: {exc}")
            continue
        try:
            key = str(resolved.relative_to(root)).replace("\\", "/")
        except ValueError:
            key = label
        if key != label:
            notes.append(f"Resolved context {label} -> {key}")
        originals[key] = text
        chunks.append(f"===== {key} =====\n{text}")
    return "\n\n".join(chunks), originals, notes


def _resolve_context_path(label: str, root: Path) -> Path | None:
    raw = Path(label).expanduser()
    candidates: list[Path] = []
    if raw.is_absolute():
        candidates.append(raw)
    else:
        candidates.extend((Path.cwd() / raw, root / raw))
        posix = str(raw).replace("\\", "/")
        if "/" not in posix:
            candidates.append(root / "prompt_matrix" / posix)
            candidates.append(root / "prompt_matrix" / "templates" / posix)
    seen: set[str] = set()
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        marker = str(resolved)
        if marker in seen:
            continue
        seen.add(marker)
        if resolved.is_file():
            return resolved
    return None


load_context_files = load_context


def parse_code(text: str) -> dict[str, str]:
    """Pull path → body pairs from headed or labeled fenced blocks."""
    files: dict[str, str] = {}
    blob = text or ""
    for pattern in (_LABELED_FENCE, _HEADED_FENCE):
        for match in pattern.finditer(blob):
            path = match.group("path").strip().strip("`")
            if "/" not in path and "\\" not in path and path.count(".") != 1:
                if not re.match(r"^[\w.-]+\.[A-Za-z0-9]{1,12}$", path):
                    continue
            files[path] = match.group("body").replace("\r\n", "\n")
    return files


extract_files = parse_code


def log_developer_attempt(filepath: str, content: str, *, log_dir: Path | None = None) -> None:
    """Append a short dump preview to logs/swarm_attempt.log. logs/ is gitignored."""
    target = (
        Path(log_dir) if log_dir is not None else Path(__file__).resolve().parent.parent / "logs"
    )
    try:
        target.mkdir(parents=True, exist_ok=True)
        preview = (content or "")[:500]
        with (target / "swarm_attempt.log").open("a", encoding="utf-8") as handle:
            handle.write(f"=== ATTEMPT: {filepath} ===\n")
            handle.write(preview)
            handle.write("...\n\n" if len(content or "") > 500 else "\n\n")
    except OSError:
        return


def accepted_files(text: str) -> tuple[dict[str, str], list[str]]:
    """parse_code then drop truncated bodies so HTML dumps never land in templates."""
    kept: dict[str, str] = {}
    skipped: list[str] = []
    blob = text or ""
    report = dump_is_truncated(blob)
    parsed = parse_code(blob)
    if not parsed and report:
        log_developer_attempt("(truncated dump)", blob)
        skipped.append("implementation dump looks truncated (" + "; ".join(report.reasons) + ")")
        return kept, skipped
    for path, body in parsed.items():
        log_developer_attempt(path, body)
        hint = truncation_reason_for_body(path, body)
        if hint:
            skipped.append(f"not applying {hint}")
            continue
        file_report = dump_is_truncated(body, path=path)
        if file_report:
            skipped.append(f"not applying {path} ({'; '.join(file_report.reasons)})")
            continue
        kept[path] = body
    return kept, skipped


def _join_impl(existing: str, chunk: str) -> str:
    left = (existing or "").rstrip()
    right = (chunk or "").strip()
    if not right:
        return existing or ""
    if not left:
        return right + "\n"
    return left + "\n\n" + right + "\n"


def _files_to_impl(files: dict[str, str]) -> str:
    parts: list[str] = []
    for path, body in files.items():
        parts.append(f"### {path}\n```\n{body.rstrip()}\n```")
    return "\n\n".join(parts) + ("\n" if parts else "")


def _empty_developer_step(bindings) -> SwarmStep:
    target, model, _note = bindings["developer"]
    return SwarmStep(
        role="developer", intent=ROLE_INTENTS["developer"], target_ai=target, model=model
    )


def file_diffs(originals: dict[str, str], files: dict[str, str]) -> dict[str, str]:
    """Unified diffs. Existing files are edits. Missing paths are new-file diffs."""
    import difflib

    diffs: dict[str, str] = {}
    for path, new in files.items():
        old = _original_for(path, originals)
        if old is None:
            old_lines: list[str] = []
            fromfile = "/dev/null"
        elif old == new:
            continue
        else:
            old_lines = old.splitlines(True)
            fromfile = f"a/{path}"
        diff = "".join(
            difflib.unified_diff(
                old_lines,
                new.splitlines(True),
                fromfile=fromfile,
                tofile=f"b/{path}",
            )
        )
        if diff:
            diffs[path] = diff
    return diffs


def parse_review(text: str) -> tuple[str, float | None]:
    """Return (Keep|Revise|Reject, confidence 0-1)."""
    blob = text or ""
    verdict = "Keep"
    match = _VERDICT_LINE.search(blob)
    if match:
        verdict = match.group(1).title()
    else:
        folded = blob.casefold()
        if re.search(r"\breject\b", folded):
            verdict = "Reject"
        elif re.search(r"\brevise\b", folded):
            verdict = "Revise"
    confidence = None
    found = _CONFIDENCE_LINE.search(blob)
    if found:
        confidence = _normalize_confidence(float(found.group(1)), found.group(2))
    return verdict, confidence


def run_pytest(files: dict[str, str]) -> TestResults:
    """Run generated unittest modules in a temp directory. Does not write the live tree."""
    tests = {
        path: body
        for path, body in (files or {}).items()
        if _TEST_FILE.search(path.replace("\\", "/"))
    }
    if not tests:
        return TestResults(
            skipped=True, skip_reason="No unittest modules extracted from the tester output."
        )
    tmp: str | None = None
    try:
        tmp = tempfile.mkdtemp(prefix="pem-swarm-")
        root = Path(tmp)
        suite = root / "tests"
        suite.mkdir()
        (suite / "__init__.py").write_text("", encoding="utf-8")
        for path, body in tests.items():
            dest = suite / Path(path).name
            dest.write_text(body, encoding="utf-8")
        repo = Path(__file__).resolve().parent.parent
        env = os.environ.copy()
        env["PYTHONPATH"] = str(repo) + os.pathsep + env.get("PYTHONPATH", "")
        argv = [sys.executable, *TEST_COMMAND.split()[1:]]
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(root),
            env=env,
        )
    except FileNotFoundError:
        return TestResults(skipped=True, skip_reason="python is not installed.")
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        err = (exc.stderr or "") if isinstance(exc.stderr, str) else "unittest timed out."
        return TestResults(passed=False, stdout=out, stderr=err, returncode=-1)
    except OSError as exc:
        return TestResults(skipped=True, skip_reason=str(exc))
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    blob = stdout + "\n" + stderr
    coverage = COVERAGE_UNAVAILABLE
    found = _COVERAGE_TOTAL.search(blob)
    if found:
        coverage = f"{found.group(1)}%"
    return TestResults(
        passed=proc.returncode == 0,
        stdout=stdout,
        stderr=stderr,
        coverage=coverage,
        returncode=proc.returncode,
    )


def compute_confidence_score(
    verdict: str,
    review_confidence: float | None,
    tests_passed: bool | None,
    files: dict[str, str],
) -> float:
    """0-1 score. Keep 0.3 plus 0.1 * reviewer confidence, tests 0.4, size 0.2.

    Does not round up to the 0.8 merge gate.
    """
    score = 0.0
    if (verdict or "").strip().title() == "Keep":
        score += 0.3
        if review_confidence is not None:
            score += 0.1 * max(0.0, min(1.0, float(review_confidence)))
    if tests_passed is True:
        score += 0.4
    score += 0.2 * _size_heuristic(files)
    return round(min(1.0, max(0.0, score)), 3)


def generate_summary(result: SwarmResult, originals: dict[str, str] | None = None) -> str:
    originals = originals or {}
    approved = result.review_verdict == "Keep"
    tests = result.test_results
    if tests.skipped:
        test_line = f"Skipped ({tests.skip_reason or 'no reason'})"
    elif tests.passed is True:
        test_line = "Pass"
    elif tests.passed is False:
        test_line = "Fail"
    else:
        test_line = "Not run"
    gate = (
        "meets"
        if result.confidence >= MIN_CONFIDENCE and approved and tests.passed is True
        else "does not meet"
    )
    parts = [
        "# Swarm quality report",
        "",
        f"Run hash: {result.run_hash or '(none)'}",
        f"Merge gate (0.8): {gate} the 0.8 confidence gate.",
        "",
        "## Task",
        result.task,
        "",
        "## Spec",
        result.spec or "(none)",
        "",
        "## Code",
    ]
    if result.files:
        for path, body in result.files.items():
            kind = "new file" if _original_for(path, originals) is None else "updated"
            parts.extend(["", f"### {path} ({kind})", "```", body.rstrip(), "```"])
    else:
        parts.append(result.implementation or "(none)")
    if result.diffs:
        parts.extend(["", "## Diffs"])
        for path, diff in result.diffs.items():
            parts.extend(["", f"### {path}", "```diff", diff.rstrip(), "```"])
    lint_line = "not run"
    if result.lint_ok is True:
        lint_line = "PASS"
    elif result.lint_ok is False:
        lint_line = "FAIL"
    parts.extend(
        [
            "",
            "## Local lint",
            lint_line,
        ]
    )
    if result.lint_errors:
        parts.extend(f"- {item.splitlines()[0]}" for item in result.lint_errors)
    rev_conf = "n/a" if result.review_confidence is None else f"{result.review_confidence:.2f}"
    approved_line = (
        "approved (Keep)" if approved else f"not approved ({result.review_verdict or 'none'})"
    )
    parts.extend(
        [
            "",
            "## Review",
            f"Verdict: {result.review_verdict or '(none)'} ({approved_line})",
            f"Reviewer confidence: {rev_conf}",
            "",
            result.review or "(none)",
            "",
            "## Tests",
            f"Self-test: {test_line}",
            f"Coverage: {tests.coverage or COVERAGE_UNAVAILABLE}",
            f"Fix rounds: {tests.fix_rounds}",
            "",
            result.tests or "(none)",
            "",
            "## Documentation",
            result.documentation or "(none)",
            "",
            "## Confidence",
            f"Overall: {result.confidence:.3f} (Keep 0.3 + 0.1 * reviewer confidence, tests pass 0.4, size 0.2).",
            "This value is not raised to 0.8.",
        ]
    )
    if result.patch_path:
        parts.extend(["", f"Patch: {result.patch_path}"])
    if result.notes:
        parts.extend(["", "## Notes", *[f"- {note}" for note in result.notes]])
    if result.targets:
        used = ", ".join(f"{role}={target}" for role, target in result.targets.items())
        parts.extend(["", f"Targets: {used}"])
        parts.append(f"Red-hat rounds: {result.redhat_rounds}")
    return "\n".join(parts).rstrip() + "\n"


def create_pull_request(result: SwarmResult, *, enabled: bool, min_confidence: float) -> str | None:
    """Write logs/swarm.patch. Optionally try gh. Never git add, commit, or push."""
    root = Path(__file__).resolve().parent.parent
    patch_dir = root / "logs"
    patch_dir.mkdir(parents=True, exist_ok=True)
    patch_path = patch_dir / "swarm.patch"
    blob = _combined_patch(result)
    patch_path.write_text(blob, encoding="utf-8")
    result.patch_path = str(patch_path)
    _logger().info("wrote patch %s (%s bytes)", patch_path, patch_path.stat().st_size)
    if not enabled:
        return str(patch_path)
    if result.lint_ok is False:
        result.notes.append(f"Skipped gh: local lint failed. Patch is at {patch_path}.")
        return str(patch_path)
    if result.confidence < min_confidence:
        result.notes.append(
            f"Skipped gh: confidence {result.confidence:.3f} is below the {min_confidence} merge gate. "
            f"Patch is at {patch_path}."
        )
        return str(patch_path)
    title = (result.task or "swarm").strip().split("\n", 1)[0][:72]
    try:
        proc = subprocess.run(
            [
                "gh",
                "pr",
                "create",
                "--title",
                title,
                "--body",
                (result.quality_report or "")[:4000],
            ],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(root),
        )
    except FileNotFoundError:
        result.notes.append(f"gh not found. Apply the patch at {patch_path}.")
        return str(patch_path)
    except OSError as exc:
        result.notes.append(f"gh failed ({exc}). Patch is at {patch_path}.")
        return str(patch_path)
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode == 0:
        result.notes.append(out.strip() or f"gh pr create succeeded. Patch also at {patch_path}.")
    else:
        result.notes.append(
            "gh pr create did not open a PR (no new commits were made). "
            f"Apply the patch at {patch_path}."
        )
        _logger().info("gh pr create exit %s\n%s", proc.returncode, out[:2000])
    return str(patch_path)


def resolve_role_targets(
    target_models: dict[str, str] | None,
    *,
    live: list[str] | None = None,
    local: bool = False,
) -> dict[str, tuple[str, str | None, str | None]]:
    """Map each role to a PEM target and optional LiteLLM id."""
    live = list(live or [])
    if local:
        live = ["ollama"] if "ollama" in live or not live else live
    merged = dict(DEFAULT_ROLE_MODELS)
    for key, value in (target_models or {}).items():
        role = (key or "").strip().lower()
        if role in ROLES and str(value).strip():
            merged[role] = str(value).strip()

    out: dict[str, tuple[str, str | None, str | None]] = {}
    for role in ROLES:
        target, model = bind_model(merged[role])
        note = None
        if local:
            target, model, note = "ollama", None, None
        elif target == "cursor" or (live and target not in live):
            fallback = live[0] if live else target
            note = f"{role}: {target} was down, used {fallback}."
            if target == "cursor":
                note = f"{role}: cursor has no chat API, used {fallback}."
            target, model = fallback, None
        out[role] = (target, model, note)
    return out


def bind_model(name: str) -> tuple[str, str | None]:
    """Turn a PEM target, pricing key, or LiteLLM id into (target, send id)."""
    raw = (name or "").strip()
    if not raw:
        return "gemini", None
    lowered = raw.lower()
    aliased = TARGET_ALIASES.get(lowered, lowered)
    if aliased in PEM_TARGETS:
        return aliased, None
    key = _pricing_key(raw)
    if key and key in TARGET_FOR:
        return TARGET_FOR[key], send_model_id(key)
    if "/" in raw:
        prefix, suffix = raw.split("/", 1)
        prefix = TARGET_ALIASES.get(prefix.lower(), prefix.lower())
        suffix_key = _pricing_key(suffix) or _pricing_key(raw)
        if suffix_key and suffix_key in TARGET_FOR:
            return TARGET_FOR[suffix_key], send_model_id(suffix_key, raw)
        if prefix in PEM_TARGETS:
            return prefix, raw
    return "gemini", raw


def _self_test_loop(cfg: SwarmConfig, result: SwarmResult, context: str, bindings) -> SwarmResult:
    cap = max(0, int(cfg.max_fix_rounds))
    pytest_result = run_pytest(result.files)
    rounds = 0
    while pytest_result.passed is False and not pytest_result.skipped and rounds < cap:
        rounds += 1
        pytest_result.fix_rounds = rounds
        blob = (pytest_result.stdout or "") + "\n" + (pytest_result.stderr or "")
        fixed = run_developer_fix(cfg, result, context, bindings, blob, rounds)
        _ingest(result, fixed)
        if fixed.reply:
            files, skipped = accepted_files(fixed.reply)
            for note in skipped:
                result.notes.append(note)
            result.files.update(files)
            result.implementation = (
                _files_to_impl(result.files)
                if result.files
                else _join_impl(result.implementation, fixed.reply)
            )
        pytest_result = run_pytest(result.files)
        pytest_result.fix_rounds = rounds
    result.test_results = pytest_result
    _logger().info(
        "self-test passed=%s skipped=%s fixes=%s",
        pytest_result.passed,
        pytest_result.skipped,
        pytest_result.fix_rounds,
    )
    return result


def _finalize(
    result: SwarmResult, originals: dict[str, str], cfg: SwarmConfig, log: logging.Logger
) -> None:
    result.diffs = file_diffs(originals, result.files)
    result.confidence = compute_confidence_score(
        result.review_verdict,
        result.review_confidence,
        result.test_results.passed,
        result.files,
    )
    result.summary = generate_summary(result, originals)
    create_pull_request(result, enabled=cfg.create_pr, min_confidence=cfg.min_confidence)
    _apply_workspace(result, cfg)
    result.summary = generate_summary(result, originals)
    _write_log_tail(log, result)


def _apply_workspace(result: SwarmResult, cfg: SwarmConfig) -> None:
    if not cfg.apply_workspace:
        return
    root = Path(__file__).resolve().parent.parent
    if result.lint_ok is False:
        result.notes.append(
            "workspace not written: local lint failed. Patch is at logs/swarm.patch."
        )
        result.applied = False
        return
    if not result.files:
        result.applied = False
        return
    try:
        note = write_workspace_files(result.files, root=root)
        result.notes.append("workspace " + note)
        result.applied = True
    except PatchError as exc:
        result.notes.append(f"workspace not written: {exc}")
        result.applied = False


def _call(
    role: str,
    task: str,
    context: str,
    bindings: dict[str, tuple[str, str | None, str | None]],
    *,
    cfg: SwarmConfig,
    workflow: str = "single",
    persona: str | None = None,
    critic: str | None = None,
    cycle: int = 0,
) -> SwarmStep:
    target, model, _note = bindings[role]
    intent = ROLE_INTENTS[role]
    log = _logger()
    log.info(
        "call role=%s intent=%s workflow=%s target=%s model=%s round=%s",
        role,
        intent,
        workflow,
        target,
        model,
        cycle,
    )
    step = SwarmStep(
        role=role,
        intent=intent,
        workflow=workflow,
        target_ai=target,
        model=model,
        round=cycle,
    )
    reset_completion_meta()
    hop = int(ROLE_TIMEOUT_SECONDS.get(role) or 60)
    workflow_sec = hop * 3 + 45 if workflow == "redhat" else hop + 45
    previous_wf = os.environ.get("PEM_WORKFLOW_TIMEOUT")
    os.environ["PEM_WORKFLOW_TIMEOUT"] = str(max(workflow_sec, 90))
    try:
        with role_output_limits(
            max_tokens=ROLE_MAX_TOKENS.get(role),
            timeout=ROLE_TIMEOUT_SECONDS.get(role),
        ):
            with model_override(target, model):
                pipe: PipelineResult = run_workflow(
                    target,
                    intent,
                    task,
                    context,
                    workflow=workflow,
                    critic=critic,
                    persona=persona,
                    ground=False,
                    direct=cfg.direct,
                    copy=False,
                    local=cfg.local,
                    lint=cfg.direct,
                    cheap=cfg.cheap,
                )
    except MatrixError as exc:
        step.error = str(exc)
        log.error("%s failed: %s", role, exc)
        return step
    finally:
        if previous_wf is None:
            os.environ.pop("PEM_WORKFLOW_TIMEOUT", None)
        else:
            os.environ["PEM_WORKFLOW_TIMEOUT"] = previous_wf

    step.target_ai = pipe.target_ai
    step.model = pipe.routed_model or model
    step.reply = pipe.reply
    step.note = pipe.note
    if not pipe.reply and cfg.direct:
        failed = next((item.error for item in pipe.steps if item.error), None)
        step.error = pipe.note or failed or f"{role} returned no reply."
    log.info(
        "done role=%s tokens_in=%s tokens_out=%s note=%s",
        role,
        pipe.input_tokens,
        pipe.output_tokens,
        (pipe.note or "")[:240],
    )
    if pipe.reply:
        log.info("output role=%s\n%s", role, pipe.reply)
    step.input_tokens = pipe.input_tokens
    step.output_tokens = pipe.output_tokens
    step.estimated_cost = pipe.estimated_cost
    return step


def _complete_developer(
    task: str,
    context: str,
    bindings,
    cfg: SwarmConfig,
    *,
    workflow: str = "single",
    persona: str | None = None,
    critic: str | None = None,
    cycle: int = 0,
    path: str | None = None,
) -> SwarmStep:
    step = _call(
        "developer",
        task,
        context,
        bindings,
        cfg=cfg,
        workflow=workflow,
        persona=persona,
        critic=critic,
        cycle=cycle,
    )
    if not cfg.direct:
        return step
    continues = 0
    while _developer_needs_continue(step, path) and continues < MAX_CONTINUES:
        continues += 1
        _progress(
            f"developer continue {continues}/{MAX_CONTINUES}" + (f" ({path})" if path else "")
        )
        more = _call(
            "developer",
            _continue_task(step.reply or "", path=path),
            context,
            bindings,
            cfg=cfg,
            workflow="single",
            cycle=cycle,
        )
        step = _merge_developer_steps(step, more)
    if _developer_needs_continue(step, path):
        extra = "truncated dump after continues; not applying incomplete files"
        step.note = f"{step.note}; {extra}" if step.note else extra
    return step


def _developer_needs_continue(step: SwarmStep, path: str | None) -> bool:
    if step.error and not (step.reply or "").strip():
        return False
    meta = last_completion_meta()
    if meta.hit_length:
        return True
    blob = step.reply or ""
    report = dump_is_truncated(blob, path=path, finish_reason=meta.finish_reason)
    if report:
        return True
    files = parse_code(blob)
    if path:
        body = files.get(path)
        if body is None:
            return blob.count("```") % 2 == 1
        return truncation_reason_for_body(path, body) is not None
    for file_path, body in files.items():
        if truncation_reason_for_body(file_path, body):
            return True
    return False


def _merge_developer_steps(first: SwarmStep, second: SwarmStep) -> SwarmStep:
    first.reply = stitch_continuation(first.reply or "", second.reply or "")
    first.input_tokens += second.input_tokens
    first.output_tokens += second.output_tokens
    if second.estimated_cost is not None:
        first.estimated_cost = (first.estimated_cost or 0.0) + second.estimated_cost
    if second.note:
        first.note = f"{first.note}; {second.note}" if first.note else second.note
    if second.error and not (second.reply or "").strip():
        first.error = second.error
    elif second.reply:
        first.error = None
    return first


def _continue_task(previous: str, *, path: str | None) -> str:
    label = path or "the file you were writing"
    tail = (previous or "")[-2000:]
    return (
        f"Your previous reply was cut off while writing {label}. "
        "Continue from the exact next character. Do not repeat already-written content. "
        "Close any open markdown fence. If the file is HTML, finish through </html>. "
        "Do not start a different path.\n\n"
        "Tail of the cut dump:\n"
        f"{tail}\n"
    )


def _ingest(result: SwarmResult, step: SwarmStep) -> None:
    result.steps.append(step)
    result.input_tokens += step.input_tokens
    result.output_tokens += step.output_tokens
    if step.estimated_cost is not None:
        result.estimated_cost = (result.estimated_cost or 0.0) + step.estimated_cost
    if step.note:
        result.notes.append(f"{step.role}: {step.note}")
    if step.error:
        result.notes.append(step.error)


def _architect_task(task: str) -> str:
    return (
        "Produce a detailed implementation spec. Do not write application code yet.\n\n"
        f"Feature task:\n{task}\n\n"
        "Cover goals, constraints from the attached files, the proposed design, "
        "tradeoffs, and concrete next steps for the developer. "
        "Domain and case come only from this task and the attached files.\n\n"
        "End with this section, one path per line, no file bodies:\n\n"
        "## Files to write\n"
        "- relative/path.ext\n"
        "\n"
        "List every path the developer must create or edit. The developer writes "
        "one file per turn from this list."
    )


def _developer_task(task: str, spec: str) -> str:
    return _developer_prompt("implement", task, spec, path=None)


def _developer_prompt(
    kind: str,
    task: str,
    spec: str,
    *,
    path: str | None,
    review: str = "",
    unittest_output: str = "",
    done: list[str] | None = None,
    current_body: str = "",
) -> str:
    already = ", ".join(done or []) or "(none)"
    one_file = (
        f"Write exactly one file in this turn: {path}\n"
        "Emit one complete ### path fence for that path, then stop.\n"
        "Do not start another file. A later turn requests the next path.\n"
        "Close the markdown fence. Never paste truncated HTML into a template.\n"
        f"Already written in this swarm: {already}\n\n"
        f"### {path}\n"
        "```\n"
        "full file content\n"
        "```\n"
    )
    many = (
        "Implement the spec as complete file contents, not patches and not hypotheses.\n"
        "The deliverable is the files. You may list brief notes after the files.\n"
        "Write ONE file per reply. If several files are required, write the first "
        "complete file and stop. Do not dump every file in one completion.\n"
        "Never paste truncated HTML. Close every markdown fence.\n\n"
        "Write each file as:\n\n"
        "### relative/path.ext\n"
        "```\n"
        "full file content\n"
        "```\n"
    )
    header = one_file if path else many
    if kind == "revise":
        body = (
            "Revise this implementation using the reviewer's required fixes. "
            "Output complete file contents in the ### path + fence convention.\n\n"
            f"{header}\n"
            f"Original task:\n{task}\n\n"
            f"Spec:\n{spec}\n\n"
        )
        if current_body:
            body += f"Current file contents:\n{current_body}\n\n"
        else:
            body += f"Current implementation:\n(see spec)\n\n"
        body += f"Reviewer feedback:\n{review}\n"
        return body
    if kind == "fix":
        return (
            "Unittest failed on the generated tests. Fix the implementation. "
            "Output complete file contents in the ### path + fence convention.\n\n"
            f"{header}\n"
            f"Original task:\n{task}\n\n"
            f"Spec:\n{spec}\n\n"
            f"Unittest output:\n{unittest_output}\n"
        )
    return f"{header}\n" f"Original task:\n{task}\n\n" f"Architect spec:\n{spec}\n"


def _lint_revise_review(lint: LintReport) -> str:
    return (
        "Verdict: Revise\n"
        "Confidence: 0.0\n"
        "Local lint failed. The developer must fix these before Keep. "
        "Do not run pem --direct, pem eval, /api/render, or live API calls.\n" + lint.text()
    )


def _reviewer_task(task: str, spec: str, code: str, *, lint: LintReport | None = None) -> str:
    lint_block = (
        (lint.text() if lint is not None else "Local lint: not run.")
        + "\nLocal lint is compile-time only. Do not run pem --direct, pem eval, "
        "/api/render, or live Supabase/Stripe/LLM calls to verify this code.\n"
        "Lint PASS is not a Keep. Review the design against the spec.\n"
        "If a dump is truncated mid-function, verdict must be Revise.\n"
    )
    return (
        "Review this implementation for bugs, security issues, style violations, "
        "and completeness against the spec.\n\n"
        "Before you finalize Keep or Revise:\n"
        "1. Use the local lint result below (already run on the patched files).\n"
        "2. If lint failed, verdict is Revise and list the lint errors.\n"
        "3. If lint passed, still do the architectural review.\n\n"
        f"{lint_block}\n"
        "End with exactly these two lines:\n"
        "Verdict: Keep | Revise | Reject\n"
        "Confidence: <number between 0 and 1>\n"
        "If the verdict is Revise or Reject, list required fixes as numbered items.\n\n"
        f"Original task:\n{task}\n\n"
        f"Spec:\n{spec}\n\n"
        f"Implementation:\n{code}\n"
    )


def _tester_task(task: str, code: str) -> str:
    return (
        "Write unittest.TestCase tests for this implementation. Do not use pytest; "
        "it is not installed. "
        f"TEST_COMMAND: {TEST_COMMAND}\n"
        "Output complete test modules using the ### path + fence convention "
        "(for example ### tests/test_feature.py). Cover the happy path "
        "and important edge cases. Do not invent APIs that are not in the implementation.\n\n"
        f"Original task:\n{task}\n\n"
        f"Implementation:\n{code}\n"
    )


def _documenter_task(task: str, code: str) -> str:
    return (
        "Write MkDocs markdown for this feature. Output docs files using the "
        "### path + fence convention (for example ### docs/feature.md). Describe "
        "what it does, how to use it, and constraints that appear in the implementation. "
        "Do not invent metrics, dates, or publication names.\n\n"
        f"Original task:\n{task}\n\n"
        f"Implementation:\n{code}\n"
    )


def _pricing_key(model: str) -> str | None:
    raw = (model or "").strip()
    if not raw:
        return None
    if raw in TARGET_FOR:
        return raw
    lowered = raw.lower()
    if lowered in TARGET_FOR:
        return lowered
    if lowered in MODEL_ALIASES:
        return MODEL_ALIASES[lowered]
    if "/" in raw:
        suffix = raw.split("/", 1)[1]
        if suffix in TARGET_FOR:
            return suffix
        if suffix.lower() in MODEL_ALIASES:
            return MODEL_ALIASES[suffix.lower()]
    return None


def _original_for(path: str, originals: dict[str, str]) -> str | None:
    if path in originals:
        return originals[path]
    needle = Path(path).as_posix()
    for key, text in originals.items():
        if Path(key).as_posix() == needle:
            return text
        if Path(key).name == Path(path).name:
            return text
    return None


def _size_heuristic(files: dict[str, str]) -> float:
    if not files:
        return 0.0
    total = sum(len(body) for body in files.values())
    if total < 40:
        return 0.3
    if total > 400_000:
        return 0.4
    return 1.0


def _combined_patch(result: SwarmResult) -> str:
    diffs = result.diffs or file_diffs({}, result.files)
    if not diffs:
        return ""
    return "\n".join(diffs.values()).rstrip() + "\n"


def _run_hash(task: str) -> str:
    raw = f"{task}\n{time.time():.3f}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def _logger() -> logging.Logger:
    log = logging.getLogger(LOG_NAME)
    if log.handlers:
        return log
    path = Path(__file__).resolve().parent.parent / "logs" / "swarm.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False
    return log


def _write_log_tail(log: logging.Logger, result: SwarmResult) -> None:
    log.info(
        "finished verdict=%s confidence=%s rounds=%s files=%s diffs=%s hash=%s",
        result.review_verdict,
        result.confidence,
        result.redhat_rounds,
        list(result.files),
        list(result.diffs),
        result.run_hash,
    )
    log.info("spec\n%s", result.spec)
    log.info("code\n%s", result.implementation)
    log.info("review\n%s", result.review)
    log.info("tests\n%s", result.tests)
    log.info("self-test\n%s", result.test_results.model_dump())
    log.info("documentation\n%s", result.documentation)
    log.info("quality_report\n%s", result.quality_report)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m prompt_matrix.swarm",
        description=(
            "Assure development swarm: architect, developer, reviewer, "
            "red-hat loop, then tester plus unittest beside documenter. "
            "Each role calls PEM run_workflow()."
        ),
    )
    parser.add_argument("--task", required=True, help="Natural-language feature to build")
    parser.add_argument(
        "--context-files",
        nargs="+",
        default=[],
        metavar="PATH",
        dest="context_files",
        help="Source files to include as context",
    )
    parser.add_argument(
        "--context",
        action="append",
        default=[],
        metavar="PATH",
        help="Alias for --context-files (repeatable)",
    )
    parser.add_argument(
        "--model",
        action="append",
        default=[],
        metavar="ROLE=MODEL",
        help="Override a role model, e.g. architect=gemini-1.5-pro (repeatable)",
    )
    parser.add_argument(
        "--pr",
        "--create-pr",
        dest="create_pr",
        action="store_true",
        help="Write logs/swarm.patch and try gh pr create if confidence meets the gate",
    )
    parser.add_argument(
        "--copy-only",
        action="store_true",
        help="Compile prompts only. Do not call provider APIs.",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Route every role through the local ollama target.",
    )
    parser.add_argument(
        "--cheap",
        action="store_true",
        help="Let PEM's cost router pick a cheaper live target.",
    )
    parser.add_argument(
        "--max-iterations",
        "--max-redhat-rounds",
        dest="max_iterations",
        type=int,
        default=MAX_REDHAT_ROUNDS,
        help=f"Cap on reviewer-driven red-hat revisions (default {MAX_REDHAT_ROUNDS})",
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=MIN_CONFIDENCE,
        help=f"Merge gate for --pr gh attempt (default {MIN_CONFIDENCE})",
    )
    parser.add_argument(
        "--edition",
        choices=["free", "pro", "team", "self-hosted"],
        help="Assure edition (or ASSURE_EDITION). Default from config / env.",
    )
    parser.add_argument(
        "--skip-tests", action="store_true", help="Skip tester and unittest self-test"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        dest="apply_workspace",
        help="Write accepted files into the repo after lint. Default is patch-only (logs/swarm.patch).",
    )
    parser.add_argument("--skip-docs", action="store_true", help="Skip documenter")
    return parser


def _parse_role_models(items: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items or []:
        raw = (item or "").strip()
        if not raw:
            continue
        if "=" not in raw:
            raise MatrixError(
                f"Expected ROLE=MODEL, got {raw!r}. Example: --model developer=deepseek-chat"
            )
        role, model = raw.split("=", 1)
        role = role.strip().lower()
        model = model.strip()
        if role not in ROLES:
            raise MatrixError(f"Unknown swarm role {role!r}. Use one of: {', '.join(ROLES)}.")
        if model:
            out[role] = model
    return out


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.edition:
        os.environ["ASSURE_EDITION"] = args.edition
    paths = list(args.context_files or []) + list(args.context or [])
    try:
        result = run_swarm(
            task=args.task,
            context_files=paths or None,
            target_models=_parse_role_models(args.model) or None,
            create_pr=bool(args.create_pr),
            direct=not args.copy_only,
            local=bool(args.local),
            cheap=bool(args.cheap),
            max_redhat_rounds=args.max_iterations,
            min_confidence=float(args.min_confidence),
            skip_tests=bool(args.skip_tests),
            skip_docs=bool(args.skip_docs),
            apply_workspace=bool(args.apply_workspace),
        )
    except MatrixError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    report = result.quality_report or result.summary
    sys.stdout.write(report)
    if not report.endswith("\n"):
        sys.stdout.write("\n")
    if any(step.error for step in result.steps) and not (result.implementation or result.files):
        return 1
    return 0


def set_progress_hook(fn: Callable[[str], None] | None) -> Token:
    """Used by the background MCP job to surface phase text on swarm_status."""
    return _PROGRESS_HOOK.set(fn)


def reset_progress_hook(token: Token) -> None:
    _PROGRESS_HOOK.reset(token)


def _progress(message: str) -> None:
    print(f"[Swarm] {message}", file=sys.stderr)
    hook = _PROGRESS_HOOK.get()
    if hook is None:
        return
    try:
        hook(message)
    except Exception:
        return


def _normalize_confidence(value: float, unit: str | None) -> float:
    unit = (unit or "").replace(" ", "")
    if "%" in unit:
        return max(0.0, min(1.0, value / 100.0))
    if unit == "/5":
        return max(0.0, min(1.0, value / 5.0))
    if unit == "/10":
        return max(0.0, min(1.0, value / 10.0))
    if value > 1.0 and value <= 5.0:
        return value / 5.0
    if value > 5.0:
        return max(0.0, min(1.0, value / 100.0))
    return max(0.0, min(1.0, value))


if __name__ == "__main__":
    raise SystemExit(main())
