"""Grounded LLM extraction (services/llm_extraction): a candidate is used only
when its quote is verbatim in the page text and its value is inside the quote
(spec §8 item 6 — the model names a place, never a value); every failure mode
is a note, never an exception; the flag turns the model call off."""

from __future__ import annotations

import json
import time

import pytest

from prompt_matrix.services import field_extractor as fx
from prompt_matrix.services import llm_extraction as lx

PROSE = (
    "Northstar Mutual issues this personal auto policy, numbered NAP-4471-2025, to Daniel R. Whitfield.\n"
    "Coverage begins on 03/01/2025 and ends on 03/01/2026.\n"
    "The insured vehicle is a 2003 Honda Accord EX Sedan bearing vehicle identification number 1HGCM82633A004352.\n"
    "The collision   deductible is $500 and the comprehensive deductible is $250.\n"
    "The total annual premium for this policy is $1,486.00, countersigned by Marianne Costa."
)
SPECS = {s.name: s for s in fx.FIELD_TAXONOMY["auto_policy"]}


def _specs(*names):
    return [SPECS[n] for n in names]


def _run(answer, *names, text=PROSE, **kw):
    notes = []
    completion = answer if callable(answer) else (lambda prompt: answer if isinstance(answer, str) else json.dumps(answer))
    params = dict(completion=completion, notes=notes, parser_name="jdf-cli", page_quality=[1.0])
    params.update(kw)
    fields = lx.extract_missing_fields("auto_policy", [text], _specs(*names), **params)
    return {f["name"]: f for f in fields}, notes


def test_correct_quote_is_accepted_with_span_method_and_factor(monkeypatch):
    monkeypatch.setenv("PARSURE_LLM_EXTRACTION", "1")
    answer = {
        "policy_number": {"quote": "personal auto policy, numbered NAP-4471-2025, to", "value": "NAP-4471-2025", "page": 1},
        # whitespace differs from the page ("collision   deductible") and the value is normalised money
        "collision_deductible": {"quote": "the collision deductible is $500 and", "value": "500"},
        "effective_date": {"quote": "Coverage begins on 03/01/2025", "value": "March 1, 2025"},
        "vin": {"quote": "vehicle identification number 1HGCM82633A004352", "value": "1hgcm82633a004352"},
        "premium": {"quote": "THE TOTAL ANNUAL PREMIUM FOR THIS POLICY IS $1,486.00", "value": "$1,486.00"},
    }
    fields, notes = _run(answer, "policy_number", "collision_deductible", "effective_date", "vin", "premium")
    pn = fields["policy_number"]
    assert pn["value"] == "NAP-4471-2025" and pn["extraction_method"] == "llm_grounded"
    span = pn["source_span"]
    assert span["page"] == 1 and PROSE[span["start_char"]:span["end_char"]] == "NAP-4471-2025"
    assert pn["provenance_confidence"] == 1.0 and pn["reason"] is None
    assert pn["confidence_basis"].endswith(f"× llm_grounded ({fx.LLM_GROUNDED_FACTOR:.2f}) = {pn['extraction_confidence']:.2f}")
    assert pn["extraction_confidence"] == pytest.approx(0.85 * 1.0 * fx.LLM_GROUNDED_FACTOR, abs=0.01)
    assert pn["grounding"]["quote"].startswith("personal auto policy, numbered")
    coll = fields["collision_deductible"]
    assert coll["value"] == 500.0 and coll["raw"] == "$500"
    assert PROSE[coll["source_span"]["start_char"]:coll["source_span"]["end_char"]] == "$500"
    assert fields["effective_date"]["value"] == "2025-03-01" and fields["effective_date"]["raw"] == "03/01/2025"
    assert fields["vin"]["value"] == "1HGCM82633A004352" and fields["vin"]["vin_check"]["valid"] is True
    assert fields["premium"]["value"] == 1486.0 and fields["premium"]["raw"] == "$1,486.00"
    assert any("5 field(s) grounded, 0 candidate(s) rejected" in n for n in notes)


