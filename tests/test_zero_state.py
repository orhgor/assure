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


def test_zero_state_dashboard_has_real_templates():
    assert 'data-create-template="research-dossier"' in HTML
    assert 'data-create-template="compliance-memo"' in HTML
    assert 'data-create-template="contract-review"' in HTML
    assert 'data-create-template="blank"' in HTML
    assert "Clinical Documentation" not in HTML
    assert "CPT Coding" not in HTML


def test_zero_state_placeholders_are_pointer_none():
    css = (ROOT / "prompt_matrix" / "static" / "style.css").read_text(encoding="utf-8")
    assert "#jdf-render-target.is-empty::before" in css
    ghost = css.split("#jdf-render-target.is-empty::before")[1].split("}")[0]
    assert "pointer-events: none" in ghost
    dash = css.split(".zero-state-dashboard {")[1].split("}")[0]
    assert "pointer-events: none" in dash
