"""Plan V5 (2026-09-29) B1 / B2 / B3: a targeted-pass failure leaves the
critique's findings intact and is attributed to itself; the per-stage model
budget — at most one model call per stage per document — holds and every call
sits in the model-call ledger under its stage.

Why: until 2026-09-29 the hinted grounded pass ran inside the critique's
``try``; a timeout there replaced an already-complete ``report["redhat"]`` with
empty findings and the reason read "critique failed". A promoted page also made
two grounding calls (promotion, then the fill over the same schema), and the
targeted pass's row was booked as ``llm_grounding``.
"""

from __future__ import annotations

import json
from collections import Counter

from prompt_matrix.services import model_calls as mc
from tests.test_end_to_end_proof import DEBRIS_LINES, client  # noqa: F401  (fixture)
from tests.test_v1_orchestrator import RESULT, VERIFICATION, _tree_for, jdf_cli_bundle

UNCERTAIN_LINES = [
    "MARINE CARGO CERTIFICATE",
    "Certificate No: MC-2025-0042",
    "Assured: Harbor Freight Lines",
    "Vessel: MV Plymouth Star",
    "Voyage: Boston to Rotterdam",
    "Sum Insured: $1,250,000",
] + ["Conditions as per the open cover and the institute cargo clauses."] * 4


class StageCounter:
    """Answers nothing; records the ledger stage each call was made under."""

    def __init__(self, answers: dict[str, str] | None = None):
        self.calls: list[str] = []
        self.answers = answers or {}

    def __call__(self, prompt: str) -> str:
        stage = mc.current_context().get("stage") or "unknown"
        self.calls.append(stage)
        return self.answers.get(stage, "{}")


class FailOnHint:
    """The blanket pass answers nothing; the hinted (targeted) pass raises."""

    def __init__(self):
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if "(note:" in prompt:
            raise RuntimeError("provider timed out on the hinted pass")
        return "{}"


def _run(completion, lines=DEBRIS_LINES, filename="debris.pdf"):
    from prompt_matrix.services.v1_orchestrator import run_after_parse

    return run_after_parse("default", bundle=jdf_cli_bundle(lines), verification=VERIFICATION, filename=filename,
                           file_bytes=b"bytes-" + filename.encode(), result={}, job_id="job-b", intake=None,
                           completion=completion, tree=_tree_for(lines))


def test_b1_a_targeted_pass_failure_keeps_the_critique_findings_and_is_attributed_to_itself(client):  # noqa: F811
    model = FailOnHint()
    out = _run(model)
    r = out["report"]
    exe = r["execution"]
    assert len(model.prompts) == 2 and "(note:" in model.prompts[1]
    # The critique block is exactly the successful first pass: findings kept, status completed.
    assert exe["redhat_graph"]["status"] == "completed" and r["redhat"]["findings"]
    assert exe["redhat_graph"]["findings"] == len(r["redhat"]["findings"])
    assert not any("critique failed" in n for n in r["redhat"].get("notes") or [])
    # The targeted pass owns its failure.
    assert exe["redhat_targeted"]["status"] == "failed"
    assert exe["redhat_targeted"]["reason"].startswith("RuntimeError: provider timed out")
    # … and the report still saved.
    from prompt_matrix.history import get_db

    row = get_db().execute("SELECT report_json FROM parsure_reports WHERE report_id = ?", (out["report_id"],)).fetchone()
    stored = json.loads(row[0])
    assert stored["execution"]["redhat_targeted"]["status"] == "failed" and stored["redhat"]["findings"]


def test_b2_a_critique_failure_is_reported_as_the_critique_and_the_targeted_pass_is_skipped(client, monkeypatch):  # noqa: F811
    from prompt_matrix.services import redhat_graph as rg

    def _boom(*a, **k):
        raise ValueError("graph walker exploded")

    monkeypatch.setattr(rg, "critique_report", _boom)
    model = StageCounter()
    r = _run(model, filename="critique-fails.pdf")["report"]
    exe = r["execution"]
    assert exe["redhat_graph"]["status"] == "failed" and exe["redhat_graph"]["reason"].startswith("ValueError: graph walker exploded")
    assert r["redhat"]["notes"] == ["critique failed: ValueError"] and r["redhat"]["findings"] == []
    assert exe["redhat_targeted"] == {"status": "skipped", "fields": [], "reason": "the critique failed; nothing to target"}
    # No hinted call was made (the targeted pass never ran).
    assert "redhat_targeted" not in model.calls


