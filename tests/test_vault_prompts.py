"""Library tab rewritten as Substrate Vault prompt cards."""

from __future__ import annotations

from pathlib import Path

from prompt_matrix.i18n import CATALOGS, LOCALES

ROOT = Path(__file__).resolve().parents[1]

KEYS = (
    "vault.prompts.lead",
    "vault.prompts.empty",
    "vault.prompts.add",
    "vault.prompts.edit",
    "vault.prompts.save_version",
    "vault.prompts.insert",
    "vault.prompts.class.research",
    "vault.prompts.class.design",
    "vault.prompts.class.comparison",
    "vault.prompts.version",
)


def test_vault_prompt_i18n() -> None:
    for locale in LOCALES:
        cat = CATALOGS[locale]
        for key in KEYS:
            assert key in cat, f"missing {locale} {key}"
            assert str(cat[key]).strip()


def test_vault_prompt_markup_and_scripts() -> None:
    html = (ROOT / "prompt_matrix" / "templates" / "index.html").read_text(encoding="utf-8")
    assert 'id="vault-prompt-grid"' in html
    assert 'id="vault-prompt-add"' in html
    assert "vault_prompts.js" in html
    assert 'data-i18n="vault.prompts.lead"' in html
    assert 'data-i18n="substrate.vault.title"' in html
    js = (ROOT / "prompt_matrix" / "static" / "vault_prompts.js").read_text(encoding="utf-8")
    assert "indexedDB" in js
    assert "history" in js
    assert "insertIntoEditor" in js
    assert "Insert into Editor" in js
    assert '"research"' in js and '"design"' in js and '"comparison"' in js
    tiptap = (ROOT / "prompt_matrix" / "static" / "jdf_tiptap.js").read_text(encoding="utf-8")
    assert "insertAtCursor" in tiptap
    css = (ROOT / "prompt_matrix" / "static" / "style.css").read_text(encoding="utf-8")
    assert ".vault-prompt-grid" in css
