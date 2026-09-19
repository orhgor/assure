"""Tier 2 of the Math Check: translation contract, Z3 verdicts, and the tiering.

Two groups, on purpose.

The first needs no Z3 and no network, so it runs in CI: the claim schema the
model's answer must satisfy, the fact lookup the verdict rests on, the sentence
split, and the cache key's coverage.

The second drives Z3, and carries the same ``skipif(CI)`` the two existing
Math Check contract tests carry in ``test_draft.py`` — Z3 intermittently
segfaults on GitHub Actions' Python 3.11 build, which is why those two are
skipped there as well. Skipping is not the same as protecting: the tiering
contract (a check that did not happen is never a pass) is skipped in CI today,
and the honest fix is a CI Python/Z3 pairing that does not crash, not a
different assertion. See the report note on ``test_draft.py``'s skipif marker.
"""

from __future__ import annotations

import json
import os

import pytest

from prompt_matrix.routers import draft as draft_module
from prompt_matrix.routers.draft import verify_locks
from prompt_matrix.services import relational_translate as rt
from prompt_matrix.services import relational_z3 as rz

z3_skip = pytest.mark.skipif(
    os.environ.get("CI") == "true",
    reason="Z3 intermittently segfaults on GitHub Actions Python 3.11",
)

LOCKS = [
    {"canonical_key": "Revenue", "metric": "ARR", "value": 12_000_000},
    {"canonical_key": "Churn Rate", "metric": "churn", "value": 0.04},
]
FACTS = rz.facts_from_locks(LOCKS)


# --- the translation contract (no Z3, no network) ------------------------------


def test_facts_from_locks_registers_every_name_a_lock_carries():
    """Both the canonical key and the alias resolve, because a claim may use either."""
    assert FACTS[rz.normalize_name("Revenue")] == 12_000_000
    assert FACTS[rz.normalize_name("ARR")] == 12_000_000
    assert FACTS[rz.normalize_name("Churn Rate")] == 0.04
    # An unusable lock is skipped, not carried as a fact of zero.
    assert "broken" not in rz.facts_from_locks([{"canonical_key": "Broken", "value": "n/a"}])


#: Two policies of the same kind on one project, as the extraction writes them: the
#: canonical key carries the entity, and the short metric name is registered beside
#: it. Real values from project walk-underwriting-2026-09-19-68e596's ledger, whose
#: two `minimum earned premium` locks read 25 (Hallmark) and 35 (Texas).
TWO_POLICIES = [
    {
        "canonical_key": "Hallmark_Specialty_Insurance_Company_minimum_earned_premium",
        "metric": "minimum earned premium",
        "entity": "Hallmark Specialty Insurance Company",
        "value": 25.0,
    },
    {
        "canonical_key": "Texas_Insurance_Company_minimum_earned_premium",
        "metric": "minimum earned premium",
        "entity": "Texas Insurance Company",
        "value": 35.0,
    },
]


def test_a_name_two_locks_disagree_about_is_not_a_fact():
    """A name two sources carry is a name no verdict can rest on."""
    facts = rz.facts_from_locks(TWO_POLICIES)
    assert "minimum_earned_premium" not in facts
    assert facts[rz.normalize_name("Texas_Insurance_Company_minimum_earned_premium")] == 35.0
    assert (
        facts[rz.normalize_name("Hallmark_Specialty_Insurance_Company_minimum_earned_premium")]
        == 25.0
    )
    assert rz.ambiguous_fact_names(TWO_POLICIES)["minimum_earned_premium"] == [
        "Hallmark_Specialty_Insurance_Company_minimum_earned_premium",
        "Texas_Insurance_Company_minimum_earned_premium",
    ]

    # An alias every lock agrees about is still a fact, and still resolves.
    agreed = [
        {"canonical_key": "Policy_A_churn", "metric": "churn", "value": 0.04},
        {"canonical_key": "Policy_B_churn_rate", "metric": "churn", "value": 0.04},
    ]
    assert rz.facts_from_locks(agreed)["churn"] == 0.04
    assert rz.ambiguous_fact_names(agreed) == {}


