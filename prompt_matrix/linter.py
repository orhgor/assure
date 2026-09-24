"""Pre-dispatch dialect checks. Fail Send on broken tags; copy-only still works."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_TAG = re.compile(r"(?m)^[ \t]*<(/?)([a-zA-Z][\w-]*)>")


@dataclass
class LintReport:
    target: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_text(self) -> str:
        lines = [f"error: {item}" for item in self.errors]
        lines.extend(f"warning: {item}" for item in self.warnings)
        return "\n".join(lines) if lines else "ok"


def lint_prompt(target: str, prompt: str) -> LintReport:
    target = (target or "").strip().lower()
    text = prompt or ""
    report = LintReport(target=target)
    if not text.strip():
        report.errors.append("Prompt is empty.")
        return report
    if target == "claude":
        _xml_dialect(
            report, text, required=("role", "instructions"), optional=("thinking", "context")
        )
    elif target == "cursor":
        if "/ask" not in text:
            report.errors.append("Cursor dialect needs /ask (usually /ask @workspace).")
        if "@workspace" not in text and "@file" not in text:
            report.warnings.append("No @workspace or @file reference.")
    elif target == "gemini":
        if "Data not available" not in text:
            report.warnings.append("Gemini grounding line (Data not available) is missing.")
        if "Role:" not in text and "<role>" not in text:
            report.warnings.append("No Role line.")
    elif target in {"kimi", "ollama"}:
        _need(report, text, "## Task", "Task heading")
        if "## Output" not in text and "## Output Format" not in text:
            report.warnings.append("No Output heading.")
    return report


def _need(report: LintReport, text: str, needle: str, label: str) -> None:
    if needle not in text:
        report.errors.append(f"Missing {label} ({needle}).")


def _xml_dialect(
    report: LintReport,
    text: str,
    *,
    required: tuple[str, ...],
    optional: tuple[str, ...] = (),
) -> None:
    counts: dict[str, list[int]] = {}
    for closing, name in _TAG.findall(text):
        key = name.lower()
        pair = counts.setdefault(key, [0, 0])
        if closing:
            pair[1] += 1
        else:
            pair[0] += 1
    for name in required:
        opens, closes = counts.get(name, [0, 0])
        if opens == 0:
            report.errors.append(f"Missing <{name}> block.")
        elif opens != closes:
            report.errors.append(f"Unbalanced <{name}>: {opens} open, {closes} close.")
    for name in optional:
        opens, closes = counts.get(name, [0, 0])
        if opens and opens != closes:
            report.errors.append(f"Unbalanced <{name}>: {opens} open, {closes} close.")
