"""Tests for progressive draft stream pipeline."""

from __future__ import annotations

import json
import os

import pytest

from prompt_matrix.models.jdf import draft_text_to_sections, get_node_by_id
from prompt_matrix.routers.draft import (
    run_draft_pipeline,
    run_redhat_audit,
    run_redhat_pipeline,
    verify_locks,
)


@pytest.fixture(autouse=True)
def _disable_omp_cache_for_draft_tests(monkeypatch, request):
    if request.node.name == "test_run_draft_pipeline_omp_cache_hit":
        monkeypatch.setenv("PEM_OMP_CACHE", "1")
        return
    monkeypatch.setenv("PEM_OMP_CACHE", "0")


def test_draft_text_to_sections_headings():
    body = draft_text_to_sections(
        "## Revenue\n\nQ3 ARR reached $12M.\n\n## Growth\n\nYoY growth was 45%."
    )
    assert len(body) == 2
    assert body[0]["title"] == "Revenue"
    assert body[0]["children"][0]["content"].startswith("Q3 ARR")
    assert body[1]["title"] == "Growth"


def test_draft_text_to_sections_plain():
    body = draft_text_to_sections("Single paragraph draft.")
    assert len(body) == 1
    assert body[0]["children"][0]["content"] == "Single paragraph draft."


@pytest.mark.skipif(
    os.environ.get("CI") == "true",
    reason="Z3 intermittently segfaults on GitHub Actions Python 3.11",
)
def test_verify_locks_pass_requires_a_checked_metric():
    locks = [{"canonical_key": "Revenue", "value": 12_000_000, "metric": "ARR"}]
    result = verify_locks(locks, "Revenue: 12000000 this quarter.")
    assert result["status"] == "PASS"
    assert result["locks_verified"] == 1
    assert result["metrics_checked"] == 1


@pytest.mark.skipif(
    os.environ.get("CI") == "true",
    reason="Z3 intermittently segfaults on GitHub Actions Python 3.11",
)
def test_verify_locks_zero_metrics_is_skipped_not_pass():
    """A lock with no ``key: value`` metric in the draft checks nothing, so the
    gate must not report PASS over zero checks (it used to)."""
    locks = [{"canonical_key": "Revenue", "value": 12_000_000, "metric": "ARR"}]
    result = verify_locks(locks, "Revenue ARR is $12M this quarter.")
    assert result["status"] == "SKIPPED"
    assert result["metrics_checked"] == 0
    assert result["locks_verified"] == 1
    assert "key: value" in result["skip_reason"]


