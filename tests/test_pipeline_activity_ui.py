"""The Pipeline activity panel in the prototype shell (customer complaint 2026-09-28:
"requests are not reaching OpenRouter and I cannot see whether the pipeline ran";
OpenRouter removed 2026-10-01 — the fixtures use Bedrock ids).

Static checks over ``prototype/index.html`` / ``prototype/shell.js`` and the seven
catalogs, plus the shipped normalisers executed in node against the backend contract
(``GET /api/projects/<id>/pipeline-activity``). The rule under test is the honesty
one: a stage the record lacks is ``no_record``, a status word the shell does not know
is *not* mapped to ``ran``, and the probe / 24 h warnings follow the record only.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from prompt_matrix.i18n import CATALOGS, EN, LOCALES
from prompt_matrix.ui_cache import APP_CSS, APP_JS

ROOT = Path(__file__).resolve().parents[1]
SHELL_JS = ROOT / "prototype" / "shell.js"
INDEX_HTML = ROOT / "prototype" / "index.html"
SHELL_CSS = ROOT / "prototype" / "shell.css"

STAGE_ORDER = (
    "parse", "intake", "z3", "llm_grounding", "discovery", "vision", "redhat_graph",
    "redhat_targeted", "compile_draft", "anchor", "entailment", "edit", "redhat_multipass", "compare",
)
STAGE_LABELS = {
    "parse": "Parse", "intake": "Intake & routing (LAYA)", "z3": "Z3 verification",
    "llm_grounding": "Grounded field pass", "discovery": "Field discovery", "vision": "Vision",
    "redhat_graph": "Red-Hat graph critique", "redhat_targeted": "Red-Hat targeted re-read",
    "compile_draft": "Compile draft", "anchor": "Claim anchoring", "entailment": "Entailment",
    "edit": "Surgical edit", "redhat_multipass": "Red-Hat audit", "compare": "Compare",
}

#: The contract payload, as given to the frontend on 2026-09-28.
FIXTURE: dict[str, Any] = {
    "ok": True,
    "llm": {
        "backend": "bedrock", "provider": "bedrock", "key_present": True, "key_hint": "sk-or-…9f3a",
        "probe": {"status": "reachable", "detail": "HTTP 200 …", "checked_at": "2026-09-28T13:00:00Z", "ms": 412},
        "last_call_at": "2026-09-28T13:02:11Z", "calls_24h": 37, "failed_24h": 2,
    },
    "stages": [
        {"stage": "parse", "label": "Parse", "status": "ran", "detail": "jdf-cli+tesseract · 1 page · OCR 0.80 · 18.2 s",
         "model": None, "calls": 0, "ok": 0, "failed": 0, "last_at": "2026-09-28T12:59:00Z", "last_ms": 18212,
         "last_http_status": None, "last_error": None, "source": "ingest_job"},
        {"stage": "llm_grounding", "label": "Grounded field pass", "status": "ran", "model": "bedrock/us.anthropic.claude-sonnet-5-5",
         "calls": 3, "ok": 3, "failed": 0, "last_at": "2026-09-28T13:01:00Z", "last_ms": 2140, "last_http_status": 200,
         "last_error": None, "detail": "3 fields offered, 2 grounded", "source": "model_calls+execution"},
        {"stage": "entailment", "label": "Entailment", "status": "failed", "model": "bedrock/us.anthropic.claude-sonnet-5-5",
         "calls": 2, "ok": 1, "failed": 1, "last_at": "2026-09-28T13:02:11Z", "last_ms": 1830, "last_http_status": 401,
         "last_error": "HTTP 401 Unauthorized: invalid API key", "detail": None, "source": "model_calls"},
        {"stage": "compare", "label": "Compare", "status": "no_record"},
        # A status word the shell does not know must surface as-is, never as green.
        {"stage": "vision", "label": "Vision", "status": "completed", "calls": 1, "ok": 1, "failed": 0},
    ],
    "calls": [
        {"id": "mc-1", "stage": "llm_grounding", "backend": "bedrock", "provider": "bedrock", "model": "bedrock/us.anthropic.claude-sonnet-5-5",
         "status": "ok", "http_status": 200, "ms": 2140, "prompt_chars": 4210, "completion_chars": 120, "input_tokens": 1100,
         "output_tokens": 40, "error": None, "task": "draft-1", "created_at": "2026-09-28T13:01:00Z"},
        {"id": "mc-2", "stage": "entailment", "backend": "bedrock", "provider": "bedrock",
         "model": "bedrock/us.anthropic.claude-sonnet-5-5", "status": "error", "http_status": 401, "ms": 1830,
         "prompt_chars": 4210, "completion_chars": 0, "input_tokens": None, "output_tokens": None,
         "error": "HTTP 401 Unauthorized: invalid API key", "task": "draft-1", "created_at": "2026-09-28T13:02:11Z"},
        {"id": "mc-0", "stage": "entailment", "model": "bedrock/x", "status": "ok", "created_at": "2026-09-28T12:00:00Z"},
    ],
}

#: The shell functions the harness executes, in dependency order.
_HELPERS = (
    "_parseServerTs", "_pipelineStatusKey", "_pipelineRow", "_pipelineStageRows",
    "_pipelineWarnings", "_pipelineCallsNewestFirst",
)


def _extract_function(source: str, name: str) -> str:
    """The source text of ``function name(...) { … }``, brace-balanced."""
    start = source.index(f"function {name}(")
    depth = 0
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"unbalanced braces after function {name}")


def _extract_var(source: str, name: str) -> str:
    """``var NAME = [...];`` — the fixed stage list and the status vocabulary."""
    start = source.index(f"var {name} = ")
    end = source.index("];", start) + 2
    return source[start:end]


def _run_in_node(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed, so the shell's normalisers cannot be executed")
    source = SHELL_JS.read_text(encoding="utf-8")
    harness = "\n".join(
        [
            _extract_var(source, "PIPELINE_STAGES"),
            _extract_var(source, "PIPELINE_STATUSES"),
            *[_extract_function(source, name) for name in _HELPERS],
            f"var payloads = {json.dumps(payloads)};",
            "process.stdout.write(JSON.stringify(payloads.map(function (p) {",
            "  return { rows: _pipelineStageRows(p), warnings: _pipelineWarnings(p.llm),",
            "           calls: _pipelineCallsNewestFirst(p).map(function (c) { return c.id; }) };",
            "})));",
        ]
    )
    result = subprocess.run([node, "-e", harness], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


# ---------------------------------------------------------------------------
# Markup, script and style
# ---------------------------------------------------------------------------

def test_markup_carries_the_panel_in_compiler_inspector_and_fields_panes() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")
    hosts = re.findall(r'data-pipeline-host="([^"]+)"', html)
    assert sorted(hosts) == ["compiler", "fields", "inspector"]
    for el_id in ("section-pipeline", "pipeline-activity-compiler", "insp-pipeline",
                  "pipeline-activity-inspector", "fields-pipeline", "pipeline-activity-fields"):
        assert f'id="{el_id}"' in html, el_id
    assert html.count('data-i18n="shell.pipeline.title"') == 3
    # The Fields copy sits outside the report-gated header: a parse that never
    # ran must be visible with no report at all.
    fields_head_end = html.index("</header>", html.index('id="fields-head"'))
    assert html.index('id="fields-pipeline"') > fields_head_end


def test_shell_reads_the_contract_endpoint_and_names_a_non_200() -> None:
    js = SHELL_JS.read_text(encoding="utf-8")
    assert '"/pipeline-activity?limit=50"' in js
    for fn in ("_refreshPipelineActivity", "_renderPipelineActivity", "_schedulePipelineActivityPoll",
               "_pipelineActivityChanged", "_pipelineActive", "_pipelineStageRows", "_pipelineWarnings",
               "_buildPipelineBadge", "_buildPipelineStages", "_buildPipelineCalls", "_pipelineWhen"):
        assert f"function {fn}(" in js, fn
    assert "var PIPELINE_POLL_MS = 10000;" in js
    assert '"shell.pipeline.unavailable", "activity endpoint unavailable (HTTP {code})"' in js
    assert '"shell.pipeline.blocked", "No request can leave this server: {detail}"' in js
    assert '"shell.pipeline.silent", "No model request has been recorded in the last 24 h."' in js
    assert 'data-pipeline-refresh' in js
    # The poll follows the shell's own active-state flags, nothing else.
    active = _extract_function(js, "_pipelineActive")
    assert "runInProgress" in active and "__activeJobs" in active and '"streaming"' in active
    # Refresh at every lifecycle edge the shell already performs.
    assert _extract_function(js, "_setRunInProgress").count("_pipelineActivityChanged()") == 1
    assert _extract_function(js, "_pollIngestJobs").count("_pipelineActivityChanged()") == 1
    assert js.count("_pipelineActivityChanged();\n        return _openParsureReport(pid, __parsureReportId);") == 2
    # Both time forms: relative text, absolute title.
    when = _extract_function(js, "_pipelineWhen")
    assert "el.title = _whenWords(ts)" in when and 'setAttribute("datetime"' in when


def test_shell_never_defaults_a_stage_to_green() -> None:
    js = SHELL_JS.read_text(encoding="utf-8")
    key = _extract_function(js, "_pipelineStatusKey")
    assert 'return "no_record";' in key and 'return "unknown";' not in key.split("PIPELINE_STATUSES")[0]
    tone = _extract_function(js, "_pipelineStatusTone")
    assert tone.count('"verified"') == 1 and 'status === "ran"' in tone


def test_style_covers_the_panel_with_the_shell_tones() -> None:
    css = SHELL_CSS.read_text(encoding="utf-8")
    for sel in (".pipeline-activity", ".pipe-chip", ".pipe-alert", ".pipe-stage", ".pipe-call", ".pipe-calls > summary",
                '.pipe-chip[data-tone="verified"]', '.pipe-chip[data-tone="contradicted"]', '.pipe-alert[data-tone="partial"]'):
        assert sel in css, sel


def test_ui_cache_bumped_for_the_pipeline_panel() -> None:
    assert APP_CSS == "assure-117"
    assert APP_JS == "assure-117"


# ---------------------------------------------------------------------------
# Catalogs
# ---------------------------------------------------------------------------

def _pipeline_keys_used() -> set[str]:
    js = SHELL_JS.read_text(encoding="utf-8")
    html = INDEX_HTML.read_text(encoding="utf-8")
    # Whole keys only: the composed prefixes (``"shell.pipeline.stage." + stage``) are
    # covered by the explicit sets below.
    used = set(k for k in re.findall(r'"(shell\.pipeline\.[a-z0-9_.]+)"', js) if not k.endswith("."))
    used |= set(re.findall(r'data-i18n="(shell\.pipeline\.[a-z0-9_.]+)"', html))
    # Composed keys: the stage labels, the status words, the probe words.
    used |= {f"shell.pipeline.stage.{s}" for s in STAGE_ORDER}
    used |= {f"shell.pipeline.status.{s}" for s in ("ran", "failed", "skipped", "pending", "no_record")}
    used |= {f"shell.pipeline.probe.{s}" for s in ("reachable", "unauthorized", "unreachable", "no_key", "not_probed")}
    return used


def test_every_pipeline_string_is_in_every_catalog() -> None:
    used = _pipeline_keys_used()
    assert used, "the shell uses no shell.pipeline.* key, so this test proves nothing"
    for locale in LOCALES:
        cat = CATALOGS[locale]
        missing = sorted(k for k in used if k not in cat or not str(cat[k]).strip())
        assert not missing, f"{locale} lacks {missing}"
    for stage, label in STAGE_LABELS.items():
        assert EN[f"shell.pipeline.stage.{stage}"] == label
    assert "{code}" in EN["shell.pipeline.unavailable"]
    assert "{detail}" in EN["shell.pipeline.blocked"]


@pytest.mark.parametrize("locale", [loc for loc in LOCALES if loc != "en"])
def test_the_panel_title_and_sentences_are_translated(locale: str) -> None:
    """The markup's key and the sentences a reader acts on must not fall back to English."""
    keys = ("shell.pipeline.title", "shell.pipeline.refresh", "shell.pipeline.unavailable", "shell.pipeline.blocked",
            "shell.pipeline.silent", "shell.pipeline.status.no_record", "shell.pipeline.no_calls")
    cat = CATALOGS[locale]
    untranslated = sorted(k for k in keys if cat.get(k) == EN.get(k))
    assert not untranslated, f"{locale} still shows English for {untranslated}"