def test_parse_claim_rejects_what_a_verdict_could_not_rest_on():
    """A model answer is data: anything the check cannot use is a failed translation."""
    good = (
        '{"metric": "ARR", "operands": [{"name": "ARR", "value": 12000000, '
        '"unit": "USD", "source_sentence": "ARR is $12M."}], "relation": "eq", '
        '"expected": 12000000}'
    )
    parsed = rt.parse_claim(f"```json\n{good}\n```")
    assert parsed is not None
    assert parsed["relation"] == "eq"
    assert parsed["operands"][0]["value"] == 12_000_000.0

    assert rt.parse_claim("") is None
    assert rt.parse_claim("no json here") is None
    for broken in (
        good.replace('"eq"', '"approximately"'),  # relation outside the schema
        good.replace("12000000", '"twelve million"', 1),  # non-numeric operand
        good.replace('"expected": 12000000', '"expected": "twelve million"'),
    ):
        assert rt.parse_claim(broken) is None


def test_split_claims_takes_numeral_sentences_in_order():
    text = "Churn stayed under 5%. No numbers here. Q3 2024. ARR reached $12M in Q3."
    claims = rz.split_claims(text)
    assert claims[0] == "Churn stayed under 5%."
    assert claims[-1] == "ARR reached $12M in Q3."
    assert "No numbers here." not in claims


def test_states_a_range_finds_bounds_and_leaves_single_values_alone():
    """A range needs two bounds; Tier 2 compares one value to one locked value."""
    assert rz.states_a_range("time falls between 38 and 52 minutes")
    assert rz.states_a_range("a $40,000 to $50,000 range")
    assert rz.states_a_range("losses of 38-52 minutes")
    # A threshold is not a range, however many figures sit near it.
    assert not rz.states_a_range("used only when the time is 53 minutes or longer")
    assert not rz.states_a_range("the standard 45-minute session format")
    assert not rz.states_a_range("lasts 53 minutes, 55 minutes, or 70 minutes")


def test_cache_key_covers_everything_the_translation_depends_on(monkeypatch):
    """A key that missed one of these would serve a verdict from a different question."""
    claim = "ARR reached $12M."
    base = rt.cache_key(claim, FACTS, "model-a")
    assert base == rt.cache_key(claim, dict(FACTS), "model-a")  # same question, same key
    assert base != rt.cache_key("ARR reached $14M.", FACTS, "model-a")  # claim
    assert base != rt.cache_key(claim, {**FACTS, "arr": 13_000_000}, "model-a")  # facts
    assert base != rt.cache_key(claim, FACTS, "model-b")  # translator model

    monkeypatch.setattr(rt, "z3_version", lambda: "9.9.9")
    assert base != rt.cache_key(claim, FACTS, "model-a")  # the solver that will decide it


# --- the verdicts ----------------------------------------------------------------


@z3_skip
def test_a_claim_whose_figure_contradicts_the_lock_is_violated():
    """The failure this path exists for: same metric, different number.

    The relation here holds on the draft's own numbers (7% is at least 5%), so
    nothing about the encoding is in doubt — what disagrees is the figure itself
    against the locked 4%. A check that only asked "does the relation hold for the
    locked value" would call the draft's 7% fine; it is the value comparison that
    catches it, and this is the case that made the value query necessary.
    """
    verdict = rz.check_relation(
        {
            "metric": "Churn Rate",
            "operands": [{"name": "churn", "value": 0.07, "unit": "%"}],
            "relation": "ge",
            "expected": 0.05,
        },
        FACTS,
    )
    assert verdict["verdict"] == rz.VIOLATED
    assert verdict["counterexample"]["source"] == 0.04
    assert verdict["counterexample"]["claimed"] == 0.07


@z3_skip
def test_a_translation_that_contradicts_itself_is_not_decided():
    """A garbled encoding must not become a verdict about the draft.

    ``{lt, value 0.04, expected 0.02}`` cannot express "churn was 0.04, under 2%"
    faithfully: its own numbers fail its own relation. The measured cases that
    produced this guard are real — "gravitational forces exceeding 9G" came back
    as value 9, expected 9, relation ``gt``, and the locked 9 then made a sentence
    the source agrees with read as a violation. Reported unchecked instead.
    """
    verdict = rz.check_relation(
        {
            "metric": "Churn Rate",
            "operands": [{"name": "churn", "value": 0.04, "unit": "%"}],
            "relation": "lt",
            "expected": 0.02,
        },
        FACTS,
    )
    assert verdict["verdict"] == rz.UNKNOWN
    assert "not self-consistent" in verdict["reason"]

    # And through the tiers: unchecked, never that-passed-word, never blocking.
    result = verify_locks(
        LOCKS,
        "Churn stayed under 2%, at 0.07 in the period.",
        translate=_translator(
            {
                "metric": "Churn Rate",
                "operands": [{"name": "churn", "value": 0.07, "unit": "%"}],
                "relation": "lt",
                "expected": 0.02,
            }
        ),
    )
    assert result["violated"] == 0
    assert result["violations"] == []
    assert result["unverified"] == 1