def test_run_draft_pipeline_progressive(monkeypatch):
    """Draft pipeline ends at "verified" (Math Check gate). Red-Hat is
    opt-in and never runs automatically — see test_run_redhat_pipeline.

    The compile is grounded: a draft is refused before the gate unless its
    opening is in the source and a paragraph anchors to it
    (``services/compile_guard``), so this fixture carries one source and a
    draft that quotes it."""
    source = "The policy liability limit is set at $5,000,000 for combined single limit."
    # The metric line is its own paragraph: the anchor matcher compares a whole
    # paragraph to a source sentence, so a second sentence in the same paragraph
    # dilutes the quote below the match threshold.
    draft = source + "\n\nRevenue=100."

    def fake_stream(_gov, _messages, *, target_ai=None, cancel_check=None):
        yield 'event: token\ndata: {"type": "token", "delta": "Revenue"}\n\n'
        yield (draft, 10, 5, "anthropic/claude-3-5-sonnet-20241022")

    def fake_locks(_text):
        return [
            {"canonical_key": "Revenue", "value": 100, "metric": "Revenue", "confidence": 0.9}
        ], "deepseek/deepseek-chat"

    def stub_check(_claim, _source, *, project_id=""):
        return {
            "verdict": "yes",
            "reasoning": "The source states it.",
            "model": "stub/model",
            "checked_at": "2026-09-18T00:00:00+00:00",
        }

    def fail_if_called_redhat(*_a, **_k):
        raise AssertionError("run_redhat_audit must not be called by run_draft_pipeline")

    monkeypatch.setattr("prompt_matrix.routers.draft._stream_model", fake_stream)
    monkeypatch.setattr("prompt_matrix.routers.draft.run_lock_inference", fake_locks)
    monkeypatch.setattr("prompt_matrix.routers.draft.run_redhat_audit", fail_if_called_redhat)
    monkeypatch.setattr("prompt_matrix.routers.draft.check_entailment", stub_check)
    monkeypatch.setattr(
        "prompt_matrix.routers.draft.fetch_substrate_entries_by_ids",
        lambda _pid, _ids: [{"id": "sub-1", "filename": "policy.pdf", "extracted_text": source}],
    )

    frames = list(
        run_draft_pipeline(
            "default",
            intent="Restate the revenue.",
            substrate_file_ids=["sub-1"],
            governor=_FakeGovernor(),
        )
    )
    events = [_parse_sse(f) for f in frames if f.startswith("event:") or f.startswith("data:")]
    types = [e[1].get("type") or e[0] for e in events if isinstance(e[1], dict)]

    compiled_idx = types.index("compiled")
    verified_idx = types.index("verified")
    assert compiled_idx < verified_idx, "compiled must arrive before verified"
    assert (
        "audit_complete" not in types
    ), "audit_complete is opt-in — not part of run_draft_pipeline"

    compiled = next(data for _ev, data in events if data.get("type") == "compiled")
    assert "document" in compiled
    assert compiled["node_count"] >= 1
    assert "locks" in compiled

    # The compile stream never runs the Red-Hat audit, so its stage frame must
    # say skipped — it used to claim "ran" with zero findings behind it.
    redhat = next(data for _ev, data in events if data.get("type") == "redhat")
    assert redhat["status"] == "skipped"
    assert redhat["findings_count"] == 0
    assert redhat["skip_reason"]

    # "verified" is the hybrid dock gate: Z3 has run, Red-Hat has not (and
    # will not, unless the user opts in via run_redhat_pipeline).
    verified = next(data for _ev, data in events if data.get("type") == "verified")
    assert verified["z3_status"] == "PASS"
    assert verified["redhat_count"] == 0
    assert verified["redhat_critiques"] == []
    # The paragraph quotes its source and the entailment check read it, so the
    # gate is earned: Z3 PASS over a verified claim. (A compile with no
    # entangled claim returns 'review' instead — see
    # test_run_draft_pipeline_verifies_anchored_claims.)
    assert verified["provenance_stats"]["anchored"] == 1
    assert verified["provenance_stats"]["supported"] == 1
    assert verified["gate_status"] == "pass"
    assert verified["ok"] is True
    assert "document" in verified

    assert any(f.strip() == "data: [DONE]" for f in frames)