# ---------------------------------------------------------------------------
# The normalisers, executed
# ---------------------------------------------------------------------------

def test_stage_rows_follow_the_fixed_order_and_missing_stages_read_no_record() -> None:
    out = _run_in_node([FIXTURE])[0]
    rows = out["rows"]
    assert [r["stage"] for r in rows] == list(STAGE_ORDER)
    assert [r["label"] for r in rows] == [STAGE_LABELS[s] for s in STAGE_ORDER]
    by = {r["stage"]: r for r in rows}
    assert by["parse"]["status"] == "ran" and by["parse"]["recorded"] is True
    assert by["parse"]["model"] == "" and by["parse"]["last_ms"] == 18212 and by["parse"]["last_http_status"] is None
    assert by["llm_grounding"]["model"] == "bedrock/us.anthropic.claude-sonnet-5-5"
    assert (by["llm_grounding"]["calls"], by["llm_grounding"]["ok"], by["llm_grounding"]["failed"]) == (3, 3, 0)
    assert by["entailment"]["status"] == "failed" and by["entailment"]["last_http_status"] == 401
    assert by["entailment"]["last_error"] == "HTTP 401 Unauthorized: invalid API key"
    # Sent as no_record and not sent at all read the same: no record, nothing inferred.
    assert by["compare"]["status"] == "no_record"
    for absent in ("intake", "z3", "discovery", "redhat_graph", "redhat_targeted", "compile_draft", "anchor", "edit", "redhat_multipass"):
        assert by[absent]["status"] == "no_record" and by[absent]["recorded"] is False, absent
        assert by[absent]["model"] == "" and by[absent]["calls"] is None and by[absent]["last_error"] == ""
    # A status the shell does not know is kept as the server wrote it, never "ran".
    assert by["vision"]["status"] == "unknown" and by["vision"]["status_raw"] == "completed"


