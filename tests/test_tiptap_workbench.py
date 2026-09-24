"""TipTap workbench wiring: scripts, markup, i18n."""

from __future__ import annotations


import pytest

from prompt_matrix.i18n import CATALOGS, LOCALES


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
    "generate.compile_selection",
    "generate.ast.heading",
    "generate.ast.paragraph",
    "generate.ast.list",
    "generate.ast.callout",
    "generate.ast.table",
    "jdf.confidence.toggle",
    "jdf.confidence.verified",
    "jdf.confidence.uncertain",
    "jdf.confidence.hallucination",
)


def test_tiptap_i18n_keys() -> None:
    for locale in LOCALES:
        cat = CATALOGS[locale]
        for key in KEYS:
            assert key in cat, f"missing {locale} {key}"
            assert str(cat[key]).strip(), f"empty {locale} {key}"


def test_jdf_paragraph_ignores_title_field() -> None:
    from prompt_matrix.models.jdf import JDFParagraphNode

    node = JDFParagraphNode.model_validate(
        {"type": "paragraph", "id": "p1", "content": "x", "title": ""}
    )
    assert node.content == "x"
    assert "title" not in node.model_dump()