def test_run_draft_pipeline_verifies_anchored_claims(monkeypatch):
    """The compile stream entailment-checks anchored paragraphs and the gate reads
    the verdict: a lexically anchored paragraph the check calls "partial" is no
    longer reported as a verified claim."""
    source = "The policy liability limit is set at $5,000,000 for combined single limit."
    claim = "The policy liability limit is set at $5,000,000 for combined single limit."

    def fake_stream(_gov, _messages, *, target_ai=None, cancel_check=None):
        yield (claim, 10, 5, "anthropic/claude-3-5-sonnet-20241022")

    def fake_locks(_text):
        return [], "deepseek/deepseek-chat"

    calls: list[tuple[str, str]] = []

    def stub_check(claim_text, source_text, *, project_id=""):
        calls.append((claim_text, source_text))
        return {
            "verdict": "partial",
            "reasoning": "The source states the limit but not the coverage period.",
            "model": "stub/model",
            "checked_at": "2026-09-18T00:00:00+00:00",
        }

    monkeypatch.setattr("prompt_matrix.routers.draft._stream_model", fake_stream)
    monkeypatch.setattr("prompt_matrix.routers.draft.run_lock_inference", fake_locks)
    monkeypatch.setattr("prompt_matrix.routers.draft.check_entailment", stub_check)
    monkeypatch.setattr(
        "prompt_matrix.routers.draft.fetch_substrate_entries_by_ids",
        lambda _pid, _ids: [{"id": "sub-1", "filename": "policy.pdf", "extracted_text": source}],
    )

    frames = list(
        run_draft_pipeline(
            "default",
            intent="Restate the liability limit.",
            substrate_file_ids=["sub-1"],
            governor=_FakeGovernor(),
        )
    )
    events = [_parse_sse(f) for f in frames if f.startswith("event:") or f.startswith("data:")]
    frames_by_type = [data for _ev, data in events if isinstance(data, dict)]

    assert [d for d in frames_by_type if d.get("stage") == "entailment"], "stage must be announced"
    # The source handed to the model is the anchored source sentence, not the claim.
    assert calls == [(claim, claim.rstrip("."))]

    verified = next(d for d in frames_by_type if d.get("type") == "verified")
    # Separated layers: the paragraph is anchored (it carries a matched source
    # sentence — `node["provenance"]` below) and the verdict on that anchor is
    # "partial", so anchored is 1 while supported is 0.
    assert verified["provenance_stats"] == {
        "eligible": 1,
        "anchored": 1,
        "supported": 0,
        "partial": 1,
        "unsupported": 0,
        "unanchored": 0,
        "unverified": 0,
    }
    assert verified["gate_status"] == "review"
    assert verified["ok"] is False
    assert "1 supported only in part" in verified["unverified_reason"]
    node = verified["document"]["body"][0]["children"][0]
    assert node["meta"]["provenance"]["entailment"]["verdict"] == "partial"
    assert node["provenance"], "the paragraph is still lexically anchored"


def test_run_draft_pipeline_omp_cache_hit(monkeypatch):
    """OMP compile cache should skip the LLM when a prior result exists."""

    def fail_stream(*_a, **_k):
        raise AssertionError("_stream_model must not run on cache hit")

    cached = {
        "draft_text": "Cached draft.",
        "document": {
            "document_id": "doc-default",
            "meta": {},
            "truth_ledger": {},
            "body": [
                {
                    "type": "section",
                    "id": "sec-1",
                    "title": "Cached",
                    "children": [
                        {"type": "paragraph", "id": "para-1", "content": "Cached draft."},
                    ],
                }
            ],
        },
        "locks": [],
        "verified": {
            "ok": True,
            "gate_status": "pass",
            "z3_status": "PASS",
            "z3_results": {"status": "PASS"},
            "redhat_count": 0,
            "redhat_critiques": [],
            "document": {
                "document_id": "doc-default",
                "meta": {},
                "truth_ledger": {},
                "body": [],
            },
        },
    }

    monkeypatch.setattr("prompt_matrix.routers.draft._stream_model", fail_stream)
    wrapped = {
        "compiled": {
            "document": cached["document"],
            "nodes": cached["document"]["body"],
            "locks": cached["locks"],
            "node_count": 1,
            "lock_count": 0,
            "draft_text": cached["draft_text"],
        },
        "verified": cached["verified"],
    }
    monkeypatch.setattr("prompt_matrix.routers.draft.load_ast_cache", lambda _key: wrapped)
    remember_calls: list[tuple] = []
    monkeypatch.setattr(
        "prompt_matrix.routers.draft.save_ast_cache",
        lambda *args, **kwargs: remember_calls.append((args, kwargs)),
    )

    frames = list(
        run_draft_pipeline(
            "default",
            intent="Same intent as before.",
            governor=_FakeGovernor(),
        )
    )
    events = [_parse_sse(f) for f in frames if f.startswith("event:") or f.startswith("data:")]
    types = [e[1].get("type") or e[0] for e in events if isinstance(e[1], dict)]
    assert "compiled" in types
    assert "verified" in types
    compiled = next(data for _ev, data in events if data.get("type") == "compiled")
    assert compiled.get("cache_hit") is True
    assert remember_calls == []


