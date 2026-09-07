"""Zero-state overlay stays outside #jdf-render-target internals."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "prompt_matrix" / "templates" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "prompt_matrix" / "static" / "zero_state.js").read_text(encoding="utf-8")


def test_zero_state_is_wrapper_not_inner_canvas():
    assert 'id="zero-state-dashboard"' in HTML
    assert 'id="jdf-render-target"' in HTML
    dash_at = HTML.index('id="zero-state-dashboard"')
    target_at = HTML.index('id="jdf-render-target" class="jdf-tree"')
    assert dash_at < target_at
    inner = HTML[target_at : target_at + 180]
    assert "zero-state-dashboard" not in inner
    assert 'role="tree"' in inner


def test_zero_state_js_does_not_rewrite_canvas_html():
    assert "innerHTML" not in JS
    assert 'getElementById("jdf-render-target")' in JS
    assert "classList.toggle" in JS
