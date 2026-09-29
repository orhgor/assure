"""The "What the page says (raw candidates)" surface (customer finding V-B, 2026-09-29).

``report.raw_candidates`` and ``report.projection`` existed since plan V4 but neither
the workbench nor ``/parsing/<id>`` rendered them, so a reviewer could not see the
page's own facts when the schema layer was empty. These are static checks over
``prototype/`` (the shell is vanilla JS the tests read as text), the seven catalogs,
and unit checks of ``services.parsure_view.raw_candidates_view``.

The rule under test is the honesty one: verbatim text, the projection outcome the
log wrote (``none`` when no log names the candidate), no confidence anywhere on a
candidate, and an empty pool that says so.
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
NON_ENGLISH = [loc for loc in LOCALES if loc != "en"]


def _renderer_body() -> str:
    m = re.search(r"function _renderFieldsRawCandidates\(rep\) \{.*?\n    function _overrideType\(\)", SHELL_JS, re.S)
    assert m, "shell.js has no _renderFieldsRawCandidates(rep)"
    return m.group(0)


# ---------------------------------------------------------------------------
# Markup and script
# ---------------------------------------------------------------------------


def test_fields_pane_carries_a_collapsed_raw_candidates_section() -> None:
    m = re.search(r'<details class="fields-execution fields-raw" id="fields-raw"[^>]*>(.*?)</details>', INDEX_HTML, re.S)
    assert m, "index.html has no #fields-raw details"
    opening = INDEX_HTML[m.start():INDEX_HTML.index(">", m.start()) + 1]
    assert " open" not in opening, "the raw pool is collapsed by default"
    assert 'data-count="0"' in opening and " hidden" in opening
    body = m.group(1)
    for needle in ('data-i18n="shell.fields.raw.title"', 'id="fields-raw-count"', 'id="fields-raw-stats"',
                   'id="fields-raw-empty"', '<ul class="raw-list" id="fields-raw-list" role="list">'):
        assert needle in body, needle
    # It sits beside the execution panel, inside the Fields header.
    assert INDEX_HTML.index('id="fields-execution"') < m.start() < INDEX_HTML.index('id="fields-pipeline"')


def test_shell_renders_the_pool_from_report_raw_candidates_and_the_projection_log() -> None:
    body = _renderer_body()
    assert "rep.raw_candidates" in body
    assert "candidates_log" in body
    assert "rep.projection" in body and "ex.projection" in body, "the log is read from report.projection, else execution.projection"
    assert "_rawStatsWords(ex)" in body and "ex.raw_candidates" in SHELL_JS.split("function _rawStatsWords")[1].split("\n    }")[0]
    assert re.search(r"_renderFieldsExecution\(rep\);\n\s*_renderFieldsRawCandidates\(rep\);", SHELL_JS), "called right after the execution panel"
    # Rows carry label, verbatim text, page, source chip, markers and the outcome chip.
    for needle in ("c.label_anchor || c.name_hint", "c.raw_text", '"raw-text"', "shell.fields.page", "_rawSourceWords(c.source_kind)",
                   "shell.fields.raw.not_preferred", "shell.fields.raw.preferred", "shell.fields.raw.uncorroborated", "_rawOutcomeChip(entry)"):
        assert needle in body, needle
    # Locating reuses the field rows' own paths: the paragraph locator, else the source sheet.
    locator = SHELL_JS.split("function _rawLocator(c)")[1].split("\n    function _renderFieldsRawCandidates")[0]
    assert "_locateNode(nodeId)" in locator and "_nodeWrapper(nodeId)" in locator
    assert '_showInSource({ page: page, bbox: bbox, text: String(c.raw_text || "") }, "parsure")' in locator
    assert "return null;" in locator, "no locator → the row is static, nothing is faked"
    assert "li.classList.add(\"is-locatable\")" in body and 'li.setAttribute("role", "button")' in body


def test_shell_draws_no_confidence_on_a_candidate() -> None:
    body = _renderer_body()
    chip = SHELL_JS.split("function _rawOutcomeChip(entry)")[1].split("\n    }")[0]
    stats = SHELL_JS.split("function _rawStatsWords(ex)")[1].split("\n    }")[0]
    for text in (body, chip, stats):
        assert "confidence" not in text.lower()
        assert "conf-bar" not in text and "_pct(" not in text and "progressbar" not in text
    assert "shell.fields.raw.empty" in body and "shell.fields.raw.outcome.none" in chip


def test_outcome_chip_speaks_the_four_log_words_and_maps_to_the_field() -> None:
    chip = SHELL_JS.split("function _rawOutcomeChip(entry)")[1].split("\n    }")[0]
    assert '"mapped → {field}"' in chip and "entry.field" in chip
    for word, tone in (("mapped", "verified"), ("conflicting", "contradicted"), ("review_needed", "partial")):
        assert f'outcome === "{word}"' in chip, word
        assert f'tone = "{tone}"' in chip, tone
    assert 'outcome === "unmapped"' in chip
    assert 'chip.setAttribute("data-outcome", outcome)' in chip


def test_styles_for_the_raw_rows_exist() -> None:
    for sel in (".fields-raw-stats", ".fields-raw-empty", ".raw-list", ".raw-row", ".raw-row.is-locatable", ".raw-row.is-locatable:focus-visible",
                ".raw-head", ".raw-label", ".raw-text", ".raw-meta", '.raw-row[data-preferred="0"] .raw-text', '.raw-row[data-corroborated="0"] .raw-text'):
        assert sel in SHELL_CSS, sel


def test_ui_cache_bumped_for_the_raw_pool() -> None:
    assert APP_CSS == "assure-117"
    assert APP_JS == "assure-117"


# ---------------------------------------------------------------------------
# Catalogs
# ---------------------------------------------------------------------------

RAW_KEYS = tuple(k for k in EN if k.startswith("shell.fields.raw.") or k.startswith("parsing.raw."))


def test_every_raw_string_the_shell_uses_is_in_the_catalog() -> None:
    # A key that ends in a dot is a dynamic prefix (`"shell.fields.raw.source." + k`); its members are listed below.
    used = set(re.findall(r'_tf?\("(shell\.fields\.raw\.[a-z_.]*[a-z_])"', SHELL_JS))
    used |= {"shell.fields.raw.source." + k for k in ("layout_text", "table_cell", "image_vision", "textract", "discovery")}
    used |= {"shell.fields.raw.outcome." + k for k in ("mapped", "mapped_nofield", "unmapped", "conflicting", "review_needed", "none")}
    missing = sorted(k for k in used if k not in EN)
    assert not missing, missing
    assert "shell.fields.raw.title" in EN and "parsing.raw.title" in EN and "parsing.raw.empty" in EN


@pytest.mark.parametrize("locale", LOCALES)
def test_raw_strings_present_and_non_empty_in_every_locale(locale: str) -> None:
    catalog = CATALOGS[locale]
    empty = sorted(k for k in RAW_KEYS if not str(catalog.get(k) or "").strip())
    assert not empty, f"{locale}: {empty}"


@pytest.mark.parametrize("locale", NON_ENGLISH)
def test_the_declared_title_and_the_reader_facing_words_are_translated(locale: str) -> None:
    catalog = CATALOGS[locale]
    for key in ("shell.fields.raw.title", "shell.fields.raw.empty", "shell.fields.raw.count", "shell.fields.raw.no_stats",
                "shell.fields.raw.outcome.mapped", "shell.fields.raw.outcome.unmapped", "shell.fields.raw.outcome.conflicting",
                "shell.fields.raw.outcome.review_needed", "shell.fields.raw.outcome.none", "shell.fields.raw.uncorroborated",
                "parsing.raw.title", "parsing.raw.empty"):
        assert catalog[key] != EN[key], f"{locale} falls back to English for {key}"


# ---------------------------------------------------------------------------
# parsure_view.raw_candidates_view — the record page's rows
# ---------------------------------------------------------------------------

def _cand(cid, label, text, page=1, kind="layout_text", **extra):
    c = {"candidate_id": cid, "name_hint": label.lower().replace(" ", "_"), "raw_text": text, "normalized_value": None,
         "label_anchor": label, "page": page, "node_id": None, "element_id": None,
         "source_span": {"span_type": "text_range", "start_char": 0, "end_char": len(text)},
         "source_kind": kind, "source_priority": 50, "corroborated": True, "preferred": True, "trace": "test"}
    c.update(extra)
    return c


def test_raw_candidates_view_rows_carry_verbatim_text_outcomes_and_markers() -> None:
    report = {
        "raw_candidates": [
            _cand("c1", "REPORT ID", "RPT-260708-E7BE23", node_id="n-1", element_id="e-7"),
            _cand("c2", "Inspector", "J. Doe", kind="image_vision", corroborated=False),
            _cand("c3", "REPORT ID", "RPT-26O7O8-E7BE23", preferred=False),
            _cand("c4", "Site", "North yard", page=2, kind="table_cell"),
        ],
        "projection": {"status": "completed", "candidates_log": {
            "c1": {"outcome": "mapped", "field": "report_id"}, "c2": {"outcome": "review_needed", "field": "inspector"},
            "c3": {"outcome": "conflicting", "field": "report_id"}}},
        "execution": {"raw_candidates": {"status": "completed", "candidates": 4, "collected": 5,
                                         "by_source": {"layout_text": 2, "table_cell": 1, "image_vision": 1, "textract": 0, "discovery": 0},
                                         "failed_sources": {"textract": "RuntimeError: no forms"}}},
    }
    view = parsure_view.raw_candidates_view(report)
    assert view["count"] == 4 and view["recorded"] is True and view["status"] == "completed"
    assert view["stats"] == ["completed", "4 candidates", "layout text 2", "table cell 1", "image vision 1", "source failed: Textract"]
    r1, r2, r3, r4 = view["rows"]
    assert r1["label"] == "REPORT ID" and r1["raw_text"] == "RPT-260708-E7BE23" and r1["page"] == 1
    assert r1["outcome"] == "mapped" and r1["field"] == "report_id" and r1["outcome_words"] == "mapped → report_id"
    assert r1["node_id"] == "n-1" and r1["element_id"] == "e-7" and r1["show_preferred"] is True
    assert r2["source_kind"] == "image_vision" and r2["uncorroborated"] is True and r2["outcome"] == "review_needed" and r2["outcome_words"] == "review needed"
    assert r3["preferred"] is False and r3["show_preferred"] is False and r3["outcome"] == "conflicting"
    assert r4["outcome"] == "none" and r4["outcome_words"] == "no projection" and r4["source_words"] == "table cell"
    for row in view["rows"]:
        assert "confidence" not in row and "conf_pct" not in row


def test_raw_candidates_view_empty_pool_and_missing_stats_say_so() -> None:
    view = parsure_view.raw_candidates_view({"fields": [{"name": "x", "value": "1"}]})
    assert view == {"count": 0, "recorded": False, "status": None, "stats": [], "rows": []}
    # The projection log may live only on execution; the preferred mark is quiet without an overlap.
    view = parsure_view.raw_candidates_view({"raw_candidates": [_cand("c1", "A", "1")],
                                             "execution": {"projection": {"candidates_log": {"c1": {"outcome": "unmapped", "field": None}}}}})
    assert view["rows"][0]["outcome"] == "unmapped" and view["rows"][0]["show_preferred"] is False and view["recorded"] is False


def test_execution_view_carries_the_raw_pool_for_the_record_page() -> None:
    ex = parsure_view.execution_view({"raw_candidates": [_cand("c1", "A", "1")]})
    assert ex["raw_candidates"]["count"] == 1 and ex["raw_candidates"]["rows"][0]["raw_text"] == "1"