def test_b3_at_most_one_model_call_per_stage_each_under_its_own_ledger_stage(client):  # noqa: F811
    # An uncertain page: the type suggestion (intake), discovery and the
    # grounded pass may each ask once; nothing asks twice.
    model = StageCounter()
    r = _run(model, lines=UNCERTAIN_LINES, filename="cargo.pdf")["report"]
    counts = Counter(model.calls)
    assert counts, "the model was never asked on an uncertain page"
    assert "unknown" not in counts, model.calls
    assert set(counts) <= {"intake", "discovery", "llm_grounding", "redhat_targeted", "redhat_graph", "vision"}, model.calls
    assert all(n <= 1 for n in counts.values()), model.calls
    assert sum(counts.values()) <= 5
    # The typed debris page: one grounding call, one targeted call — never two of either.
    model2 = StageCounter()
    r2 = _run(model2, filename="debris-budget.pdf")["report"]
    counts2 = Counter(model2.calls)
    assert counts2["llm_grounding"] == 1 and counts2["redhat_targeted"] == 1 and all(n <= 1 for n in counts2.values()), model2.calls
    assert r2["execution"]["redhat_targeted"]["status"] in ("ran", "failed")
    assert r2["execution"]["redhat_graph"].get("recheck") in (None, "rules_only_after_targeted_pass")


def test_b3_a_promoted_segment_reuses_its_grounded_pass_instead_of_a_second_call(client, monkeypatch):  # noqa: F811
    """A page whose labels fit no schema but whose values the grounded pass
    finds: the promotion's call is the grounding call; the extraction step
    takes its records and the ledger says so."""
    from prompt_matrix.services import v1_orchestrator as orch

    lines = ["HEALTH INSURANCE CLAIM FORM", "1a. INSURED'S I.D. NUMBER", "2. PATIENT'S NAME", "Martinez Gail D", "6543 7285-A",
             "33. BILLING PROVIDER", "Abbott Northwestern Hospital", "25. FEDERAL TAX I.D. 41-0693831", "28. TOTAL CHARGE $ 910 00"] + ["x"] * 3
    monkeypatch.setattr(orch.lx, "classify_with_model", lambda texts, completion=None, project_id=None: {"document_type": "medical_claim", "basis": "form layout", "model": "injected"})
    answers = {"llm_grounding": json.dumps({
        "patient_name": {"quote": "Martinez Gail D", "value": "Martinez Gail D", "page": 1},
        "member_id": {"quote": "6543 7285-A", "value": "6543 7285-A", "page": 1},
        "provider_name": {"quote": "Abbott Northwestern Hospital", "value": "Abbott Northwestern Hospital", "page": 1},
        "provider_tax_id": {"quote": "FEDERAL TAX I.D. 41-0693831", "value": "41-0693831", "page": 1},
    })}
    model = StageCounter(answers)
    r = _run(model, lines=lines, filename="hcfa.pdf")["report"]
    counts = Counter(model.calls)
    assert counts["llm_grounding"] == 1, model.calls  # promotion or fill — never both
    promo = r["execution"].get("grounded_promotion") or {}
    if promo.get("promoted"):
        assert "answered by the grounded promotion pass" in (r["execution"]["llm_grounding"].get("reason") or "")
    else:
        assert r["execution"]["llm_grounding"]["status"] == "ran"
    if r["classification"]["document_type"] == "medical_claim":
        assert any(f["name"] == "patient_name" and f["value"] == "Martinez Gail D" for f in r["fields"])
    assert all(n <= 1 for n in counts.values()), model.calls
