"""Static coverage for the Argument Spine panel and shared canvas status helper."""

from __future__ import annotations


from prompt_matrix.i18n import CATALOGS, LOCALES

SPINE_KEYS = (
    "spine.title",
    "spine.thesis",
    "spine.branch",
    "spine.collapse_all",
    "spine.expand_all",
    "spine.unverified",
    "spine.verified",
    "spine.warning",
    "spine.error",
    "spine.locked",
    "spine.scroll_to",
    "spine.empty",
)


def test_spine_keys_in_every_locale() -> None:
    en = CATALOGS["en"]
    for locale in LOCALES:
        cat = CATALOGS[locale]
        for key in SPINE_KEYS:
            assert key in cat, f"missing {locale} {key}"
            assert str(cat[key]).strip(), f"empty {locale} {key}"
        if locale != "en":
            for key in SPINE_KEYS:
                if key == "spine.title":
                    continue
                assert cat[key] != en[key], f"{locale} {key} left as English"
