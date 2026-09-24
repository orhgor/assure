"""Library tab rewritten as Substrate Vault prompt cards."""

from __future__ import annotations


from prompt_matrix.i18n import CATALOGS, LOCALES


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