def test_plausible_value_not_in_text_is_rejected_field_stays_none(monkeypatch):
    monkeypatch.setenv("PARSURE_LLM_EXTRACTION", "1")
    answer = {
        # value is not in the document at all (fabricated quote)
        "liability_limit": {"quote": "Bodily injury liability of $250,000 each person", "value": "$250,000"},
        # quote is real but the value is not inside it
        "agent_name": {"quote": "countersigned by Marianne Costa.", "value": "Mary Agent"},
        # money value inside a real quote but a different amount than the model claims
        "comprehensive_deductible": {"quote": "the comprehensive deductible is $250.", "value": "$2,500"},
        # a bare string that is not in the text
        "insured_name": "Daniel Whitfield Jr.",
    }
    fields, notes = _run(answer, "liability_limit", "agent_name", "comprehensive_deductible", "insured_name")
    for name in ("liability_limit", "agent_name", "comprehensive_deductible", "insured_name"):
        f = fields[name]
        assert f["value"] is None and f["raw"] is None, name
        assert f["extraction_confidence"] == 0.0 and f["review_required"] is True
        assert f["reason"] == "field not found" and f["source_span"] is None and f["extraction_method"] is None
    assert sum("llm candidate rejected" in n for n in notes) == 4
    assert any("quote not found verbatim" in n for n in notes) and any("value not inside the quoted text" in n for n in notes)


def test_garbage_json_and_timeout_are_skipped_with_a_note(monkeypatch):
    monkeypatch.setenv("PARSURE_LLM_EXTRACTION", "1")
    calls = []

    def chatty(prompt):
        calls.append(prompt)
        return "Sure! The policy number is NAP-4471-2025 and the premium is $1,486."

    fields, notes = _run(chatty, "policy_number", "premium")
    assert fields["policy_number"]["value"] is None and fields["premium"]["value"] is None
    assert len(calls) == lx.JSON_RETRIES + 1  # asked once more, then gave up
    assert notes[0] == "llm answer was not a JSON object; asked once more"
    assert notes[1].startswith("llm extraction skipped: model answer was not a JSON object (starts: 'Sure! The policy number")
    assert len(notes) == 2

    def slow(prompt):
        time.sleep(0.5)
        return json.dumps({"policy_number": {"quote": "numbered NAP-4471-2025", "value": "NAP-4471-2025"}})

    fields, notes = _run(slow, "policy_number", timeout_s=0.05)
    assert fields["policy_number"]["value"] is None
    assert notes == ["llm extraction skipped: timed out after 0.05s"]

    def broken(prompt):
        raise ConnectionError("connection refused")

    fields, notes = _run(broken, "policy_number")
    assert fields["policy_number"]["value"] is None and notes == ["llm extraction skipped: ConnectionError: connection refused"]

    fields, notes = _run(lambda p: (_ for _ in ()).throw(lx.LLMUnavailable("ollama/qwen2.5:1.5b: no key")), "policy_number")
    assert notes == ["llm extraction skipped: ollama/qwen2.5:1.5b: no key"]


def test_flag_off_means_no_model_call(monkeypatch):
    monkeypatch.setenv("PARSURE_LLM_EXTRACTION", "0")
    calls = []

    def spy(prompt):
        calls.append(prompt)
        return json.dumps({"policy_number": {"quote": "numbered NAP-4471-2025", "value": "NAP-4471-2025"}})

    fields, notes = _run(spy, "policy_number")
    assert calls == [] and fields["policy_number"]["value"] is None
    assert notes == ["llm extraction skipped: PARSURE_LLM_EXTRACTION is off"]
    monkeypatch.setenv("PARSURE_LLM_EXTRACTION", "false")
    assert lx.llm_extraction_enabled() is False
    monkeypatch.delenv("PARSURE_LLM_EXTRACTION")
    assert lx.llm_extraction_enabled() is True