#: The renewal memo's sentence and the cap the source locks, as measured on project
#: walk-underwriting-2026-09-19-68e596: the translator wrote 100000000 for the
#: draft's "100 billion" and the Math Check listed a violated cap the draft states
#: correctly.
TRIA_SENTENCE = (
    "The prior policy’s terrorism coverage was provided under the Terrorism Risk "
    "Insurance Act (TRIA) and was subject to a $100 billion cap on U."
)
TRIA_FACTS = {"tria_cap": 100_000_000_000.0}


def _tria_claim(value: float, *, sentence: str = TRIA_SENTENCE) -> dict:
    """The translation the model actually produced, with the value it wrote."""
    return {
        "metric": "tria_cap",
        "operands": [
            {"name": "tria_cap", "value": value, "unit": "USD", "source_sentence": sentence}
        ],
        "relation": "eq",
        "expected": value,
    }


@z3_skip
def test_a_transcription_that_lost_the_sentence_magnitude_is_not_a_violation():
    """A dropped magnitude word is the translation's error, not a finding about the draft.

    Measured on the renewal memo: "subject to a $100 billion cap" came back as
    100000000 against a locked 100000000000, the value query reported it, and the
    product listed a violated cap the draft states correctly. A wrong violation is
    worse than no check, so the claim is left unchecked with the reason — with both
    figures still in the record, since the tab shows them side by side.
    """
    verdict = rz.check_relation(_tria_claim(100_000_000), TRIA_FACTS)
    assert verdict["verdict"] == rz.UNKNOWN
    assert "lost the sentence's magnitude" in verdict["reason"]
    assert "100000000000" in verdict["reason"]
    assert verdict["counterexample"]["claimed"] == 100_000_000
    assert verdict["counterexample"]["source"] == 100_000_000_000

    # The same sentence transcribed with its magnitude is decided as before.
    assert rz.check_relation(_tria_claim(100_000_000_000), TRIA_FACTS)["verdict"] == rz.VERIFIED

    # And through the tier: unchecked, not a violation, not a pass.
    result = verify_locks(
        [{"canonical_key": "tria_cap", "value": 100_000_000_000}],
        "The cap is $100 billion.",
        translate=_translator(_tria_claim(100_000_000)),
    )
    assert result["violated"] == 0
    assert result["violations"] == []
    assert result["verified"] == 0
    assert result["unverified"] == 1
    assert result["status"] == "SKIPPED"


@z3_skip
def test_a_scale_the_sentence_itself_states_is_still_a_violation():
    """The guard reads the quoted sentence, not the source.

    A draft that really says "$100 million" where the source locks 100 billion is
    the disagreement this path exists for, and the sentence carries its own
    magnitude, so it still reaches the queries. Same for a sentence with no
    magnitude word at all.
    """
    sentence = TRIA_SENTENCE.replace("$100 billion", "$100 million")
    verdict = rz.check_relation(_tria_claim(100_000_000, sentence=sentence), TRIA_FACTS)
    assert verdict["verdict"] == rz.VIOLATED
    assert verdict["counterexample"]["claimed"] == 100_000_000

    plain = {
        "metric": "deductible",
        "operands": [
            {
                "name": "deductible",
                "value": 5_000_000,
                "unit": "USD",
                "source_sentence": "The deductible is $5,000,000 per occurrence.",
            }
        ],
        "relation": "eq",
        "expected": 5_000_000,
    }
    assert rz.check_relation(plain, {"deductible": 7_000_000.0})["verdict"] == rz.VIOLATED
    assert rz.check_relation(plain, {"deductible": 5_000_000.0})["verdict"] == rz.VERIFIED