def test_run_redhat_pipeline_opt_in(monkeypatch):
    """Opt-in Stage 4: only runs when explicitly invoked, over an
    already-verified document, and attaches findings without recomputing
    Z3."""

    def fake_redhat(*_a, **_k):
        return [
            {
                "title": "Red-hat review",
                "content": "Looks good.",
                "model": "deepseek/deepseek-reasoner",
            }
        ], {
            "input_tokens": 50,
            "output_tokens": 20,
            "model_id": "deepseek/deepseek-reasoner",
            "task_type": "redhat",
        }

    monkeypatch.setattr("prompt_matrix.routers.draft.run_redhat_audit", fake_redhat)

    verified_document = {
        "document_id": "doc-default",
        "meta": {},
        "truth_ledger": {"Revenue": 100},
        "body": [
            {
                "type": "section",
                "id": "sec-1",
                "title": "Draft",
                "children": [
                    {
                        "type": "paragraph",
                        "id": "para-1",
                        "content": "Hello world with Revenue=100",
                    }
                ],
            }
        ],
    }

    frames = list(
        run_redhat_pipeline(
            "default",
            draft_text="Hello world with Revenue=100",
            document=verified_document,
            z3_results={"status": "PASS", "violations": [], "lock_results": []},
            governor=_FakeGovernor(),
        )
    )
    events = [_parse_sse(f) for f in frames if f.startswith("event:") or f.startswith("data:")]

    audit = next(data for _ev, data in events if data.get("type") == "audit_complete")
    assert audit["z3_status"] == "PASS"
    assert audit["redhat_count"] == 1
    assert audit["gate_status"] == "review"
    assert audit["redhat_critiques"]
    assert audit["document"]["body"][0]["children"][0]["annotations"]["redhat"]

    assert any(f.strip() == "data: [DONE]" for f in frames)


def test_run_redhat_pipeline_survives_model_error(monkeypatch):
    """Red-Hat failures must still emit audit_complete so the UI can recover."""

    def boom(*_a, **_k):
        raise RuntimeError("API timeout")

    monkeypatch.setattr("prompt_matrix.routers.draft.run_redhat_audit", boom)

    doc = {
        "document_id": "doc-default",
        "meta": {},
        "truth_ledger": {},
        "body": [
            {
                "type": "section",
                "id": "sec-1",
                "title": "Draft",
                "children": [
                    {"type": "paragraph", "id": "para-1", "content": "Revenue was $4.2M."},
                ],
            }
        ],
    }

    frames = list(
        run_redhat_pipeline(
            "default",
            draft_text="Revenue was $4.2M.",
            document=doc,
            governor=_FakeGovernor(),
        )
    )
    events = [_parse_sse(f) for f in frames if f.startswith("event:") or f.startswith("data:")]
    audit = next(data for _ev, data in events if data.get("type") == "audit_complete")
    assert audit["redhat_count"] == 1
    assert "Audit failed" in audit["redhat_critiques"][0]["content"]


