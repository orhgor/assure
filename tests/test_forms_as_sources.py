"""Forms as compile sources (services/source_carry ``attach_form_fields``, 2026-09-28).

A filled form's text is captions the compile guard refuses to ground on; its
Parsure report holds the fields with verbatim grounding quotes. The walk appends
one sentence per found field, built only from the field's ``raw`` /
``grounding_quote`` (never the parsed value), with its own ``[S<n>]`` id and a
``parsure_field`` provenance; a citation to it anchors the claim to the field,
and the claim block's quote, page and ``grounding_source`` are the field's.
"""

from __future__ import annotations

import copy

from prompt_matrix.routers.draft import attach_citations_to_tree, build_sentence_map, run_draft_pipeline
from prompt_matrix.services import source_carry
from prompt_matrix.services.claim_policy import derive_claim
from prompt_matrix.services.compile_guard import looks_like_form
from prompt_matrix.services.source_carry import (
    FORM_FIELDS_HEADER,
    attach_form_fields,
    carry_plan,
    form_field_sentence,
    numbered_source_blocks,
)
from tests.test_draft import SOURCE_FILLER, _FakeGovernor, _parse_sse

# A filled CMS-1500-like form: numbered captions (``looks_like_form``) with the
# values written under them, so the field quotes are verbatim in the text.
FORM_TEXT = "\n".join(
    [
        "1. INSURED'S NAME (Last Name, First Name, Middle Initial)",
        "SAMPLE, JOHN Q",
        "2. PATIENT'S BIRTH DATE",
        "01 15 1980",
        "3. INSURED'S POLICY GROUP OR FECA NUMBER",
        "GRP-77812",
        "4. PATIENT'S ADDRESS (No., Street)",
        "12 Main Street",
        "5. INSURED'S ID NUMBER",
        "ID 44551",
        "6. PATIENT RELATIONSHIP TO INSURED",
        "Self",
        "7. OTHER INSURED'S NAME",
        "",
        "8. RESERVED FOR NUCC USE",
        "",
        "9. SIGNATURE ON FILE",
        "SIGNED",
        "10. DATE",
        "02 03 2026",
    ]
)

FIELDS = [
    {
        "name": "insured_name", "label": "Insured name", "value": "John Q Sample", "raw": "SAMPLE, JOHN Q",
        "grounding_quote": "SAMPLE, JOHN Q", "grounding_source": "label_anchor", "evidence_state": "found_unverified",
        "field_state": "unverified", "element_id": "p1e1:aaaaaaaaaaaa",
        "source_span": {"page": 1, "span_type": "bbox_relative", "bbox": [0.1, 0.1, 0.5, 0.12], "element_id": "p1e1:aaaaaaaaaaaa"},
    },
    {
        # The parsed value is ISO; only the page's own writing may be carried.
        "name": "patient_birth_date", "label": "Patient birth date", "value": "1980-01-15", "raw": "01 15 1980",
        "grounding_quote": "01 15 1980", "grounding_source": "label_anchor", "evidence_state": "found_verified",
        "field_state": "accepted", "element_id": "p1e3:bbbbbbbbbbbb",
        "source_span": {"page": 1, "span_type": "bbox_relative", "bbox": [0.1, 0.2, 0.3, 0.22], "element_id": "p1e3:bbbbbbbbbbbb"},
    },
    {
        # Suspect debris under a label is not a value and yields no sentence.
        "name": "policy_number", "label": "Policy number", "value": None, "raw": "~~",
        "grounding_quote": "Policy Number: ~~", "evidence_state": "found_suspect", "field_state": "unverified",
        "source_span": {"page": 1, "bbox": [0.1, 0.3, 0.2, 0.31]},
    },
    {"name": "vin", "label": "VIN", "value": None, "raw": None, "evidence_state": "not_on_document", "field_state": "not_found"},
]

REPORT = {
    "report_id": "pr-form-1",
    "document_id": "sub-form",
    "filename": "cms1500.pdf",
    "quality_flags": ["form_template"],
    "fields": FIELDS,
}


def _row(**over) -> dict:
    row = {"id": "sub-form", "filename": "cms1500.pdf", "extracted_text": FORM_TEXT + SOURCE_FILLER, "parse_confidence": 0.9}
    row.update(over)
    return row


def _patch_reports(monkeypatch, reports):
    monkeypatch.setattr("prompt_matrix.db.parsure_repository.list_reports", lambda pid, **kw: copy.deepcopy(reports))


