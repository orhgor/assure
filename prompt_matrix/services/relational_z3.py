"""Tier 2 Math Check: a translated claim, checked against the locked source facts.

Tier 1 (``_parse_metrics`` + ``TruthLedgerEngine``) compares ``key: value`` labels.
This module is the other half of the tiered path: it takes the JSON a model
produced for one prose claim (``services.relational_translate``) and decides a
verdict with Z3 against the values the source actually locked. No model runs here
— the translation is the model's only job, and the numbers it proposes never
decide anything on their own.

Claim shape (the translation contract)::

    {"metric": str,
     "operands": [{"name": str, "value": number, "unit": str, "source_sentence": str}],
     "relation": "eq" | "lt" | "le" | "gt" | "ge",
     "expected": number}

Deciding rule, in order:

1. The claim asserts two things about ``operands[0]``: its **value** is
   ``operands[0].value``, and that value stands in ``relation`` to ``expected``.
2. ``operands[0]``'s name — else the metric name — is looked up in the locked
   source facts. **No locked fact means no verdict**: the result is UNKNOWN with
   the reason, never a pass. A check that did not happen is never reported as
   passing, which is the same intent ``verify_locks`` already pins for Tier 1.
3. With the fact pinned to the locked value, both assertions are put to Z3 as
   separate queries, so the verdict names which one the source refutes:
   * **value** — is ``v ≠ claimed`` satisfiable? The locked value is the only
     value ``v`` may take, so ``sat`` means the draft's figure contradicts the
     source (this is Tier 1's comparison, and it is why a draft that says "churn
     0.07" over a locked 0.04 is a violation however the relation reads).
   * **relation** — is ``¬(v relation expected)`` satisfiable? ``sat`` means the
     locked value does not satisfy the relation the draft asserts.
   ``unsat`` on both is VERIFIED (the source is consistent with everything the
   claim asserts); either ``sat`` is VIOLATED, and Z3's model for that query is
   the counterexample; ``unknown`` (timeout) is UNKNOWN with the timeout named.

A translation whose own numbers are sloppy can only move the *relation* query,
and only where the source value really fails it — "churn 0.04 stayed under 2%" is
reported VIOLATED for a locked 0.04, which is exactly what the sentence says.

VERIFIED means "consistent with the locked source value", which is a weaker
statement for an inequality claim than for an ``eq`` claim: ``ARR > 10M`` is
consistent with a locked 12M, but so is a claim the source merely fails to
contradict. The verdict text says which one was checked.

Everything here is deterministic: the same translation and the same facts give
the same verdict on the same Z3 build. That is why ``Z3_PINNED_VERSION`` is part
of the translation cache key as well as this module's contract — two Z3 versions
may disagree on the same input, and a cached verdict must not cross that line.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable

from z3 import Not, Real, Solver, sat, unknown

#: The Z3 build the cached verdicts were produced under. Bumping this invalidates
#: every cached translation (see ``relational_translate.CACHE_KIND``); a verdict
#: is only reusable against the solver that produced it.
Z3_PINNED_VERSION = "5.1.0"

#: Solver wall clock, per claim. Ground arithmetic like this decides in
#: microseconds; the bound exists so a pathological translation cannot stall the
#: compile, and a timeout reads UNKNOWN rather than guessing.
DEFAULT_TIMEOUT_MS = 10_000

VERIFIED = "VERIFIED"
VIOLATED = "VIOLATED"
UNKNOWN = "UNKNOWN"

RELATIONS = ("eq", "lt", "le", "gt", "ge")

_OPS = {
    "eq": lambda left, right: left == right,
    "lt": lambda left, right: left < right,
    "le": lambda left, right: left <= right,
    "gt": lambda left, right: left > right,
    "ge": lambda left, right: left >= right,
}

_RELATION_TEXT = {
    "eq": "equal to",
    "lt": "less than",
    "le": "at most",
    "gt": "greater than",
    "ge": "at least",
}

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_NUMBER_RE = re.compile(r"\d")
_WORD_RE = re.compile(r"\w+")
_NON_NAME_RE = re.compile(r"[^a-z0-9]+")

#: A claim that compares two bounds is not a single-value comparison. Tier 2
#: checks one value against one locked value; a range needs two, which is Tier 3
#: (named as a follow-up, deliberately not built).
_RANGE_RES = (
    re.compile(r"\bbetween\s+\$?\d[\d,.]*\s+and\s+\$?\d", re.IGNORECASE),
    re.compile(r"\$?\d[\d,.]*\s*(?:to|through|–|—|-)\s*\$?\d", re.IGNORECASE),
)
#: A sentence this long is a paragraph, not a claim: one translation call is not
#: going to produce a well-formed relation for it, so it is not offered.
MAX_CLAIM_CHARS = 400

#: Words that carry no metric meaning, so a claim is not built from a stub like
#: "Q3 2024." (a heading with a numeral in it).
MIN_CLAIM_WORDS = 3


def z3_version() -> str:
    """The running Z3 build, for the cache key and the report."""
    try:
        from z3 import get_version_string

        return str(get_version_string())
    except Exception:  # pragma: no cover - z3 always exposes this
        return "unknown"


def pinned_version_matches() -> bool:
    """Whether the running Z3 is the build the cached verdicts are pinned to.

    A mismatch is not fatal — the cache key carries the *running* version, so two
    builds never share an entry — but it is worth saying out loud on a box that
    has not been rebuilt since the pin changed, because the same claim can decide
    differently across versions and the difference should not look like a model's.
    """
    return z3_version() == Z3_PINNED_VERSION


if not pinned_version_matches():  # pragma: no cover - depends on the environment
    logging.getLogger(__name__).warning(
        "[math-check] z3 %s is running, but verdicts are pinned to %s; "
        "cached translations from the other build will not be reused",
        z3_version(),
        Z3_PINNED_VERSION,
    )


def normalize_name(name: Any) -> str:
    """Case- and punctuation-insensitive metric name, for fact lookup.

    ``"Q3 Revenue ($M)"`` and ``"q3_revenue"`` are the same metric to this
    lookup; the ledger's canonical keys are written by the lock inference, so
    exact spelling is not something the model can be relied on to reproduce.

    Separators collapse to a single ``_`` rather than disappearing: the
    normalized form is also what the translation prompt shows the model, and
    ``cpt_90837_minimum_time_per_session`` is a name a model can match, while
    ``cpt90837minimumtimepersession`` is a string it will misspell.
    """
    return _NON_NAME_RE.sub("_", str(name or "").strip().lower()).strip("_")


def facts_from_locks(locks: Iterable[dict[str, Any]]) -> dict[str, float]:
    """``{normalized name: value}`` — the locked source values, both names a lock carries.

    A lock records ``canonical_key`` and often a shorter ``metric`` alias
    (``Revenue`` / ``ARR``); either may be the name a claim uses, so both are
    registered. Unparsable values are skipped — an unusable lock is the
    inference's problem, not a fact.
    """
    facts: dict[str, float] = {}
    for lock in locks or []:
        if not isinstance(lock, dict):
            continue
        try:
            value = float(lock.get("value"))
        except (TypeError, ValueError):
            continue
        for raw in (lock.get("canonical_key"), lock.get("metric"), lock.get("entity")):
            key = normalize_name(raw)
            if key:
                facts.setdefault(key, value)
    return facts


def states_a_range(sentence: str) -> bool:
    """Whether the claim compares two bounds rather than stating one value.

    Measured on the fixture: "the documented face-to-face psychotherapy time
    falls between 38 and 52 minutes" was translated as the ledger's *range* key
    with the lower bound as its value, and the locked upper bound (52) then made
    a sentence the source agrees with read as a violation. Whichever bound the
    model picks, one of them disagrees with a single locked value — so the
    verdict would be the schema's artefact, not the draft's error.

    A range is Tier 3. Nothing here decides it; the caller reports it unchecked
    with this reason, which is also why this runs before the translation call
    rather than after it.
    """
    text = str(sentence or "")
    return any(pattern.search(text) for pattern in _RANGE_RES)


def split_claims(text: str) -> list[str]:
    """Candidate claim sentences: sentence-split, numeral-bearing, one thought each.

    Ordered as the draft has them, so a capped run checks the first claims a
    reader sees rather than an arbitrary subset.
    """
    claims: list[str] = []
    for raw in _SENTENCE_RE.split(str(text or "")):
        sentence = " ".join(raw.split()).strip(" \t-*#|")
        if not sentence or len(sentence) > MAX_CLAIM_CHARS:
            continue
        if not _NUMBER_RE.search(sentence):
            continue
        if len(_WORD_RE.findall(sentence)) < MIN_CLAIM_WORDS:
            continue
        claims.append(sentence)
    return claims


def _verdict(
    verdict: str,
    reason: str = "",
    counterexample: dict[str, Any] | None = None,
    *,
    evidence: str = "",
) -> dict:
    """A verdict record. ``evidence`` is Z3's model for a refuting query."""
    return {
        "verdict": verdict,
        "reason": reason,
        "counterexample": counterexample,
        "evidence": evidence,
    }