@z3_skip
def test_a_metric_two_sources_carry_is_decided_against_the_source_the_claim_names():
    """The claim's own source decides it, never the extraction order.

    Measured on the renewal memo: a claim about the prior policy's 25% minimum
    earned premium was decided against the renewal policy's locked 35% — "draft
    claims 25 %, locked source value is 35 %" — because the shared `minimum earned
    premium` alias resolved to whichever lock the extraction wrote first. The same
    claim with the locks in the other order was VERIFIED. A verdict that turns on
    the order of a list is not a finding about the draft.
    """
    facts = rz.facts_from_locks(TWO_POLICIES)
    ambiguous = rz.ambiguous_fact_names(TWO_POLICIES)

    named = {
        "metric": "minimum earned premium",
        "operands": [
            {
                "name": "Texas_Insurance_Company_minimum_earned_premium",
                "value": 35.0,
                "unit": "%",
                "source_sentence": (
                    "The policy issued by Texas Insurance Company has a minimum earned "
                    "premium of 35.00%."
                ),
            }
        ],
        "relation": "eq",
        "expected": 35.0,
    }
    # The claim naming its own source resolves to that source's figure, either way
    # the extraction happens to order its locks.
    assert rz.check_relation(named, facts, ambiguous=ambiguous)["verdict"] == rz.VERIFIED

    alias = json.loads(json.dumps(named))
    alias["operands"][0]["name"] = "minimum earned premium"
    alias["operands"][0]["value"] = 25.0
    alias["expected"] = 25.0
    alias["operands"][0]["source_sentence"] = (
        "The prior policy’s minimum earned premium is 25.00% of the total premium."
    )
    verdict = rz.check_relation(alias, facts, ambiguous=ambiguous)
    assert verdict["verdict"] == rz.UNKNOWN
    assert "names 2 locked metrics with different values" in verdict["reason"]
    assert "Texas_Insurance_Company_minimum_earned_premium" in verdict["reason"]
    assert verdict["counterexample"] is None

    # Through the tier: unchecked, not a violation, and the reader is told why.
    result = verify_locks(
        [
            {
                "canonical_key": "Hallmark_Specialty_Insurance_Company_minimum_earned_premium",
                "metric": "minimum earned premium",
                "value": 25.0,
            },
            {
                "canonical_key": "Texas_Insurance_Company_minimum_earned_premium",
                "metric": "minimum earned premium",
                "value": 35.0,
            },
        ],
        "This is a change from the prior policy, which had a minimum earned premium of 25.00%.",
        translate=_translator(alias),
    )
    assert result["violated"] == 0
    assert result["violations"] == []
    assert result["unverified"] == 1


@z3_skip
def test_a_claim_with_no_locked_fact_is_unknown_never_verified():
    """No fact means no check, and no check is never a pass."""
    verdict = rz.check_relation(
        {
            "metric": "EBITDA",
            "operands": [{"name": "EBITDA", "value": 5, "unit": "USD"}],
            "relation": "eq",
            "expected": 5,
        },
        FACTS,
    )
    assert verdict["verdict"] == rz.UNKNOWN
    assert "no locked source value" in verdict["reason"]
    assert verdict["counterexample"] is None


@z3_skip
def test_agreement_is_verified_with_both_numbers_recorded():
    verdict = rz.check_relation(
        {
            "metric": "ARR",
            "operands": [{"name": "ARR", "value": 12_000_000, "unit": "USD"}],
            "relation": "ge",
            "expected": 10_000_000,
        },
        FACTS,
    )
    assert verdict["verdict"] == rz.VERIFIED
    assert verdict["counterexample"]["source"] == 12_000_000


# --- the tiers ------------------------------------------------------------------


def _translator(claim_json: dict | None, *, fail: bool = False):
    """A stand-in for the model, so the tiering is tested without a call."""

    def translate(claim: str, facts: dict) -> dict:
        if fail or claim_json is None:
            return {
                "ok": False,
                "claim": None,
                "model": "stub/model",
                "cached": False,
                "json_sha256": "",
                "raw": "",
                "reason": "the model returned no usable JSON relation",
            }
        return {
            "ok": True,
            "claim": claim_json,
            "model": "stub/model",
            "cached": False,
            "json_sha256": "stub",
            "raw": "",
            "reason": "",
        }

    return translate