# --------------------------------------------------------------------------- #
# The field sentence
# --------------------------------------------------------------------------- #
def test_field_sentence_is_the_label_and_the_text_as_written_never_the_parsed_value():
    sentence, prov = form_field_sentence(FIELDS[1], "pr-form-1")
    assert sentence == "Patient birth date: 01 15 1980"
    assert "1980-01-15" not in sentence
    assert prov == {
        "kind": "parsure_field", "field": "patient_birth_date", "element_id": "p1e3:bbbbbbbbbbbb",
        "page": 1, "bbox": [0.1, 0.2, 0.3, 0.22], "quote": "01 15 1980", "report_id": "pr-form-1",
    }
    # A suspect read and an absent field yield nothing — nothing is invented for them.
    assert form_field_sentence(FIELDS[2]) is None
    assert form_field_sentence(FIELDS[3]) is None
    # No raw: the grounding quote (the page line) is the text as written.
    only_quote = {**FIELDS[0], "raw": None, "grounding_quote": "INSURED: SAMPLE, JOHN Q"}
    assert form_field_sentence(only_quote)[0] == "Insured name: INSURED: SAMPLE, JOHN Q"
    # Neither text: nothing.
    assert form_field_sentence({**FIELDS[0], "raw": None, "grounding_quote": None}) is None


# --------------------------------------------------------------------------- #
# The walk
# --------------------------------------------------------------------------- #
def test_the_walk_appends_field_sentences_with_their_own_ids_inside_the_fence():
    row = _row(parsure_fields=FIELDS, parsure_report_id="pr-form-1")
    (block, entries), = numbered_source_blocks([row])
    fields = [e for e in entries if e[4]]
    text_entries = [e for e in entries if not e[4]]
    assert len(fields) == 2, "two found fields, the suspect and the absent one excluded"
    assert FORM_FIELDS_HEADER in block
    assert block.index(FORM_FIELDS_HEADER) > block.index(text_entries[-1][1]), "fields come after the source's own text"
    assert block.rstrip().endswith(source_carry.wrap_untrusted_source("x").split("x")[-1].strip()), "still inside the fence"
    last_text_n = int(text_entries[-1][0][1:])
    assert [e[0] for e in fields] == [f"S{last_text_n + 1}", f"S{last_text_n + 2}"], "ids continue after the text"
    assert fields[0][1] == "Insured name: SAMPLE, JOHN Q" and fields[0][3] == 1
    assert f"[{fields[0][0]}] Insured name: SAMPLE, JOHN Q" in block
    assert fields[1][4]["kind"] == "parsure_field"

    # The carry plan is the same walk: it counts the field sentences and reports them.
    plan = carry_plan([row])
    entry = plan["sources"][0]
    assert entry["parsure_fields"] == 2
    assert entry["sentences"] == len(entries) and entry["last_id"] == entries[-1][0]

    # A prose row carries no field block and reports zero.
    prose = numbered_source_blocks([_row()])[0]
    assert FORM_FIELDS_HEADER not in prose[0] and all(e[4] is None for e in prose[1])
    assert carry_plan([_row()])["sources"][0]["parsure_fields"] == 0

    # The sentence map carries the provenance on the field ids only.
    smap = build_sentence_map([row])
    assert smap[fields[0][0]]["provenance"]["field"] == "insured_name"
    assert "provenance" not in smap[text_entries[0][0]]


def test_attach_form_fields_marks_form_rows_from_the_report_and_leaves_prose_alone(monkeypatch):
    _patch_reports(monkeypatch, [REPORT])
    form, prose = _row(), {"id": "sub-prose", "filename": "policy.pdf", "extracted_text": "The policy liability limit is set at five million dollars per occurrence." + SOURCE_FILLER}
    assert looks_like_form([form["extracted_text"]]) and not looks_like_form([prose["extracted_text"]])
    rows = attach_form_fields("proj", [form, prose])
    assert rows[0]["parsure_form"] is True and rows[0]["parsure_report_id"] == "pr-form-1"
    assert [f["name"] for f in rows[0]["parsure_fields"]] == [f["name"] for f in FIELDS]
    assert "parsure_form" not in rows[1] and "parsure_fields" not in rows[1]

    # The report's flag alone marks a form whose text the heuristic does not catch.
    flagged = {"id": "sub-flag", "filename": "f.pdf", "extracted_text": prose["extracted_text"]}
    _patch_reports(monkeypatch, [{**REPORT, "document_id": "sub-flag", "report_id": "pr-flag"}])
    assert attach_form_fields("proj", [flagged])[0]["parsure_report_id"] == "pr-flag"

    # A form with no report: marked, but no fields — nothing is invented.
    _patch_reports(monkeypatch, [])
    bare = attach_form_fields("proj", [_row()])[0]
    assert bare.get("parsure_form") is True and "parsure_fields" not in bare
    assert FORM_FIELDS_HEADER not in numbered_source_blocks([bare])[0][0]

    # A lookup failure leaves the rows as they were.
    def boom(pid, **kw):
        raise RuntimeError("no table")

    monkeypatch.setattr("prompt_matrix.db.parsure_repository.list_reports", boom)
    assert "parsure_fields" not in attach_form_fields("proj", [_row()])[0]


