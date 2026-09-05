"""TipTap workbench wiring: scripts, markup, i18n."""

from __future__ import annotations

from pathlib import Path

import pytest

from prompt_matrix.i18n import CATALOGS, LOCALES

ROOT = Path(__file__).resolve().parents[1]

KEYS = (
    "generate.model_label",
    "generate.lock_toggle",
    "generate.recent_prompts",
    "generate.recent_empty",
    "generate.reuse",
    "generate.quick_actions",
    "generate.duplicate_node",
    "generate.split_section",
    "generate.merge_next",
    "generate.append_hint",
    "jdf.diff.title",
    "jdf.diff.original",
    "jdf.diff.proposed",
    "jdf.diff.accept",
    "jdf.diff.reject",
    "jdf.export.word",
    "jdf.export.md",
    "jdf.export.html",
    "generate.history_ok",
    "generate.history_fail",
    "generate.history_pending",
)


def test_tiptap_i18n_keys() -> None:
    for locale in LOCALES:
        cat = CATALOGS[locale]
        for key in KEYS:
            assert key in cat, f"missing {locale} {key}"
            assert str(cat[key]).strip(), f"empty {locale} {key}"


def test_tiptap_markup_and_scripts() -> None:
    html = (ROOT / "prompt_matrix" / "templates" / "index.html").read_text(encoding="utf-8")
    assert "tiptap.bundle.js" in html
    assert "jdf_tiptap.js" in html
    assert "prompt_history.js" in html
    assert 'id="generate-model-select"' in html
    assert 'id="generate-lock-toggle"' in html
    assert 'id="generate-prompt-history"' in html
    assert 'id="generate-duplicate-node"' in html
    assert 'id="jdf-diff-panel"' in html
    assert 'id="btn-export-md"' in html
    assert 'id="btn-export-html"' in html
    assert 'data-i18n="generate.recent_prompts"' in html
    assert 'data-i18n="jdf.diff.accept"' in html


def test_tiptap_mapper_exports() -> None:
    js = (ROOT / "prompt_matrix" / "static" / "jdf_tiptap.js").read_text(encoding="utf-8")
    assert "jdfToTiptap" in js
    assert "tiptapToJdf" in js
    assert "AssureTiptapEditor" in js
    canvas = (ROOT / "prompt_matrix" / "static" / "jdf_canvas.js").read_text(encoding="utf-8")
    assert "AssureTiptapEditor" in canvas
    assert "duplicateNode" in canvas
    assert "showRevisionDiff" in canvas
    assert "sanitizeJDFDocument" in canvas
    assert "sanitizeJDFNode" in canvas
    para_keys = 'paragraph: ["type", "id", "content", "entities_referenced", "provenance", "meta", "annotations"]'
    assert para_keys in canvas
    assert "document: sanitizeJDFDocument(this.tree)" in canvas
    assert "jdf-tiptap-host" in canvas
    gen_js = (ROOT / "prompt_matrix" / "static" / "generate.js").read_text(encoding="utf-8")
    assert "sanitizeJDFDocument" in gen_js
    gen = (ROOT / "prompt_matrix" / "static" / "generate.js").read_text(encoding="utf-8")
    assert "prompt_cycle" in gen
    assert "AssurePromptHistory" in gen


def test_jdf_paragraph_forbids_title_field() -> None:
    from pydantic import ValidationError

    from prompt_matrix.models.jdf import JDFParagraphNode

    with pytest.raises(ValidationError, match="title"):
        JDFParagraphNode.model_validate(
            {"type": "paragraph", "id": "p1", "content": "x", "title": ""}
        )


def test_tiptap_does_not_copy_title_onto_paragraphs() -> None:
    js = (ROOT / "prompt_matrix" / "static" / "jdf_tiptap.js").read_text(encoding="utf-8")
    assert 'var isCallout = node.type === "jdfCallout";' in js
    assert 'type: node.type === "jdfCallout" ? "callout" : "paragraph"' not in js