def test_unknown_stages_are_appended_after_the_fixed_list() -> None:
    payload = {"stages": [{"stage": "ocr_repair", "label": "OCR repair", "status": "skipped"}]}
    rows = _run_in_node([payload])[0]["rows"]
    assert len(rows) == len(STAGE_ORDER) + 1
    assert rows[-1]["stage"] == "ocr_repair" and rows[-1]["label"] == "OCR repair" and rows[-1]["status"] == "skipped"


def test_warnings_follow_the_probe_and_the_24h_counter_only() -> None:
    reachable = FIXTURE
    unauthorized = {"llm": {"backend": "bedrock", "probe": {"status": "unauthorized", "detail": "HTTP 401 invalid key"}, "calls_24h": 0}}
    no_key = {"llm": {"backend": "bedrock", "key_present": False, "probe": {"status": "no_key", "detail": None}, "calls_24h": 5}}
    ollama_idle = {"llm": {"backend": "ollama", "probe": {"status": "reachable"}, "calls_24h": 0}}
    not_probed = {"llm": {"backend": "bedrock", "probe": {"status": "not_probed"}, "calls_24h": None}}
    outs = _run_in_node([reachable, unauthorized, no_key, ollama_idle, not_probed, {}])
    assert outs[0]["warnings"] == []
    assert outs[1]["warnings"] == [
        {"kind": "blocked", "status": "unauthorized", "detail": "HTTP 401 invalid key"},
        {"kind": "silent", "status": "", "detail": ""},
    ]
    assert outs[2]["warnings"] == [{"kind": "blocked", "status": "no_key", "detail": "no_key"}]
    assert outs[3]["warnings"] == [], "an idle local Ollama is not a silence"
    assert outs[4]["warnings"] == [], "a counter the server did not send is not a zero"
    assert outs[5]["warnings"] == []


def test_recent_requests_are_newest_first() -> None:
    assert _run_in_node([FIXTURE])[0]["calls"] == ["mc-2", "mc-1", "mc-0"]
