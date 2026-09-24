"""Refinement Layer: Document Structure, Refine Workspace, editor sync, AST serializer."""

from __future__ import annotations


from prompt_matrix.i18n import CATALOGS, LOCALES


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
