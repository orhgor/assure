"""Design token lint — scoped to Operator Cockpit CSS surfaces."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.quality_check

CSS_PATH = Path(__file__).resolve().parents[2] / "prompt_matrix" / "static" / "style.css"

SELECTOR_RE = re.compile(
    r"(#operator-prompt|\.operator-prompt|\.operator-canvas-overlay|"
    r"\.founder-sync-status|\.evidence-inspector|@keyframes founder-sync-spin|"
    r"@keyframes operator-prompt-glow|@keyframes operator-prompt-spin|"
    r"@keyframes evidence-skeleton-shimmer)"
)

ALLOWED_HEX = {
    "#1e293b",
    "#3b82f6",
    "#10b981",
    "#059669",
    "#047857",
    "#f59e0b",
    "#ef4444",
    "#dc2626",
    "#f8fafc",
    "#ffffff",
    "#000000",
    "#0f172a",
    "#334155",
    "#64748b",
    "#94a3b8",
    "#cbd5e1",
    "#e2e8f0",
    "#475569",
    "#090a0f",
    "#131620",
    "#0f111a",
    "#ecfdf5",
    "#a7f3d0",
    "#dbeafe",
    "#1d4ed8",
    "#2563eb",
    "#fef2f2",
    "#fecaca",
    "#fffbeb",
    "#fde68a",
    "#f1f5f9",
    "#e5e7eb",
    "#f9fafb",
    "#60a5fa",
    "#93c5fd",
    "#f7f8fa",
    "#2d3748",
    "#0d2b45",
    "#d4a843",
    "#e8a838",
    "#f0fdf4",
    "#166534",
    "#16a34a",
    "#111827",
    "#718096",
    "#d97706",
    "#6b7280",
    "#fee2e2",
    "#f3f4f6",
    "#fef3c7",
    "#1a4b8c",
    "#2e7d32",
    "#1e5a22",
    "#b48a2e",
    "#9a6b12",
    "#fbf6ea",
    "#eef4fb",
    "#eef6ee",
}

ALLOWED_PX = {
    0,
    1,
    2,
    3,
    4,
    5,
    6,
    7,
    8,
    10,
    11,
    12,
    13,
    14,
    16,
    18,
    20,
    22,
    24,
    30,
    32,
    36,
    40,
    48,
    60,
    64,
    80,
    100,
    320,
    350,
    420,
    600,
    900,
    999,
}


def _cockpit_css_lines() -> list[str]:
    """Extract CSS rule blocks whose selectors target Operator Cockpit surfaces."""
    lines = CSS_PATH.read_text(encoding="utf-8").splitlines()
    scoped: list[str] = []
    i = 0
    while i < len(lines):
        if not SELECTOR_RE.search(lines[i]):
            i += 1
            continue
        depth = 0
        while i < len(lines):
            scoped.append(lines[i])
            depth += lines[i].count("{") - lines[i].count("}")
            i += 1
            if depth <= 0 and scoped[-1].strip().endswith("}"):
                break
    return scoped


def test_no_unapproved_hex_colors_in_cockpit_css():
    lines = _cockpit_css_lines()
    hex_pattern = re.compile(r"#[0-9a-fA-F]{6}\b")
    allowed = {h.lower() for h in ALLOWED_HEX}
    violations: list[str] = []
    for i, line in enumerate(lines, 1):
        if line.strip().startswith("--"):
            continue
        for match in hex_pattern.findall(line):
            if match.lower() not in allowed:
                violations.append(f"Line {i}: {match} — {line.strip()[:80]}")
    assert not violations, "Unapproved colors:\n" + "\n".join(violations[:20])


def test_no_unapproved_px_spacing_in_cockpit_css():
    lines = _cockpit_css_lines()
    px_pattern = re.compile(r"(\d+)px")
    violations: list[str] = []
    for i, line in enumerate(lines, 1):
        if line.strip().startswith("--"):
            continue
        if any(token in line for token in ("box-shadow", "calc(", "min(", "max(", "@keyframes")):
            continue
        for match in px_pattern.findall(line):
            val = int(match)
            if val not in ALLOWED_PX:
                violations.append(f"Line {i}: {val}px — {line.strip()[:80]}")
    assert not violations, "Unapproved spacing:\n" + "\n".join(violations[:20])
