"""Zero-state placeholder styles stay non-interactive (style.css is still served by base.html)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_zero_state_placeholders_are_pointer_none():
    css = (ROOT / "prompt_matrix" / "static" / "style.css").read_text(encoding="utf-8")
    assert "#jdf-render-target.is-empty::before" in css
    ghost = css.split("#jdf-render-target.is-empty::before")[1].split("}")[0]
    assert "pointer-events: none" in ghost
    dash = css.split(".zero-state-dashboard {")[1].split("}")[0]
    assert "pointer-events: none" in dash
