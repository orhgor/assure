"""Assure development swarm.

Phase 1 is sequential: architect, developer, reviewer.
Phase 2 is a red-hat rewrite loop until Keep or the round cap.
Phase 3 runs two lanes at once: tester then pytest on one, documenter on the
other. ``run_workflow`` is blocking, so those calls go through
``asyncio.to_thread``.

Each role uses a PEM intent. Live targets and edition persona clamps stay in
the pipeline. Domain and case come only from the user's task and attached
files.
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
from pathlib import Path

from pydantic import BaseModel, Field

try:
    from .cost_router import MODEL_ALIASES, TARGET_FOR, model_override, send_model_id
    from .engine import MAX_FILE_BYTES, MatrixError, TARGET_ALIASES
    from .pipelines import PipelineResult, run_workflow
    from .route import live_targets
except ImportError:
    from cost_router import MODEL_ALIASES, TARGET_FOR, model_override, send_model_id
    from engine import MAX_FILE_BYTES, MatrixError, TARGET_ALIASES
    from pipelines import PipelineResult, run_workflow
    from route import live_targets

ROLES = ("architect", "developer", "reviewer", "tester", "documenter")

DEFAULT_ROLE_MODELS = {
    "architect": "gemini-1.5-pro",
    "developer": "deepseek-chat",
    "reviewer": "claude-3-5-sonnet-20240620",
    "tester": "gemini-1.5-flash",
    "documenter": "claude-3-haiku-20240307",
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
LOG_NAME = "prompt_matrix.swarm"
COVERAGE_UNAVAILABLE = "Data not available in current context."
_TEST_FILE = re.compile(r"(?:^|/)(?:test_[^/]+\.py|[^/]+_test\.py)$")

_VERDICT_LINE = re.compile(r"^\s*verdict\s*[:\-]\s*(keep|revise|reject)\b", re.IGNORECASE | re.MULTILINE)
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
) -> SwarmResult:
    """Synchronous wrapper. Phase 1-2 sequential. Phase 3: tester+pytest beside documenter."""
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
            )
        )
    raise MatrixError("run_swarm() cannot nest inside a running event loop. Await run_swarm_async().")


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
) -> SwarmResult:
    """Architect → developer → reviewer (then red-hat). Tester+pytest beside documenter."""
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

    dev = run_developer(cfg.task, result.spec, context, bindings, cfg)
    _ingest(result, dev)
    result.implementation = dev.reply or ""
    result.files = parse_code(result.implementation)
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
    """Tester then pytest on one lane, documenter on the other. run_workflow is blocking."""
    code = result.implementation

    async def test_lane() -> SwarmStep | None:
        if cfg.skip_tests:
            result.test_results = TestResults(skipped=True, skip_reason="skipped by --skip-tests")
            result.tests = "Skipped"
            return None
        tes = await asyncio.to_thread(run_tester, cfg.task, code, context, bindings, cfg)
        _ingest(result, tes)
        result.tests = tes.reply or ""
        for path, body in parse_code(result.tests).items():
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
        for path, body in parse_code(result.documentation).items():
            result.files.setdefault(path, body)


def run_architect(task: str, context: str, bindings, cfg: SwarmConfig) -> SwarmStep:
    _progress(f"architect on {bindings['architect'][0]}")
    return _call("architect", _architect_task(task), context, bindings, cfg=cfg)


def run_developer(task: str, spec: str, context: str, bindings, cfg: SwarmConfig) -> SwarmStep:
    _progress(f"developer on {bindings['developer'][0]}")
    return _call("developer", _developer_task(task, spec), context, bindings, cfg=cfg)


def run_reviewer(task: str, spec: str, code: str, context: str, bindings, cfg: SwarmConfig) -> SwarmStep:
    _progress(f"reviewer on {bindings['reviewer'][0]}")
    return _call("reviewer", _reviewer_task(task, spec, code), context, bindings, cfg=cfg)


def run_redhat_loop(
    cfg: SwarmConfig,
    result: SwarmResult,
    context: str,
    bindings,
    originals: dict[str, str] | None = None,
) -> SwarmResult:
    """Review, then red-hat rewrite until Keep or max rounds."""
    del originals
    cap = max(0, int(cfg.max_redhat_rounds))
    rounds = 0
    _progress("phase 2: review and red-hat if needed")
    while True:
        rev = run_reviewer(cfg.task, result.spec, result.implementation, context, bindings, cfg)
        _ingest(result, rev)
        review_text = rev.reply or ""
        verdict, confidence = parse_review(review_text)
        result.review = review_text
        result.review_verdict = verdict
        result.review_confidence = confidence
        _logger().info("review verdict=%s confidence=%s", verdict, confidence)
        if verdict == "Keep" or rounds >= cap:
            break
        if not review_text and rev.error:
            result.notes.append(rev.error or "Reviewer did not reply.")
            break
        rounds += 1
        result.redhat_rounds = rounds
        revised = _call(
            "developer",
            _revision_task(cfg.task, result.spec, result.implementation, review_text),
            context,
            bindings,
            cfg=cfg,
            workflow="redhat",
            persona="security",
            critic=bindings["reviewer"][0],
            cycle=rounds,
        )
        _progress(f"red-hat {rounds}/{cap} on {bindings['developer'][0]} (security)")
        _ingest(result, revised)
        if revised.reply:
            result.implementation = revised.reply
            result.files = parse_code(result.implementation)
        elif revised.error:
            result.notes.append(revised.error)
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
    pytest_output: str,
    cycle: int,
) -> SwarmStep:
    _progress(f"self-test fix {cycle}/{cfg.max_fix_rounds} on {bindings['developer'][0]}")
    return _call(
        "developer",
        _fix_task(cfg.task, result.spec, result.implementation, pytest_output),
        context,
        bindings,
        cfg=cfg,
        cycle=cycle,
    )


def load_context(paths: list[str] | None) -> tuple[str, dict[str, str], list[str]]:
    """Read optional paths and return (context blob, originals, notes)."""
    originals: dict[str, str] = {}
    notes: list[str] = []
    chunks: list[str] = []
    for raw in paths or []:
        label = str(raw).strip()
        if not label:
            continue
        path = Path(label).expanduser()
        try:
            if not path.is_file():
                notes.append(f"Missing context file: {label}")
                continue
            data = path.read_bytes()
            if len(data) > MAX_FILE_BYTES:
                data = data[:MAX_FILE_BYTES] + b"\n... [truncated]\n"
            text = data.decode("utf-8", errors="replace")
        except OSError as exc:
            notes.append(f"Could not read {label}: {exc}")
            continue
        originals[label] = text
        chunks.append(f"===== {label} =====\n{text}")
    return "\n\n".join(chunks), originals, notes


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
    """Run generated test modules in a temp directory. Does not write the live tree."""
    tests = {path: body for path, body in (files or {}).items() if _TEST_FILE.search(path.replace("\\", "/"))}
    if not tests:
        return TestResults(skipped=True, skip_reason="No pytest modules extracted from the tester output.")
    tmp: str | None = None
    try:
        tmp = tempfile.mkdtemp(prefix="pem-swarm-")
        root = Path(tmp)
        names: list[str] = []
        for path, body in tests.items():
            dest = root / Path(path).name
            dest.write_text(body, encoding="utf-8")
            names.append(str(dest))
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--tb=short", *names],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(root),
        )
    except FileNotFoundError:
        return TestResults(skipped=True, skip_reason="pytest is not installed.")
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        err = (exc.stderr or "") if isinstance(exc.stderr, str) else "pytest timed out."
        return TestResults(passed=False, stdout=out, stderr=err, returncode=-1)
    except OSError as exc:
        return TestResults(skipped=True, skip_reason=str(exc))
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    blob = stdout + "\n" + stderr
    skipped = "No module named pytest" in blob or "No module named 'pytest'" in blob
    if skipped:
        return TestResults(
            skipped=True,
            skip_reason="pytest is not installed.",
            stdout=stdout,
            stderr=stderr,
            returncode=proc.returncode,
        )
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
    gate = "meets" if result.confidence >= MIN_CONFIDENCE and approved and tests.passed is True else "does not meet"
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
    rev_conf = "n/a" if result.review_confidence is None else f"{result.review_confidence:.2f}"
    approved_line = "approved (Keep)" if approved else f"not approved ({result.review_verdict or 'none'})"
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
    if result.confidence < min_confidence:
        result.notes.append(
            f"Skipped gh: confidence {result.confidence:.3f} is below the {min_confidence} merge gate. "
            f"Patch is at {patch_path}."
        )
        return str(patch_path)
    title = (result.task or "swarm").strip().split("\n", 1)[0][:72]
    try:
        proc = subprocess.run(
            ["gh", "pr", "create", "--title", title, "--body", (result.quality_report or "")[:4000]],
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
            result.implementation = fixed.reply
            result.files.update(parse_code(fixed.reply))
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


def _finalize(result: SwarmResult, originals: dict[str, str], cfg: SwarmConfig, log: logging.Logger) -> None:
    result.diffs = file_diffs(originals, result.files)
    result.confidence = compute_confidence_score(
        result.review_verdict,
        result.review_confidence,
        result.test_results.passed,
        result.files,
    )
    result.summary = generate_summary(result, originals)
    create_pull_request(result, enabled=cfg.create_pr, min_confidence=cfg.min_confidence)
    result.summary = generate_summary(result, originals)
    _write_log_tail(log, result)


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
    try:
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
        "Domain and case come only from this task and the attached files."
    )


def _developer_task(task: str, spec: str) -> str:
    return (
        "Implement the spec as complete file contents, not patches and not hypotheses.\n"
        "The deliverable is the files. You may list brief notes after the files.\n\n"
        "Write each file as:\n\n"
        "### relative/path.ext\n"
        "```\n"
        "full file content\n"
        "```\n\n"
        f"Original task:\n{task}\n\n"
        f"Architect spec:\n{spec}\n"
    )


def _reviewer_task(task: str, spec: str, code: str) -> str:
    return (
        "Review this implementation for bugs, security issues, style violations, "
        "and completeness against the spec.\n\n"
        "End with exactly these two lines:\n"
        "Verdict: Keep | Revise | Reject\n"
        "Confidence: <number between 0 and 1>\n"
        "If the verdict is Revise or Reject, list required fixes as numbered items.\n\n"
        f"Original task:\n{task}\n\n"
        f"Spec:\n{spec}\n\n"
        f"Implementation:\n{code}\n"
    )


def _revision_task(task: str, spec: str, code: str, review: str) -> str:
    return (
        "Revise the implementation using the reviewer's required fixes. "
        "Output complete file contents in the ### path + fence convention.\n\n"
        f"Original task:\n{task}\n\n"
        f"Spec:\n{spec}\n\n"
        f"Current implementation:\n{code}\n\n"
        f"Reviewer feedback:\n{review}\n"
    )


def _tester_task(task: str, code: str) -> str:
    return (
        "Write pytest tests for this implementation. Output complete test modules "
        "using the ### path + fence convention. Use pytest. Cover the happy path "
        "and important edge cases. Do not invent APIs that are not in the implementation.\n\n"
        f"Original task:\n{task}\n\n"
        f"Implementation:\n{code}\n"
    )


def _fix_task(task: str, spec: str, code: str, pytest_output: str) -> str:
    return (
        "Pytest failed on the generated tests. Fix the implementation. "
        "Output complete file contents in the ### path + fence convention.\n\n"
        f"Original task:\n{task}\n\n"
        f"Spec:\n{spec}\n\n"
        f"Current implementation:\n{code}\n\n"
        f"Pytest output:\n{pytest_output}\n"
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
            "red-hat loop, then tester plus pytest beside documenter. "
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
    parser.add_argument("--skip-tests", action="store_true", help="Skip tester and pytest self-test")
    parser.add_argument("--skip-docs", action="store_true", help="Skip documenter")
    return parser


def _parse_role_models(items: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items or []:
        raw = (item or "").strip()
        if not raw:
            continue
        if "=" not in raw:
            raise MatrixError(f"Expected ROLE=MODEL, got {raw!r}. Example: --model developer=deepseek-chat")
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


def _progress(message: str) -> None:
    print(f"[Swarm] {message}", file=sys.stderr)


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