def test_run_redhat_pipeline_target_node_id(monkeypatch):
    """On-demand surgical-canvas audit: scoped to one node via
    target_node_id, so the finding must attach to that node, not the
    first node in document order."""

    def fake_redhat(*_a, **_k):
        return [
            {
                "title": "Red-hat review",
                "content": "Unsupported claim.",
                "model": "deepseek/deepseek-reasoner",
            }
        ], {
            "input_tokens": 10,
            "output_tokens": 5,
            "model_id": "deepseek/deepseek-reasoner",
            "task_type": "redhat",
        }

    monkeypatch.setattr("prompt_matrix.routers.draft.run_redhat_audit", fake_redhat)

    doc = {
        "document_id": "doc-default",
        "meta": {},
        "truth_ledger": {},
        "body": [
            {
                "type": "section",
                "id": "sec-1",
                "title": "Draft",
                "children": [
                    {"type": "paragraph", "id": "para-1", "content": "First paragraph."},
                    {"type": "paragraph", "id": "para-2", "content": "Second paragraph."},
                ],
            }
        ],
    }

    frames = list(
        run_redhat_pipeline(
            "default",
            draft_text="Second paragraph.",
            document=doc,
            target_node_id="para-2",
            governor=_FakeGovernor(),
        )
    )
    events = [_parse_sse(f) for f in frames if f.startswith("event:") or f.startswith("data:")]
    audit = next(data for _ev, data in events if data.get("type") == "audit_complete")

    children = audit["document"]["body"][0]["children"]
    para1 = next(c for c in children if c["id"] == "para-1")
    para2 = next(c for c in children if c["id"] == "para-2")
    assert not (para1.get("annotations") or {}).get("redhat")
    assert (para2.get("annotations") or {}).get("redhat")


# --------------------------------------------------------------------------- #
# Source-aware Red-Hat: the node's provenance quote reaches the prompt, and
# only the node that actually has one is told to check a source.
# --------------------------------------------------------------------------- #
_SOURCE_CLAUSE = (
    "Section 4.2: The liability limit is $5,000,000 on a combined single limit basis."
)
_UNANCHORED_CLAUSE = "Section 9.1: Either party may terminate on thirty days' notice."


class _CapturingGovernor:
    """Records the prompt ``run_redhat_audit`` sends, without calling a model."""

    def __init__(self):
        self.prompts: list[str] = []

    def execute_with_retry_budget(self, _project_id, _task, messages, **_kwargs):
        self.prompts.append(messages[0]["content"])

        class R:
            text = "Review."
            input_tokens = 1
            output_tokens = 1
            model_id = "deepseek/deepseek-reasoner"

        return R()


def _sourced_document() -> dict:
    """para-a is anchored to a substrate clause; para-b carries no provenance."""
    return {
        "document_id": "doc-source-aware",
        "meta": {},
        "truth_ledger": {},
        "body": [
            {
                "type": "section",
                "id": "sec-1",
                "title": "Draft",
                "children": [
                    {
                        "type": "paragraph",
                        "id": "para-a",
                        "content": _SOURCE_CLAUSE,
                        "provenance": [
                            {
                                "source_type": "internal_doc",
                                "source_name": "msa.pdf",
                                "source_id": "row-1",
                                "page_number": "3",
                                "extracted_quote": _SOURCE_CLAUSE,
                            }
                        ],
                    },
                    {
                        "type": "paragraph",
                        "id": "para-b",
                        "content": _UNANCHORED_CLAUSE,
                        "provenance": [],
                    },
                ],
            }
        ],
    }


def test_redhat_node_prompt_hands_the_source_to_the_anchored_paragraph_only():
    """Discrimination: the paragraph whose provenance row carries an
    ``extracted_quote`` is prompted with that source sentence and asked to check
    the claim against it; the unsourced paragraph is told no source is attached
    and is never shown the other node's quote."""
    doc = _sourced_document()
    anchored = _CapturingGovernor()
    unanchored = _CapturingGovernor()

    list(
        run_redhat_pipeline(
            "default",
            draft_text=_SOURCE_CLAUSE,
            document=doc,
            target_node_id="para-a",
            governor=anchored,
        )
    )
    list(
        run_redhat_pipeline(
            "default",
            draft_text=_UNANCHORED_CLAUSE,
            document=doc,
            target_node_id="para-b",
            governor=unanchored,
        )
    )

    prompt_a = anchored.prompts[0]
    prompt_b = unanchored.prompts[0]

    assert _SOURCE_CLAUSE in prompt_a
    assert "Check the claim against that source sentence" in prompt_a
    assert "msa.pdf p.3" in prompt_a
    assert "risk review of this document" not in prompt_a

    assert _SOURCE_CLAUSE not in prompt_b
    assert "No source sentence is attached to this claim" in prompt_b
    assert 'do not report "no source" as a finding' in prompt_b
    assert "risk review of this document" not in prompt_b


