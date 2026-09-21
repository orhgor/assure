"""The counter copy and the seven catalogs.

The shell composes its counter line from catalog keys rather than joining English,
and the tiles and the legend carry ``data-i18n``, so a label added to the markup with
no catalog entry renders in whatever the attribute says — English — in six locales.

The catalogs are built ``{**EN, …overrides}``, so *membership* proves nothing: a
locale that never translated a key still "has" it, at its English value. What can be
checked is the value: an untranslated key is one whose value is still English, which
is what these tests assert. The convention is the repository's own
(``test_confidence_overlay.py``, ``test_full_audit.py``), extended to the case the
merge makes possible.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from prompt_matrix.i18n import CATALOGS, EN, LOCALES

PROTOTYPE = Path(__file__).resolve().parents[1] / "prototype"
NON_ENGLISH = [locale for locale in LOCALES if locale != "en"]

#: The counter surface, as the readers meet it: the four tiles, the legend, and the
#: two lines the shell composes (``_tf("counter.line", "…")``).
COUNTER_KEYS = (
    "counter.label.anchored",
    "counter.label.supported",
    "counter.label.partial",
    "counter.label.unanchored",
    "counter.legend",
    "counter.line",
    "counter.line.sources_one",
    "counter.line.sources_many",
)


def _declared_in_markup() -> set[str]:
    html = (PROTOTYPE / "index.html").read_text(encoding="utf-8")
    return set(re.findall(r'data-i18n(?:-placeholder|-html)?="([^"]+)"', html))


def _used_in_shell() -> set[str]:
    source = (PROTOTYPE / "shell.js").read_text(encoding="utf-8")
    return set(re.findall(r'"(counter\.[a-z_.]+)"', source))


def test_the_counter_keys_are_the_ones_the_markup_and_the_shell_use() -> None:
    """The set asserted here is the shipped one, and the English source defines each."""
    declared = _declared_in_markup() | _used_in_shell()
    assert set(COUNTER_KEYS) <= declared, (
        "a key asserted here that neither the markup nor the shell uses: "
        f"{sorted(set(COUNTER_KEYS) - declared)}"
    )
    for key in COUNTER_KEYS:
        assert EN.get(key), f"{key} is used by the shell and undefined in the English source"


@pytest.mark.parametrize("locale", NON_ENGLISH)
def test_no_locale_is_left_showing_the_english_counter_copy(locale: str) -> None:
    """``{**EN}`` means an untranslated key is indistinguishable from a translated one.

    Only the value can be checked: a counter key whose localized value is still the
    English string is a key the locale never carried, and the reader sees English.
    """
    catalog = CATALOGS[locale]
    untranslated = sorted(key for key in COUNTER_KEYS if catalog.get(key) == EN.get(key))
    assert not untranslated, f"{locale} still shows the English counter copy for {untranslated}"
    empty = sorted(key for key in COUNTER_KEYS if not str(catalog.get(key) or "").strip())
    assert not empty, f"{locale} has an empty value for {empty}"


@pytest.mark.parametrize("locale", NON_ENGLISH)
def test_every_key_the_markup_declares_is_translated_in_every_catalog(locale: str) -> None:
    """The tiles are not the only ``data-i18n`` in the shell, and none may fall back.

    A key whose localized value equals the English one is a fallback the reader cannot
    tell from a correct translation, and the merge hides it from every membership test.
    """
    declared = _declared_in_markup()
    assert declared, "index.html declares no data-i18n keys, so this test proves nothing"
    catalog = CATALOGS[locale]
    untranslated = sorted(key for key in declared if catalog.get(key) == EN.get(key))
    assert not untranslated, f"{locale} falls back to English for {untranslated}"