def test_signature_is_never_offered_and_prompt_is_page_tagged(monkeypatch):
    monkeypatch.setenv("PARSURE_LLM_EXTRACTION", "1")
    seen = {}

    def spy(prompt):
        seen["prompt"] = prompt
        return "{}"

    notes = []
    fields = lx.extract_missing_fields("auto_policy", ["", PROSE], _specs("signature", "agent_name"), completion=spy, notes=notes)
    assert [f["name"] for f in fields] == ["signature", "agent_name"]
    assert '"signature"' not in seen["prompt"] and '"agent_name"' in seen["prompt"]
    assert "=== PAGE 2 ===" in seen["prompt"] and "=== PAGE 1 ===" not in seen["prompt"]  # blank page dropped, numbering kept
    assert all(f["value"] is None for f in fields)
    # signature-only request: nothing to ask, no call
    fields = lx.extract_missing_fields("auto_policy", [PROSE], _specs("signature"), completion=lambda p: pytest.fail("called"), notes=notes)
    assert fields[0]["value"] is None


def test_chunking_keeps_page_numbers_and_stops_when_all_found(monkeypatch):
    monkeypatch.setenv("PARSURE_LLM_EXTRACTION", "1")
    pages = ["Filler paragraph about nothing in particular.\n\n" * 60 + "The policy is numbered NAP-4471-2025.", "Second page: the total annual premium is $1,486.00."]
    chunks = lx.chunk_pages(pages, 300)
    assert len(chunks) >= 2 and chunks[-1][-1][0] == 2 and all(lx._count_tokens(t) <= 300 for chunk in chunks for _, t in chunk)
    prompts = []

    def per_chunk(prompt):
        prompts.append(prompt)
        out = {}
        if "NAP-4471-2025" in prompt:
            out["policy_number"] = {"quote": "numbered NAP-4471-2025", "value": "NAP-4471-2025"}
        if "$1,486.00" in prompt:
            out["premium"] = {"quote": "total annual premium is $1,486.00", "value": "1486"}
        return json.dumps(out)

    notes = []
    monkeypatch.setattr(lx, "_input_cap", lambda: 300 + lx.PROMPT_OVERHEAD_TOKENS)
    fields = {f["name"]: f for f in lx.extract_missing_fields("auto_policy", pages, _specs("policy_number", "premium"), completion=per_chunk, notes=notes)}
    assert fields["policy_number"]["value"] == "NAP-4471-2025" and fields["policy_number"]["source_span"]["page"] == 1
    assert fields["premium"]["value"] == 1486.0 and fields["premium"]["source_span"]["page"] == 2
    assert len(prompts) == len(chunks)  # one call per chunk; both fields found only in the last two
    assert all("=== PAGE 1 ===" in p for p in prompts[:-1]) and "=== PAGE 2 ===" in prompts[-1]
    # once every pending field is grounded the remaining chunks are not sent
    prompts.clear()
    lx.extract_missing_fields("auto_policy", [pages[1], pages[0]], _specs("premium"), completion=per_chunk, notes=[])
    assert len(prompts) == 1


def test_parse_json_answer_tolerates_fences_and_prose():
    assert lx.parse_json_answer('```json\n{"a": {"quote": "x", "value": "x"}}\n```') == {"a": {"quote": "x", "value": "x"}}
    assert lx.parse_json_answer('Here you go: {"a": null} thanks') == {"a": None}
    assert lx.parse_json_answer("[1, 2]") is None and lx.parse_json_answer("") is None and lx.parse_json_answer("{oops") is None
    # qwen2.5:1.5b wraps the object in a list, sometimes split over several objects
    assert lx.parse_json_answer('```json\n[\n {"a": {"quote": "x", "value": "x"}},\n {"b": null}\n]\n```') == {"a": {"quote": "x", "value": "x"}, "b": None}


def test_find_verbatim_maps_collapsed_match_back_to_original_offsets():
    text = "Line one\n\n   Total   Premium:\t$1,486.00 annual"
    span = lx.find_verbatim(text, "total premium: $1,486.00")
    assert span and text[span[0]:span[1]] == "Total   Premium:\t$1,486.00"
    assert lx.find_verbatim(text, "premium: $1,486.01") is None
    assert lx.ground_candidate(SPECS["premium"], [text], "Total Premium: $1,486.00", "1,486") == (0, "$1,486.00", 30, 39)