# --------------------------------------------------------------------------- #
# Citations and the claim block
# --------------------------------------------------------------------------- #
def _tree(content: str) -> dict:
    return {
        "document_id": "doc-form", "meta": {}, "truth_ledger": {},
        "body": [{"type": "section", "id": "sec-1", "title": "Claim", "meta": {},
                  "children": [{"type": "paragraph", "id": "para-1", "content": content, "entities_referenced": [], "provenance": [], "meta": {}}]}],
    }


def test_a_citation_to_a_field_sentence_anchors_the_claim_to_the_field():
    row = _row(parsure_fields=FIELDS, parsure_report_id="pr-form-1")
    entries = numbered_source_blocks([row])[0][1]
    name_id, date_id = [e[0] for e in entries if e[4]]
    tree = attach_citations_to_tree(_tree(f"The insured is SAMPLE, JOHN Q. [{name_id}]"), [row])
    node = tree["body"][0]["children"][0]
    (prov,) = node["provenance"]
    assert prov["grounding_source"] == "parsure_field" and prov["field"] == "insured_name"
    assert prov["extracted_quote"] == "Insured name: SAMPLE, JOHN Q" and prov["field_quote"] == "SAMPLE, JOHN Q"
    assert prov["page"] == 1 and prov["element_id"] == "p1e1:aaaaaaaaaaaa" and prov["bbox"] == [0.1, 0.1, 0.5, 0.12]
    assert prov["parsure_report_id"] == "pr-form-1"
    assert node["content"] == "The insured is SAMPLE, JOHN Q."

    node["meta"] = {"provenance": {"entailment": {"verdict": "yes"}}}
    block = derive_claim(node, sources=[row], carry_plan=carry_plan([row]))
    assert block["verdict"] == "VERIFIED", block
    assert block["quote"] == "SAMPLE, JOHN Q" and block["quote_verbatim"] is True and block["page"] == 1
    assert block["grounding_source"] == "parsure_field" and block["field"] == "insured_name"
    assert block["element_id"] == "p1e1:aaaaaaaaaaaa"
    assert block["checks"]["source_quality"]["status"] == "ok"
    assert "from the field report (Parsure field 'insured_name', verbatim on page 1)" in block["checks"]["source_quality"]["basis"]

    # The field's page quote must still be verbatim in the source: a field whose
    # quote is not on the page anchors nothing and the claim is UNSUPPORTED.
    off_page = {**FIELDS[0], "raw": "NOBODY, NOWHERE", "grounding_quote": "NOBODY, NOWHERE"}
    row2 = _row(parsure_fields=[off_page], parsure_report_id="pr-form-1")
    (sid,) = [e[0] for e in numbered_source_blocks([row2])[0][1] if e[4]]
    tree2 = attach_citations_to_tree(_tree(f"The insured is NOBODY, NOWHERE. [{sid}]"), [row2])
    node2 = tree2["body"][0]["children"][0]
    node2["meta"] = {"provenance": {"entailment": {"verdict": "yes"}}}
    block2 = derive_claim(node2, sources=[row2], carry_plan=None)
    assert block2["verdict"] == "UNSUPPORTED" and block2["reason"] == "the cited text is not verbatim in the source"
    assert "grounding_source" not in block2


