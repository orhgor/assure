"""The "Document fields (as the page states them)" surface (user decision 2026-09-29).

The PDFs have no known field list, so ``report.dynamic_fields``
(``services.raw_candidates.dynamic_fields``) lists every key/value pair the page
states — Textract FORMS or the label/value discovery — in reading order, beside
the schema's projection. These are static checks over ``prototype/`` (the shell is
vanilla JS the tests read as text), the seven catalogs, and unit checks of
``services.parsure_view.dynamic_fields_view`` / ``quality_help_view``.

The rule under test is the honesty one: verbatim label and value, the projection
outcome the log wrote (no note when the log never named the pair), no confidence
anywhere on a pair, an empty list that says so, and a quality hover that repeats
the per-page basis the probe wrote rather than describing a formula on its own.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from prompt_matrix.i18n import CATALOGS, EN, LOCALES
from prompt_matrix.services import parsure_view
from prompt_matrix.ui_cache import APP_CSS, APP_JS

ROOT = Path(__file__).resolve().parents[1]
SHELL_JS = (ROOT / "prototype" / "shell.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "prototype" / "index.html").read_text(encoding="utf-8")
SHELL_CSS = (ROOT / "prototype" / "shell.css").read_text(encoding="utf-8")
DETAIL_HTML = (ROOT / "prompt_matrix" / "templates" / "parsure_detail.html").read_text(encoding="utf-8")
NON_ENGLISH = [loc for loc in LOCALES if loc != "en"]


def _between(start: str, end: str, text: str = SHELL_JS) -> str:
    assert start in text, start
    body = text.split(start, 1)[1]
    assert end in body, end
    return body.split(end, 1)[0]


def _renderer_body() -> str:
    return _between("function _renderFieldsDynamic(rep) {", "\n    // ---- the rows ----")


# ---------------------------------------------------------------------------
# Markup and script
# ---------------------------------------------------------------------------


def test_fields_pane_carries_an_open_document_fields_section_above_the_schema_list() -> None:
    m = re.search(r'<details class="fields-execution fields-dynamic" id="fields-dynamic"[^>]*>(.*?)</details>', INDEX_HTML, re.S)
    assert m, "index.html has no #fields-dynamic details"
    opening = INDEX_HTML[m.start():INDEX_HTML.index(">", m.start()) + 1]
    assert " open" in opening, "the document's own fields are open by default"
    assert 'data-count="0"' in opening and " hidden" in opening, "hidden until a report is open; the count is a data attribute"
    body = m.group(1)
    for needle in ('data-i18n="shell.fields.dynamic.title"', 'id="fields-dynamic-count"', 'id="fields-dynamic-filter"',
                   'data-i18n-placeholder="shell.fields.dynamic.filter_placeholder"', 'id="fields-dynamic-empty"',
                   'id="fields-dynamic-nomatch"', '<ul class="dyn-list" id="fields-dynamic-list" role="list">'):
        assert needle in body, needle
    # Above the schema field sections (#fields-list), after the raw pool, which stays as it was.
    assert INDEX_HTML.index('id="fields-raw"') < m.start() < INDEX_HTML.index('id="fields-list"')
    assert 'id="fields-raw-list"' in INDEX_HTML, "the raw-candidates pool is kept"


def test_shell_renders_the_list_from_report_dynamic_fields() -> None:
    body = _renderer_body()
    assert "rep.dynamic_fields" in body
    assert re.search(r"_renderFieldsRawCandidates\(rep\);\n\s*_renderFieldsDynamic\(rep\);", SHELL_JS), "called right after the raw pool"
    # Two columns: the label, then the value with page, source chip, schema note and marker.
    for needle in ('"dyn-label"', '"dyn-value"', "shell.fields.page", "_rawSourceWords(d.source)", "d.schema_field",
                   "shell.fields.dynamic.schema_note", "_projectionTone(d.projection)", "d.preferred === false",
                   "shell.fields.raw.not_preferred", 'np.setAttribute("data-tone", "partial")', "shell.fields.dynamic.empty"):
        assert needle in body, needle
    # Locating reuses the raw rows' paths: the saved paragraph, else the source sheet.
    locator = _between("function _dynamicLocator(d) {", "\n    function _applyDynamicFilter()")
    assert "_locateNode(nodeId)" in locator and "_nodeWrapper(nodeId)" in locator
    assert '_showInSource({ page: page, bbox: bbox, text: String(d.value || "") }, "parsure")' in locator
    assert "return null;" in locator, "no locator → the row is static, nothing is faked"
    assert 'li.classList.add("is-locatable")' in body and 'li.setAttribute("role", "button")' in body


def test_shell_filter_matches_label_or_value_client_side() -> None:
    body = _renderer_body()
    assert 'li.setAttribute("data-search", (label + "\\n" + value).toLowerCase())' in body
    flt = _between("function _applyDynamicFilter() {", "\n    function _renderFieldsDynamic(rep)")
    assert 'li.getAttribute("data-search")' in flt and "li.hidden = !hit" in flt
    assert "shell.fields.dynamic.no_match" in flt and "shell.fields.dynamic.shown" in flt and "shell.fields.dynamic.count" in flt
    assert 'input.addEventListener("input"' in body
    assert "fetch(" not in flt and "jsonPost(" not in flt, "the filter never asks the server"


def test_shell_draws_no_confidence_on_a_document_field() -> None:
    body = _renderer_body()
    tone = _between("function _projectionTone(outcome) {", "\n    function _projectionWords")
    words = _between("function _projectionWords(outcome) {", "\n    // Located like a raw row")
    for text in (body, tone, words):
        assert "confidence" not in text.lower()
        assert "conf-bar" not in text and "_pct(" not in text and "progressbar" not in text


def test_schema_note_tone_follows_the_projection_outcome() -> None:
    tone = _between("function _projectionTone(outcome) {", "\n    function _projectionWords")
    for word, t in (("mapped", "verified"), ("conflicting", "contradicted"), ("review_needed", "partial")):
        assert f'if (o === "{word}") return "{t}";' in tone, word
    assert 'return "none";' in tone, "unmapped or no log → the quiet tone"


def test_styles_for_the_document_field_rows_exist() -> None:
    for sel in (".fields-dynamic-filter", ".fields-dynamic-filter-input", ".fields-dynamic-empty", ".dyn-list", ".dyn-row",
                ".dyn-row.is-locatable", ".dyn-row.is-locatable:focus-visible", ".dyn-label", ".dyn-cell", ".dyn-value",
                ".dyn-meta", ".dyn-page", '.dyn-row[data-preferred="0"] .dyn-value', '.dyn-flag[data-flag="not_preferred"]'):
        assert sel in SHELL_CSS, sel
    assert "grid-template-columns: minmax(0, 2fr) minmax(0, 3fr)" in SHELL_CSS, "two columns: label → value"


def test_ui_cache_bumped_for_the_document_fields() -> None:
    assert APP_CSS == "assure-117"
    assert APP_JS == "assure-117"


# ---------------------------------------------------------------------------
# Quality hover — how the score was computed
# ---------------------------------------------------------------------------


def test_quality_figures_carry_the_per_page_basis_in_their_title() -> None:
    helper = _between("function _qualityHelpText(rep, onlyPage) {", "\n    function _withQualityHelp")
    assert "rep.pages" in helper and "p.basis" in helper
    assert "shell.fields.quality_page_basis" in helper and '"Page {n}: {basis}"' in helper
    assert "shell.fields.quality_help" in helper
    assert 'return "";' in helper, "no basis on any page → no hover text"
    setter = _between("function _withQualityHelp(el, help) {", "\n    function _qualityWarnings")
    assert "el.title = help" in setter and 'el.setAttribute("aria-label"' in setter
    head = _between("function _renderFieldsHead(rep) {", "\n    function _fieldsBreakdown")
    assert '_el("span", "fields-quality"' in head and "_withQualityHelp(qEl, _qualityHelpText(rep))" in head
    assert "_withQualityHelp(opt, _qualityHelpText(rep))" in head, "the report selector option too"
    assert "_qualityHelpText(__parsure, _fieldPageNumber(f))" in SHELL_JS, "per-page figures narrow the hover to their page"
    assert "function _confField(host, label, value, help)" in SHELL_JS


def test_record_page_prints_the_quality_help_and_the_document_fields() -> None:
    assert 'id="record-quality-score"' in DETAIL_HTML and 'id="record-quality-help"' in DETAIL_HTML
    assert "record.execution.quality_help.lines" in DETAIL_HTML
    assert DETAIL_HTML.count('class="quality-help"') >= 2, "a ? beside the document score and beside each page score"
    assert 'shell.fields.quality_help' in DETAIL_HTML
    m = re.search(r'<section class="section dynamic-fields" id="dynamic-fields"[^>]*>(.*?)</section>', DETAIL_HTML, re.S)
    assert m, "parsure_detail.html has no #dynamic-fields section"
    body = m.group(1)
    for key in ("parsing.dynamic.title", "parsing.dynamic.empty", "parsing.dynamic.col.label", "parsing.dynamic.col.value",
                "parsing.dynamic.col.page", "parsing.dynamic.col.source", "parsing.dynamic.col.schema_field", "shell.fields.dynamic.schema_note"):
        assert key in body, key
    assert "record.execution.dynamic_fields" in DETAIL_HTML
    assert "confidence" not in body.lower()
    assert m.start() < DETAIL_HTML.index('<section class="section" id="fields">'), "above the schema fields"


# ---------------------------------------------------------------------------
# Catalogs
# ---------------------------------------------------------------------------

DYNAMIC_KEYS = tuple(k for k in EN if k.startswith("shell.fields.dynamic.") or k.startswith("parsing.dynamic.")
                     or k in ("shell.fields.quality_help", "shell.fields.quality_page_basis"))


def test_every_dynamic_string_the_shell_uses_is_in_the_catalog() -> None:
    used = set(re.findall(r'_tf?\("(shell\.fields\.dynamic\.[a-z_.]*[a-z_])"', SHELL_JS))
    used |= {"shell.fields.quality_help", "shell.fields.quality_page_basis"}
    used |= set(re.findall(r'data-i18n(?:-placeholder|-aria)?="(shell\.fields\.dynamic\.[a-z_.]+)"', INDEX_HTML))
    missing = sorted(k for k in used if k not in EN)
    assert not missing, missing
    used_tpl = set(re.findall(r'strings\.get\("(parsing\.dynamic\.[a-z_.]+)"', DETAIL_HTML))
    assert used_tpl and not sorted(k for k in used_tpl if k not in EN)
    assert "×" in EN["shell.fields.quality_help"] and "OCR" in EN["shell.fields.quality_help"]


@pytest.mark.parametrize("locale", LOCALES)
def test_dynamic_strings_present_and_non_empty_in_every_locale(locale: str) -> None:
    catalog = CATALOGS[locale]
    empty = sorted(k for k in DYNAMIC_KEYS if not str(catalog.get(k) or "").strip())
    assert not empty, f"{locale}: {empty}"
    for key in ("shell.fields.dynamic.count", "shell.fields.dynamic.shown", "shell.fields.dynamic.no_match",
                "shell.fields.dynamic.schema_note", "shell.fields.quality_page_basis"):
        for var in re.findall(r"\{[a-z]+\}", EN[key]):
            assert var in catalog[key], f"{locale} {key} lost {var}"


@pytest.mark.parametrize("locale", NON_ENGLISH)
def test_the_reader_facing_words_are_translated(locale: str) -> None:
    catalog = CATALOGS[locale]
    for key in ("shell.fields.dynamic.title", "shell.fields.dynamic.empty", "shell.fields.dynamic.filter_placeholder",
                "shell.fields.dynamic.no_match", "shell.fields.quality_help", "parsing.dynamic.title", "parsing.dynamic.empty",
                "parsing.dynamic.col.schema_field"):
        assert catalog[key] != EN[key], f"{locale} falls back to English for {key}"


# ---------------------------------------------------------------------------
# parsure_view.dynamic_fields_view / quality_help_view — the record page's rows
# ---------------------------------------------------------------------------

def _pair(cid, label, value, page=1, source="textract", **extra):
    d = {"label": label, "value": value, "name_hint": label.lower().replace(" ", "_"), "page": page,
         "bbox": [0.1, 0.1, 0.4, 0.15], "source": source, "preferred": True, "candidate_id": cid, "element_id": None,
         "node_id": None, "trace": "textract:FORMS", "schema_field": None, "projection": None}
    d.update(extra)
    return d


FIXTURE_REPORT = {
    "dynamic_fields": [
        _pair("c1", "REPORT ID", "RPT-260708-E7BE23", node_id="n-1", element_id="e-7", schema_field="report_id", projection="mapped"),
        _pair("c2", "Inspector", "J. Doe", source="discovery", schema_field="inspector", projection="review_needed"),
        _pair("c3", "REPORT ID", "RPT-26O7O8-E7BE23", preferred=False, schema_field="report_id", projection="conflicting"),
        _pair("c4", "Site", "North yard", page=2),
        _pair("c5", "Ref", "A-1", schema_field="reference", projection="unmapped"),
        {"label": "", "value": "orphan value", "source": "textract"},
    ],
    "pages": [
        {"page": 1, "quality_score": 0.48, "basis": "ocr 0.96 × coverage 1.00; grounded reads 0.50 × ocr 0.96 = floor 0.48 (below the probe score)"},
        {"page": 2, "quality_score": 0.91},
    ],
    "fields": [{"name": "report_id", "value": "RPT-260708-E7BE23", "extraction_confidence": 0.93}],
}


def test_dynamic_fields_view_rows_carry_label_value_page_source_and_the_projection() -> None:
    view = parsure_view.dynamic_fields_view(FIXTURE_REPORT)
    assert view["count"] == 5, "a pair without a label is not a field"
    r1, r2, r3, r4, r5 = view["rows"]
    assert r1["label"] == "REPORT ID" and r1["value"] == "RPT-260708-E7BE23" and r1["page"] == 1
    assert r1["source"] == "textract" and r1["source_words"] == "Textract"
    assert r1["schema_field"] == "report_id" and r1["projection"] == "mapped" and r1["projection_words"] == "mapped"
    assert r1["node_id"] == "n-1" and r1["element_id"] == "e-7" and r1["preferred"] is True
    assert r2["source_words"] == "discovery" and r2["projection"] == "review_needed" and r2["projection_words"] == "review needed"
    assert r3["preferred"] is False and r3["projection"] == "conflicting"
    assert r4["schema_field"] is None and r4["projection"] is None and r4["projection_words"] is None, "no log entry → no note"
    assert r5["projection"] == "unmapped" and r5["projection_words"] == "unmapped"
    for row in view["rows"]:
        assert "confidence" not in row and "conf_pct" not in row and "trace" not in row
    # The page order is the list's own: nothing is re-sorted.
    assert [r["candidate_id"] for r in view["rows"]] == ["c1", "c2", "c3", "c4", "c5"]


def test_dynamic_fields_view_empty_list_says_so() -> None:
    assert parsure_view.dynamic_fields_view({"fields": [{"name": "x", "value": "1"}]}) == {"count": 0, "rows": []}
    assert parsure_view.dynamic_fields_view({"dynamic_fields": "not a list"}) == {"count": 0, "rows": []}


def test_quality_help_view_repeats_the_per_page_basis_and_is_none_without_one() -> None:
    help_ = parsure_view.quality_help_view(FIXTURE_REPORT)
    assert help_ == {"lines": ["Page 1: ocr 0.96 × coverage 1.00; grounded reads 0.50 × ocr 0.96 = floor 0.48 (below the probe score)"],
                     "pages": {1: "ocr 0.96 × coverage 1.00; grounded reads 0.50 × ocr 0.96 = floor 0.48 (below the probe score)"}}
    assert parsure_view.quality_help_view({"pages": [{"page": 1, "quality_score": 0.9}]}) is None
    assert parsure_view.quality_help_view({}) is None


def test_execution_view_carries_the_document_fields_and_the_quality_help_for_the_record_page() -> None:
    ex = parsure_view.execution_view(FIXTURE_REPORT)
    assert ex["dynamic_fields"]["count"] == 5 and ex["dynamic_fields"]["rows"][0]["value"] == "RPT-260708-E7BE23"
    assert ex["quality_help"]["lines"][0].startswith("Page 1: ocr 0.96")
    assert ex["raw_candidates"]["count"] == 0, "the raw pool is untouched"