def _safe_symbol(name: str) -> str:
    return normalize_name(name) or "operand"


def check_relation(
    claim: dict[str, Any],
    facts: dict[str, float],
    *,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> dict[str, Any]:
    """Decide one translated claim against ``facts``. Never raises.

    Returns ``{"verdict", "reason", "counterexample"}``. UNKNOWN carries the
    reason the check could not run; VERIFIED/VIOLATED carry the locked value
    that decided it, so the UI can show both numbers side by side.
    """
    if not isinstance(claim, dict):
        return _verdict(UNKNOWN, "translation is not an object")

    metric = str(claim.get("metric") or "").strip()
    relation = str(claim.get("relation") or "").strip().lower()
    operands = [o for o in (claim.get("operands") or []) if isinstance(o, dict)]

    if relation not in RELATIONS:
        return _verdict(UNKNOWN, f"unsupported relation {relation or '(missing)'!r}")
    if not operands:
        return _verdict(UNKNOWN, "translation carries no operand to check")

    primary = operands[0]
    try:
        claimed = float(primary.get("value"))
        expected = float(claim.get("expected"))
    except (TypeError, ValueError):
        return _verdict(UNKNOWN, "operand value and expected must both be numbers")

    operand_name = str(primary.get("name") or metric or "").strip()
    source = facts.get(normalize_name(operand_name))
    if source is None and metric:
        source = facts.get(normalize_name(metric))
    if source is None:
        return _verdict(
            UNKNOWN,
            f"no locked source value for {operand_name or '(unnamed operand)'}"
            + (f" or {metric}" if metric and metric != operand_name else ""),
        )

    solver = Solver()
    solver.set("timeout", int(timeout_ms))
    primary_var = Real("v_" + _safe_symbol(operand_name or metric))
    solver.add(primary_var == source)
    # Extra operands are context the claim cited; when the ledger holds them they
    # are pinned too, so a translation cannot smuggle a value past the source.
    for extra in operands[1:]:
        extra_name = normalize_name(extra.get("name"))
        if extra_name and extra_name in facts:
            solver.add(Real("v_" + extra_name) == facts[extra_name])

    detail = {
        "metric": metric or operand_name,
        "relation": relation,
        "claimed": claimed,
        "expected": expected,
        "source": source,
        "unit": str(primary.get("unit") or ""),
        "claimed_sentence": str(primary.get("source_sentence") or ""),
    }

    # Query 1 — does the draft's figure contradict the locked source value?
    # A translation whose own numbers do not satisfy its own relation is not a
    # faithful encoding of the sentence (measured on a real fixture: "gravitational
    # forces exceeding 9G" came back as value 9, expected 9, relation gt — the
    # threshold in both fields). Deciding that would report the encoding, not the
    # draft, so it is left unchecked with the reason instead.
    if not _OPS[relation](claimed, expected):
        return _verdict(
            UNKNOWN,
            f"the translation is not self-consistent: {_fmt(claimed)} does not satisfy "
            f"{relation} {_fmt(expected)}, so there is nothing here to check against the source",
            detail,
        )

    solver.push()
    solver.add(primary_var != claimed)
    value_result = solver.check()
    value_model = _model_text(solver) if value_result == sat else ""
    solver.pop()

    # Query 2 — does the locked source value satisfy the asserted relation?
    solver.push()
    solver.add(Not(_OPS[relation](primary_var, expected)))
    relation_result = solver.check()
    relation_model = _model_text(solver) if relation_result == sat else ""
    solver.pop()

    if value_result == unknown or relation_result == unknown:
        return _verdict(UNKNOWN, f"z3 returned unknown within {int(timeout_ms) // 1000}s", detail)

    if value_result == sat:
        return _verdict(
            VIOLATED,
            f"draft claims {_fmt(claimed)}{_unit_text(detail)}, locked source value is "
            f"{_fmt(source)}{_unit_text(detail)} — the figure does not match the source",
            detail,
            evidence=value_model,
        )
    if relation_result == sat:
        return _verdict(
            VIOLATED,
            f"locked source value {_fmt(source)}{_unit_text(detail)} does not satisfy the "
            f"claim's relation ({_RELATION_TEXT[relation]} {_fmt(expected)})",
            detail,
            evidence=relation_model,
        )
    return _verdict(
        VERIFIED,
        f"locked source value {_fmt(source)}{_unit_text(detail)} matches the draft's figure "
        f"and satisfies the claim's relation ({_RELATION_TEXT[relation]} {_fmt(expected)})",
        detail,
    )


def _unit_text(detail: dict[str, Any]) -> str:
    unit = str(detail.get("unit") or "").strip()
    return f" {unit}" if unit else ""


def _model_text(solver: Any) -> str:
    """Z3's model for the refuting query, as ``name = value`` pairs.

    This is the counterexample the verdict is reported with — it is the solver's
    assignment, not a restatement of the numbers we passed in.
    """
    try:
        model = solver.model()
        assignments = sorted(
            (str(d), str(model[d])) for d in model.decls()
        )
    except Exception:  # pragma: no cover - a model always exists after sat
        return ""
    return "; ".join(f"{name} = {value}" for name, value in assignments)


def _fmt(value: float) -> str:
    """Compact numeric text: 12000000 → 12000000, 0.4 → 0.4 (no locale formatting)."""
    if float(value).is_integer():
        return str(int(value))
    return repr(float(value))


def violation_text(result: dict[str, Any]) -> str:
    """The one-line violation, in the shape the tree/UI already parse.

    ``apply_z3_violations_to_tree`` reads ``Metric '<key>'`` out of each string to
    find the paragraph that names the metric, and the Math Check tab joins the
    strings for display — so a Tier 2 violation is worded like a Tier 1 one, puts
    both numbers in the same sentence, and carries Z3's model for the query that
    refuted the claim.
    """
    detail = result.get("counterexample") or {}
    key = str(detail.get("metric") or "")
    unit = f" {detail['unit']}" if detail.get("unit") else ""
    source = _fmt(detail.get("source")) if detail.get("source") is not None else "?"
    claimed = _fmt(detail.get("claimed")) if detail.get("claimed") is not None else "?"
    evidence = str(result.get("evidence") or "")
    tail = f" (z3: {evidence})" if evidence else ""
    return (
        f"Metric '{key}' (relational): draft claims {claimed}{unit}, "
        f"locked source value is {source}{unit}{tail}."
    )


__all__ = [
    "DEFAULT_TIMEOUT_MS",
    "MAX_CLAIM_CHARS",
    "MIN_CLAIM_WORDS",
    "RELATIONS",
    "UNKNOWN",
    "VERIFIED",
    "VIOLATED",
    "Z3_PINNED_VERSION",
    "check_relation",
    "facts_from_locks",
    "normalize_name",
    "split_claims",
    "violation_text",
    "z3_version",
]