def _claim(value: float, *, name: str = "ARR", metric: str = "ARR", relation: str = "eq"):
    return {
        "metric": metric,
        "operands": [{"name": name, "value": value, "unit": "USD"}],
        "relation": relation,
        "expected": value,
    }


@z3_skip
def test_a_range_claim_is_left_unchecked_rather_than_decided_wrongly():
    """The false positive this guard exists for, reproduced as a contract.

    "falls between 38 and 52 minutes" gets translated as the ledger's range key
    with the lower bound as its value; the locked upper bound then refutes a
    sentence the source agrees with. A range is Tier 3, so the claim must come
    back unchecked — and unchecked must not mean passed.
    """
    draft = "The time falls between 38 and 52 minutes."
    mis_encoded = _claim(38, name="Churn Rate", metric="Churn Rate", relation="ge")
    result = verify_locks(LOCKS, draft, translate=_translator(mis_encoded))
    assert result["status"] == "SKIPPED"
    assert result["violated"] == 0
    assert result["violations"] == []
    assert result["metrics_checked"] == 0
    assert result["unverified"] == 1
    assert "range" in result["unverified_reason"]
    assert result["checked_by_relational"] == 0


@z3_skip
def test_claims_past_the_cap_are_counted_and_listed_as_unchecked():
    """The cap bounds cost, not honesty: a claim it skips is visible with a reason."""
    draft = " ".join(f"Revenue ARR is ${12 + n}M in year {n}." for n in range(10))
    result = verify_locks(LOCKS, draft, translate=_translator(_claim(12_000_000)))
    checked = len([r for r in result["claim_results"] if r.get("tier") == "relational"])
    assert checked == draft_module._MAX_RELATIONAL_CLAIMS
    skipped = [r for r in result["claim_results"] if "cap" in str(r.get("reason") or "")]
    assert len(skipped) == len(result["claim_results"]) - checked
    assert result["unverified"] == len(skipped)
    assert len(result["claim_results"]) == len(rz.split_claims(draft))


@z3_skip
def test_tier2_checks_a_prose_claim_tier1_cannot_see():
    """The gap itself: no ``key: value`` label, and the number is still checked."""
    draft = "Revenue ARR is $12M this quarter."
    assert verify_locks(LOCKS, draft)["metrics_checked"] == 0  # Tier 1 alone: nothing

    result = verify_locks(LOCKS, draft, translate=_translator(_claim(12_000_000)))
    assert result["metrics_checked"] == 1
    assert result["checked_by_relational"] == 1
    assert result["checked_by_value"] == 0
    assert result["verified"] == 1
    assert result["status"] == "PASS"


@z3_skip
def test_tier2_violation_blocks_and_carries_the_counterexample():
    result = verify_locks(
        LOCKS, "Revenue ARR is $14M this quarter.", translate=_translator(_claim(14_000_000))
    )
    assert result["status"] == "VIOLATION"
    assert result["violated"] == 1
    assert result["verified"] == 0
    assert any("Metric 'ARR'" in str(v) for v in result["violations"])
    finding = result["claim_results"][0]
    assert finding["counterexample"]["source"] == 12_000_000
    assert finding["counterexample"]["claimed"] == 14_000_000


@z3_skip
def test_an_untranslatable_claim_is_unverified_not_passed():
    """Zero checks stays SKIPPED, and says which claim could not be checked."""
    result = verify_locks(
        LOCKS, "Revenue ARR is $12M this quarter.", translate=_translator(None, fail=True)
    )
    assert result["status"] == "SKIPPED"
    assert result["metrics_checked"] == 0
    assert result["unverified"] == 1
    assert "translation failed" in result["unverified_reason"]
    assert "key: value" in result["skip_reason"]


@z3_skip
def test_a_failed_translation_falls_back_to_the_value_comparison():
    """Hierarchy step 2: checked by value, and labelled as that, not as relational."""
    result = verify_locks(
        LOCKS,
        "Revenue: 12000000 and ARR is $12M this quarter.",
        translate=_translator(None, fail=True),
    )
    assert result["checked_by_value"] == 2
    assert result["checked_by_relational"] == 0
    assert result["verified"] == 2
    assert result["status"] == "PASS"
    tiers = [record.get("tier") for record in result["claim_results"]]
    assert "value" in tiers
    assert any(
        record.get("note") == "checked by value, not relationship"
        for record in result["claim_results"]
    )