# --------------------------------------------------------------------------- #
# The compile: no longer refused, anchored to the fields
# --------------------------------------------------------------------------- #
def test_compile_of_a_filled_form_is_grounded_on_its_fields_instead_of_refused(monkeypatch):
    _patch_reports(monkeypatch, [REPORT])
    rows = [_row()]
    # The ids the walk will give the field sentences, computed by the same walk.
    entries = numbered_source_blocks(attach_form_fields("form-project", [copy.deepcopy(rows[0])]))[0][1]
    name_id, date_id = [e[0] for e in entries if e[4]]
    draft = f"The insured named on the claim form is SAMPLE, JOHN Q. [{name_id}]\n\nThe patient's birth date is 01 15 1980. [{date_id}]"

    def fake_stream(_gov, _messages, *, target_ai=None, cancel_check=None):
        yield 'event: token\ndata: {"type": "token", "delta": "The"}\n\n'
        yield (draft, 10, 5, "anthropic/claude-3-5-sonnet-20241022")

    shown: list[str] = []

    def capture_stream(gov, messages, **kw):
        shown.append("\n".join(str(m.get("content") or "") for m in messages))
        return fake_stream(gov, messages, **kw)

    monkeypatch.setattr("prompt_matrix.routers.draft._stream_model", capture_stream)
    monkeypatch.setattr("prompt_matrix.routers.draft.run_lock_inference", lambda _text: ([], "stub/model"))
    monkeypatch.setattr("prompt_matrix.routers.draft.check_entailment",
                        lambda *_a, **_k: {"verdict": "yes", "reasoning": "the field states it", "model": "stub", "checked_at": "x"})
    monkeypatch.setattr("prompt_matrix.routers.draft.fetch_substrate_entries_by_ids", lambda _pid, _ids: copy.deepcopy(rows))

    frames = list(run_draft_pipeline("form-project", intent="State the insured and the birth date.", substrate_file_ids=["sub-form"], governor=_FakeGovernor()))
    events = [_parse_sse(f) for f in frames if f.startswith("event:") or f.startswith("data:")]
    types = [d.get("type") for _ev, d in events if isinstance(d, dict)]
    assert "error" not in types, [d for _ev, d in events if d.get("type") == "error"]
    assert "compiled" in types and "verified" in types

    # The prompt carried the field block, inside the source's fence.
    assert shown and FORM_FIELDS_HEADER in shown[0] and f"[{name_id}] Insured name: SAMPLE, JOHN Q" in shown[0]
    assert "1980-01-15" not in shown[0], "the parsed value never reaches the model"

    verified = next(d for _ev, d in events if d.get("type") == "verified")
    compiled = next(d for _ev, d in events if d.get("type") == "compiled")
    assert verified["sources"]["sources"][0]["parsure_fields"] == 2
    assert compiled["sources"]["sources"][0]["parsure_fields"] == 2
    assert verified["provenance_stats"]["anchored"] == 2 and verified["provenance_stats"]["verified"] == 2

    claims = [n["meta"]["provenance"]["claim"] for s in verified["document"]["body"] for n in s.get("children") or [] if n.get("type") == "paragraph"]
    assert [c["verdict"] for c in claims] == ["VERIFIED", "VERIFIED"]
    assert [c["grounding_source"] for c in claims] == ["parsure_field", "parsure_field"]
    assert [c["quote"] for c in claims] == ["SAMPLE, JOHN Q", "01 15 1980"]
    assert [c["page"] for c in claims] == [1, 1]
    assert all("from the field report" in c["checks"]["source_quality"]["basis"] for c in claims)


def test_a_form_without_a_report_is_still_refused_as_a_form(monkeypatch):
    """No report, no fields: the guard's form refusal stands (nothing is invented)."""
    from prompt_matrix.services.compile_guard import FORM_SOURCE_MESSAGE

    _patch_reports(monkeypatch, [])
    draft = "The insured's name is captured on the claim form.\n\nThe patient's birth date is also captured."

    def fake_stream(_gov, _messages, *, target_ai=None, cancel_check=None):
        yield (draft, 10, 5, "anthropic/claude-3-5-sonnet-20241022")

    monkeypatch.setattr("prompt_matrix.routers.draft._stream_model", fake_stream)
    monkeypatch.setattr("prompt_matrix.routers.draft.run_lock_inference", lambda _text: ([], "stub/model"))
    monkeypatch.setattr("prompt_matrix.routers.draft.check_entailment", lambda *_a, **_k: {"verdict": "no", "reasoning": "captions", "model": "stub", "checked_at": "x"})
    monkeypatch.setattr("prompt_matrix.routers.draft.fetch_substrate_entries_by_ids", lambda _pid, _ids: [_row()])
    monkeypatch.setattr("prompt_matrix.db.parsure_repository.get_latest_report", lambda pid: None)
    frames = list(run_draft_pipeline("form-project", intent="Summarise the claim form.", substrate_file_ids=["sub-form"], governor=_FakeGovernor()))
    events = [_parse_sse(f) for f in frames if f.startswith("event:") or f.startswith("data:")]
    error = next(d for _ev, d in events if d.get("type") == "error")
    assert error["error"] == FORM_SOURCE_MESSAGE and error["form_source"] is True
