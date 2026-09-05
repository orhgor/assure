"""Refinement Layer: Document Structure, Refine Workspace, editor sync, AST serializer."""

from __future__ import annotations

from pathlib import Path

from prompt_matrix.i18n import CATALOGS, LOCALES

ROOT = Path(__file__).resolve().parents[1]

STRUCTURE_KEYS = (
    "structure.title",
    "structure.connect",
    "structure.merge_next",
    "structure.split",
    "structure.delete",
    "structure.no_sections",
    "structure.delete_confirm",
)

REFINE_KEYS = (
    "refine.workspace.title",
    "refine.click_to_refine",
    "refine.refining_node",
    "refine.selection",
    "refine.full_document",
    "refine.run_redhat",
    "refine.input_placeholder",
    "refine.select_node",
    "refine.no_intent",
    "refine.connecting",
)


def test_refinement_i18n_keys() -> None:
    for locale in LOCALES:
        cat = CATALOGS[locale]
        for key in STRUCTURE_KEYS + REFINE_KEYS:
            assert key in cat, f"missing {locale} {key}"
            assert str(cat[key]).strip(), f"empty {locale} {key}"


def test_refinement_markup_and_scripts() -> None:
    html = (ROOT / "prompt_matrix" / "templates" / "index.html").read_text(encoding="utf-8")
    assert 'id="left-pane-shared"' in html
    assert 'id="document-structure"' in html
    assert 'id="structureTreeContainer"' in html
    assert 'id="refine-workspace"' in html
    assert 'id="refineNodeFocusIndicator"' in html
    assert 'id="refineIntentInput"' in html
    assert 'id="executeNodeRefineBtn"' in html
    assert 'id="executeFullDocRefineBtn"' in html
    assert 'id="executeRedHatBtn"' in html
    assert 'id="actionConnectBtn"' in html
    assert 'id="structure-node-menu"' in html
    assert 'data-i18n="structure.title"' in html
    assert 'data-i18n="refine.workspace.title"' in html
    for name in (
        "document_structure.js",
        "refine.js",
        "connect.js",
        "editor_sync.js",
        "ast_serializer.js",
        "redhat_panel.js",
    ):
        assert name in html


def test_refinement_js_wiring() -> None:
    canvas = (ROOT / "prompt_matrix" / "static" / "jdf_canvas.js").read_text(encoding="utf-8")
    assert "getSectionPath" in canvas
    assert "reorderSections" in canvas
    assert "mergeSectionWithNext" in canvas
    assert "deleteSection" in canvas
    assert "connectSections" in canvas
    assert "refineFullDocument" in canvas

    structure = (ROOT / "prompt_matrix" / "static" / "document_structure.js").read_text(
        encoding="utf-8"
    )
    assert "AssureDocumentStructure" in structure
    assert "assure:jdf:rendered" in structure

    refine = (ROOT / "prompt_matrix" / "static" / "refine.js").read_text(encoding="utf-8")
    assert "AssureRefineEngine" in refine
    assert "assure:jdf:selected" in refine

    sync = (ROOT / "prompt_matrix" / "static" / "editor_sync.js").read_text(encoding="utf-8")
    assert "AssureEditorBridge" in sync
    assert "syncReorderedASTToCanvas" in sync

    ser = (ROOT / "prompt_matrix" / "static" / "ast_serializer.js").read_text(encoding="utf-8")
    assert "AssureAstSerializer" in ser
    assert "tiptapToJdf" in ser
    assert "sanitizeJDFDocument" in ser

    tiptap = (ROOT / "prompt_matrix" / "static" / "jdf_tiptap.js").read_text(encoding="utf-8")
    assert "initializeEditorSyncBridge" in tiptap
    assert "initializeAstSerializer" in tiptap
    assert "data-section-id" in tiptap

    css = (ROOT / "prompt_matrix" / "static" / "style.css").read_text(encoding="utf-8")
    assert ".tree-node-item" in css
    assert ".btn-trust-action" in css
    assert ".focus-context-badge" in css