def test_redhat_node_prompt_calls_a_missing_node_unsourced():
    """A node-scoped audit whose id is not in the document takes the
    no-source variant — never the whole-document one, which would describe a
    single claim as the document."""
    gov = _CapturingGovernor()
    run_redhat_audit(
        "p1",
        "Orphan claim.",
        gov=gov,
        target_node_id="para-gone",
        document=_sourced_document(),
    )

    prompt = gov.prompts[0]
    assert "No source sentence is attached to this claim" in prompt
    assert "risk review of this document" not in prompt


def test_redhat_whole_document_prompt_is_a_risk_review():
    """No source is supplied for a whole-document run, so the prompt must not
    ask for a grounding verdict ("unsupported claims" / "missing citations")."""
    gov = _CapturingGovernor()
    run_redhat_audit("p1", "Whole draft body.", gov=gov)

    prompt = gov.prompts[0]
    assert "risk review of this document" in prompt
    assert "unsupported claims" not in prompt
    assert "missing citations" not in prompt


def test_get_node_by_id_resolves_a_nested_paragraph():
    node = get_node_by_id(_sourced_document(), "para-b")
    assert node is not None
    assert node["content"] == _UNANCHORED_CLAUSE


def test_get_node_by_id_resolves_a_top_level_section():
    node = get_node_by_id(_sourced_document(), "sec-1")
    assert node is not None
    assert node["title"] == "Draft"


def test_get_node_by_id_returns_none_for_an_unknown_id():
    assert get_node_by_id(_sourced_document(), "para-gone") is None


def _parse_sse(frame: str) -> tuple[str, dict]:
    if frame.strip() == "data: [DONE]":
        return "done", {"type": "done"}
    event = "message"
    data = ""
    for line in frame.strip().split("\n"):
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data = line[5:].strip()
    if data == "[DONE]":
        return "done", {"type": "done"}
    return event, json.loads(data)


class _FakeGovernor:
    class _Acct:
        def count_messages(self, _):
            return 10

        def count(self, text):
            return max(1, len(text) // 4)

    accountant = _Acct()

    def preflight(self, *_a, **_k):
        return None

    def policy_for(self, _task):
        class P:
            litellm_model = "anthropic/claude-3-5-sonnet-20241022"
            max_output_tokens = 256
            model_id = "anthropic/claude-3-5-sonnet-20241022"

        return P()

    def record_usage(self, *_a, **_k):
        return None

    def execute_with_retry_budget(self, *_a, **_k):
        class R:
            text = "critique"
            input_tokens = 10
            output_tokens = 5
            model_id = "deepseek/deepseek-reasoner"

        return R()


@pytest.fixture
def client():
    from prompt_matrix.web import create_app

    app = create_app(require_auth=False)
    app.config["TESTING"] = True
    return app.test_client()


def test_list_projects(client):
    res = client.get("/api/projects")
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert any(p["id"] == "default" for p in data["projects"])


def test_draft_stream_requires_intent(client):
    res = client.post("/api/projects/default/draft/stream", json={"intent": ""})
    assert res.status_code == 400


def test_draft_payload_selection_compile_type():
    from prompt_matrix.routers.draft import DraftPayload

    payload = DraftPayload.model_validate(
        {
            "intent": "",
            "compileType": "selection",
            "content": "Revenue grew 12% year over year.",
        }
    )
    assert payload.compile_type == "selection"
    assert payload.content.startswith("Revenue")


def test_draft_stream_selection_requires_content(client):
    res = client.post(
        "/api/projects/default/draft/stream",
        json={"intent": "", "compileType": "selection", "content": ""},
    )
    assert res.status_code == 400


def test_draft_redhat_stream_requires_draft_text(client):
    res = client.post(
        "/api/projects/default/draft/redhat/stream",
        json={"draft_text": "", "document": {}},
    )
    assert res.status_code == 400
