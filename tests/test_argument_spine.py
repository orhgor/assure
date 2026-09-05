"""Static coverage for the Argument Spine panel and shared canvas status helper."""

from __future__ import annotations

from pathlib import Path

from prompt_matrix.i18n import CATALOGS, LOCALES

ROOT = Path(__file__).resolve().parents[1]
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


def test_spine_markup_and_script() -> None:
    html = (ROOT / "prompt_matrix" / "templates" / "index.html").read_text(encoding="utf-8")
    assert 'id="argument-spine"' in html
    assert 'id="argument-spine-tree"' in html
    assert 'id="spine-node-menu"' in html
    assert 'data-i18n="spine.title"' in html
    assert 'data-i18n="spine.empty"' in html
    assert "argument_spine.js" in html
    js = (ROOT / "prompt_matrix" / "static" / "argument_spine.js").read_text(encoding="utf-8")
    assert "AssureArgumentSpine" in js
    assert "assure:jdf:rendered" in js
    assert "assure:jdf:selected" in js
    assert "assure_spine_collapsed" in js
    assert "assure_spine_folded_sections" in js


def test_canvas_shared_status_uses_violation_not_fail() -> None:
    js = (ROOT / "prompt_matrix" / "static" / "jdf_canvas.js").read_text(encoding="utf-8")
    assert "computeNodeStatus" in js
    assert 'z.status === "violation"' in js
    assert 'z.status === "FAIL"' not in js.split("computeNodeStatus")[1][:800]
    assert "nodeHasLockedNumber" in js
    assert "getNodeSectionPath" in js
    assert "assure:jdf:rendered" in js
    assert "assure:jdf:selected" in js
    assert "skipViewSwitch" in js
    tiptap = (ROOT / "prompt_matrix" / "static" / "jdf_tiptap.js").read_text(encoding="utf-8")
    assert 'z.status === "violation"' in tiptap
    assert 'z.status === "FAIL"' not in tiptap
