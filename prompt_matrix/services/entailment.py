"""Adversarial claim entailment for anchored paragraphs.

``models/jdf.py:attach_substrate_provenance_to_tree`` anchors a paragraph to a
*run of consecutive substrate sentences* by *wording* similarity (token overlap +
numeric matching) and says so in its own TODO. This module adds the missing step:
does the anchored evidence actually *entail* the claim the paragraph makes? It is
given the same run the matcher scored (the provenance row's ``anchor_window``),
not one sentence quoted out of it — a claim the source states across two sentences
is not judged against half of them.

Verdicts (frozen contract, persisted at ``node.meta.provenance.entailment``):

    {"verdict": "yes" | "partial" | "no" | "contradicts" | "unverified",
     "contradicted": bool,
     "reasoning": "<one sentence>",
     "model": "bedrock/us.anthropic.claude-sonnet-5-5",
     "checked_at": "<ISO8601>",
     "prompt_version": ENTAILMENT_PROMPT_VERSION}

``yes`` is the only verdict that means verified. ``partial`` and ``no`` are real
judgements — ``no`` is "the source does not state this" (absent figure, unrelated
source); ``contradicts`` (added 2026-09-27, prompt version 2) is "the source says
otherwise", which the customer policy reports as CONTRADICTED and which was
folded into ``no`` before, so a fabricated figure and a denied claim were the
same bucket. ``unverified`` is the visible failure of a call that could not be
made or could not be parsed — never a silent pass, and never a fallback to the
lexical anchor alone. ``contradicted`` is ``True`` iff the verdict is
``contradicts`` (readers written for the older record keep working).

A ``contradicts`` must be backed by the conflicting source text: the prompt asks
for it on an ``EVIDENCE:`` line and ``enforce_contradiction_evidence`` re-finds it
verbatim in the window (``llm_extraction.find_verbatim``); when it is not there —
or when the model's own REASON describes an absence ("does not mention") and no
conflict — the verdict is downgraded to ``no``. Measured on the live stack 2026-09-27 (Llama
3.3 70B drafting, mistral-small-24b judging): the four-label prompt without this
answered ``contradicts`` for "The policy in question is an auto policy with the
number AP-2025-0001" against "Policy Number: AP-2025-0001" — a fact merely absent
from a one-sentence window is not a contradiction, and the model's own quote is
what tells the two apart.

One model call per anchored paragraph, reused within a compile and — through
``services/entailment_cache``, keyed on the claim, the evidence window, the
composed prompt, the model and the pipeline version — across compiles too, so a
re-stated paragraph costs no second judgement. A failed call is never cached.
"""

from __future__ import annotations

import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any, Callable

try:
    from ..cost_governance import CostGovernor, TaskType
    from .claim_policy import sentence_units
    from .entailment_cache import load_verdict, store_verdict, verdict_cache_key
    from .llm_extraction import find_verbatim
    from .numeric_recompute import extract_figures
except ImportError:
    from claim_policy import sentence_units
    from cost_governance import CostGovernor, TaskType
    from entailment_cache import load_verdict, store_verdict, verdict_cache_key
    from llm_extraction import find_verbatim
    from numeric_recompute import extract_figures

try:
    from . import model_calls as _mc
except ImportError:  # pragma: no cover - flat-import fallback
    import model_calls as _mc  # type: ignore

_log = logging.getLogger(__name__)

#: Bumped whenever the prompt's labels or rules change. Part of the entailment
#: cache key and of the compile cache key (``routers/draft._prompt_key_material``),
#: so a verdict judged under an older rule is re-asked, not replayed. 2 = the
#: four-label prompt of 2026-09-27 (``contradicts`` split out of ``no``); 3 = the
#: EVIDENCE line and the explicit "absence is no" rule, same day, after the live
#: run showed ``contradicts`` answered for facts a one-line window did not mention.
ENTAILMENT_PROMPT_VERSION = 3

VERDICTS = ("yes", "no", "partial", "contradicts", "unverified")

#: ``checker(claim, source) -> entailment record``. Injected so callers (routes,
#: tests) can swap the transport without touching the prompt or the persistence.
EntailmentChecker = Callable[[str, str], dict[str, Any]]

_MAX_REASON_CHARS = 240
_SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{6,}|Bearer\s+\S+)", re.IGNORECASE)
_VERDICT_RE = re.compile(
    r"verdict\s*[:\-]\s*\**\s*(yes|no|partial|contradicts|contradicted|contradiction)\b",
    re.IGNORECASE,
)
_BARE_VERDICT_RE = re.compile(
    r"\b(yes|no|partial|contradicts|contradicted|contradiction)\b", re.IGNORECASE
)
_REASON_RE = re.compile(
    r"reason\s*[:\-]\s*(.+?)(?=\n\s*\**\s*evidence\s*[:\-]|\Z)", re.IGNORECASE | re.DOTALL
)
_EVIDENCE_RE = re.compile(r"evidence\s*[:\-]\s*\**\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_MAX_EVIDENCE_CHARS = 400
#: A REASON that describes absence, not conflict. A ``contradicts`` whose own
#: reasoning is one of these is an absence the model mislabelled (live run
#: 2026-09-27: "The SOURCE does not mention the VIN, total premium, …" came back
#: ``contradicts`` with the whole one-line window as EVIDENCE).
_ABSENCE_RE = re.compile(
    r"\b(?:does not|doesn't|do not|don't|did not|didn't|never|not) (?:mention|contain|state|include|"
    r"provide|specify|refer|address|cover|list|name|say|discuss|indicate|describe|confirm|support)"
    r"|\bno (?:mention|reference|information) (?:of|to|about|on)|\bis silent\b|\bnot (?:mentioned|stated|"
    r"included|present|found|specified|provided|contained)\b|\babsent\b|\bomits?\b|\blacks?\b",
    re.IGNORECASE,
)
_CONFLICT_RE = re.compile(
    r"\b(?:different|differs?|instead|rather than|whereas|contrary|conflicts?|contradicts?|opposite|"
    r"reverses?|negates?|denies|excluded?s?\b.{0,40}\bcover|cover.{0,40}\bexclud|not \$|is \$|"
    r"states? (?:that )?(?:it|the [a-z ]+) (?:is|was|are|were) (?:not )?[\w$]|but the source|"
    r"the source (?:says|states|gives|lists|shows) [\w$]+ (?:as|is|of)|higher|lower|later|earlier|"
    r"more than|less than|greater|smaller|another|other than|mismatch|inconsistent|incorrect|wrong)",
    re.IGNORECASE,
)

_PROMPT = """\
You are an adversarial claim-entailment auditor. SOURCE is a verbatim extract \
from a document the author cited. CLAIM is a sentence the author wrote and \
attributed to that document.

Decide whether the SOURCE supports the CLAIM. Do not be charitable. Wording that \
merely overlaps is not support: ask what the SOURCE actually asserts. Phrases in \
the CLAIM such as "as stated in sentence", "according to line", "as per lines and" \
are citation remnants, not assertions: ignore them. Do not \
assume facts the SOURCE does not state, and do not give the CLAIM the benefit of \
the doubt about numbers, parties, obligations, direction, negation, modality, or \
scope. A paraphrase that preserves the substance of the source — numbers, \
entities, modifiers — is supported. Restatement using different words is not a \
gap. Supporting detail that names the entities the user's question asked about \
is supported, not partial.

Apply these rules in priority order and stop at the first that matches:
1. contradicts — the SOURCE states a DIFFERENT value, date, amount, party, \
direction or negation FOR THE SAME ITEM the CLAIM names (the CLAIM says the \
deductible is $500 and the SOURCE says the deductible is $250; the CLAIM says \
covered and the SOURCE says excluded). A fact the SOURCE simply does not mention \
is NEVER a contradiction. You must copy the conflicting SOURCE text exactly on \
the EVIDENCE line. If your REASON would say that the SOURCE "does not mention" \
or "does not contain" something, the VERDICT is no, not contradicts.
2. no — the CLAIM states a number, date, percentage, amount, party or \
conclusion that the SOURCE does not contain and does not contradict. A figure \
the SOURCE lacks is not found, never partial. If the SOURCE is unrelated to the \
CLAIM, or covers only part of what the CLAIM says and conflicts with none of it, \
answer no.
3. partial — the SOURCE supports the CLAIM's central assertion, but the CLAIM \
also asserts another material element (party, obligation, scope, or modality) \
that the SOURCE neither states nor contradicts.
4. yes — the SOURCE states or directly entails every material element of the CLAIM.

Answer with exactly these three lines and nothing else:
VERDICT: yes|partial|no|contradicts
REASON: one sentence
EVIDENCE: the exact SOURCE text that conflicts with the CLAIM, copied verbatim, \
only when VERDICT is contradicts; otherwise the single word none

SOURCE:
{source}

CLAIM:
{claim}
"""


def build_entailment_prompt(claim: str, source: str) -> str:
    """The adversarial entailment prompt. Exactly one verdict, plus one sentence."""
    return _PROMPT.format(source=str(source or "").strip(), claim=str(claim or "").strip())


def _sanitize(text: str) -> str:
    """Reason/error text with credentials scrubbed and bounded."""
    cleaned = _SECRET_RE.sub("[redacted]", str(text or "").replace("\n", " ").strip())
    return cleaned[:_MAX_REASON_CHARS]


def _record(verdict: str, reasoning: str, model: str, evidence: str = "") -> dict[str, Any]:
    out = {
        "verdict": verdict,
        "reasoning": _sanitize(reasoning),
        "model": model,
        "checked_at": datetime.now(UTC).isoformat(),
    }
    if evidence:
        out["evidence"] = _SECRET_RE.sub("[redacted]", str(evidence).strip())[:_MAX_EVIDENCE_CHARS]
    return out


_NUMERIC_CONFLICT_RE = re.compile(
    r"\$|\d|\b(?:amount|value|figure|number|sum|total|deductible|premium|limit|rate|percent|"
    r"price|cost|fee|balance|payment)s?\b",
    re.IGNORECASE,
)


def _is_year(fig: Any) -> bool:
    return fig.kind == "number" and 1900 <= fig.value <= 2100


def _figures_all_in_claim(evidence: str, claim: str, reasoning: str) -> bool:
    """True when the model's reason claims a numeric difference, the evidence
    carries at least one value (money, percent, number — years and dates aside)
    and every one of them also appears in the claim. A "different value" reported
    for such an evidence line is refuted by the two texts themselves (live run
    2026-09-27: EVIDENCE "Comprehensive Deductible: $250" against a claim stating
    "the comprehensive deductible is $250" came back ``contradicts`` — "the
    deductible for comprehensive coverage is different"). A textual conflict
    ("declined" against "grew", both "in fiscal 2025") is not touched: the shared
    year is context, and the reason names no amount."""
    if not _NUMERIC_CONFLICT_RE.search(reasoning or ""):
        return False
    evidence_figures = [
        fig for fig in extract_figures(evidence) if fig.kind != "date" and not _is_year(fig)
    ]
    if not evidence_figures:
        return False
    claim_figures = extract_figures(claim or "")
    return all(any(fig.same_value(other) for other in claim_figures) for fig in evidence_figures)


#: Polarity pairs a policy states its position with; either order, stem match.
_POLARITY_PAIRS = (
    ("cover", "exclu"), ("includ", "exclu"), ("approv", "den"), ("approv", "reject"), ("paid", "unpaid"),
    ("active", "cancel"), ("valid", "expir"), ("insured", "uninsured"), ("eligible", "ineligible"), ("permit", "prohibit"),
    # direction words a figure moves with (stems: grew/grow/growth vs declined/decline, rose vs fell …)
    ("grew", "declin"), ("grow", "declin"), ("grow", "decreas"), ("grew", "fell"), ("rose", "fell"), ("increas", "decreas"),
    ("increas", "declin"), ("increas", "fell"), ("higher", "lower"), ("above", "below"), ("exceed", "below"), ("gain", "loss"),
    ("profit", "loss"), ("surplus", "deficit"),
)
_FIGURE_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _visible_opposition(claim: str, evidence: str) -> bool:
    """Does the evidence visibly oppose the claim — a negation flip, an antonym or
    polarity pair, or a figure that differs? ``evidence_assembly.
    _has_explicit_opposition`` first; then the policy polarity pairs (it needs
    topic overlap that a short window may not have: "Flood is covered under the
    policy." vs "Flood is excluded." returned False); then differing figures."""
    try:
        try:
            from .evidence_assembly import _has_explicit_opposition
        except ImportError:
            from evidence_assembly import _has_explicit_opposition  # type: ignore
        if _has_explicit_opposition(claim, evidence):
            return True
    except Exception:  # noqa: BLE001 — the detector is a guard, never a blocker
        return True
    c, e = claim.lower(), evidence.lower()
    for a, b in _POLARITY_PAIRS:
        if (a in c and b in e and a not in e) or (b in c and a in e and b not in e):
            return True
    claim_figures = {f.replace(",", "") for f in _FIGURE_RE.findall(c)}
    evidence_figures = {f.replace(",", "") for f in _FIGURE_RE.findall(e)}
    if claim_figures and evidence_figures and not evidence_figures <= claim_figures:
        return True
    return False


def enforce_contradiction_evidence(
    record: dict[str, Any], source: str, claim: str | None = None
) -> dict[str, Any]:
    """A ``contradicts`` verdict stands only on conflicting text found verbatim in
    ``source`` whose figures do not all reappear in the claim, and whose reason
    names a conflict rather than an absence; otherwise it is downgraded to ``no``
    with the reason kept.

    The comparison is ``llm_extraction.find_verbatim`` (whitespace-collapsed,
    case-insensitive). ``downgraded_from`` records what the model said, so the
    Evidence pane can show that a contradiction was claimed and not backed.
    """
    if not isinstance(record, dict) or record.get("verdict") != "contradicts":
        return record
    evidence = str(record.get("evidence") or "").strip()
    reasoning = str(record.get("reasoning") or "")
    verbatim = bool(evidence) and evidence.lower() != "none" and find_verbatim(str(source or ""), evidence) is not None
    absence_only = bool(_ABSENCE_RE.search(reasoning)) and not _CONFLICT_RE.search(reasoning)
    refuted = claim is not None and verbatim and _figures_all_in_claim(evidence, claim, reasoning)
    # Lexical opposition (2026-09-27, live: "The policy excludes flood damage …
    # and the agent is Mary Agent" was called ``contradicts`` against the
    # verbatim evidence "Flood damage is excluded under this policy" — the
    # window merely lacks the second clause). A contradiction must show in the
    # words: a negation flip, an antonym pair, or a differing figure between the
    # claim and the evidence (``evidence_assembly._has_explicit_opposition``).
    # Without one, the model's verdict is not a conflict the reader can see, so
    # it is treated as "not fully stated" — never as contradicted.
    opposed = True
    if claim is not None and verbatim:
        opposed = _visible_opposition(str(claim), evidence)
    if verbatim and not absence_only and not refuted and opposed:
        return record
    out = dict(record)
    out["verdict"] = "no"
    out["downgraded_from"] = "contradicts"
    if not verbatim:
        why = "contradiction claimed without verbatim source text; treated as not stated. "
    elif refuted:
        why = "contradiction claimed on figures the claim states identically; treated as not stated. "
    elif not opposed:
        why = "contradiction claimed but the evidence shows no negation flip, antonym or differing figure against the claim; treated as not stated. "
    else:
        why = "contradiction claimed on what the source does not mention; treated as not stated. "
    out["reasoning"] = _sanitize(why + reasoning)
    return out


def unverified(reason: str, model: str = "") -> dict[str, Any]:
    """The visible failure record: a call that could not produce a verdict."""
    return _record("unverified", reason, model)


def parse_entailment_verdict(raw: str, model: str) -> dict[str, Any]:
    """Parse a model answer into the frozen entailment record.

    Unparseable output is ``unverified`` — this check must never answer by
    default, in either direction.
    """
    text = str(raw or "").strip()
    if not text:
        return unverified("model returned an empty answer", model)
    if text.upper().startswith("ERROR:"):
        return unverified(text, model)

    match = _VERDICT_RE.search(text)
    if match is None:
        # Tolerate a bare first word ("yes") — but only inside the opening line,
        # so a stray "no" in a long explanation cannot decide the verdict.
        head = text.splitlines()[0][:120]
        match = _BARE_VERDICT_RE.search(head)
    if match is None:
        return unverified(f"unparseable verdict: {_sanitize(text)}", model)

    verdict = match.group(1).lower()
    if verdict.startswith("contradict"):
        verdict = "contradicts"
    reason_match = _REASON_RE.search(text)
    if reason_match is not None:
        reasoning = reason_match.group(1)
    else:
        reasoning = text[match.end() :]
    evidence_match = _EVIDENCE_RE.search(text)
    evidence = evidence_match.group(1).strip().strip('"').strip() if evidence_match else ""
    if evidence.lower() == "none":
        evidence = ""
    return _record(verdict, reasoning or "(no reasoning given)", model, evidence)


def check_entailment(claim: str, source: str, *, project_id: str = "") -> dict[str, Any]:
    """Ask the SEMANTIC_VALIDATION policy model whether ``source`` entails ``claim``.

    The one place a verdict is obtained, and so the one place it is cached: a
    judgement already made for this (claim, evidence) under this prompt and this
    model is returned from ``entailment_cache`` without a model call. The key
    covers the claim, the evidence, the composed prompt, the model and the
    pipeline version, so any of those changing is a new question and a real call.

    Never raises: every failure becomes an ``unverified`` record carrying the
    reason, so a broken call degrades the gate to "not verified" rather than
    falling back to the lexical anchor or (worse) a pass. A failure is never
    cached — a transient 401 must not become this claim's permanent verdict.
    """
    gov = CostGovernor()
    model_id = ""
    try:
        model_id = gov.policy_for(TaskType.SEMANTIC_VALIDATION).model_id
    except Exception:
        pass

    prompt = build_entailment_prompt(claim, source)
    # The prompt version rides in the key beside the prompt's own hash: the hash
    # already moves when the text changes, and the version names the rule the
    # verdict was judged under, so a record cached under the three-label prompt
    # is never replayed as a four-label one.
    cache_key = (
        verdict_cache_key(claim, source, f"v{ENTAILMENT_PROMPT_VERSION}\n{prompt}", model_id)
        if model_id
        else ""
    )
    cached = load_verdict(cache_key)
    if cached is not None:
        _log.info("[entailment-cache] hit %s", cache_key)
        return cached

    messages = [{"role": "user", "content": prompt}]
    try:
        policy = gov.preflight(project_id or "entailment", TaskType.SEMANTIC_VALIDATION, messages)
        executor = gov.executor or gov._default_executor  # noqa: SLF001
        with _mc.stage_context("entailment", project_id=project_id or None):
            raw, in_tok, out_tok = executor(
                policy.litellm_model,
                messages,
                policy.max_output_tokens,
                policy.caching,
            )
    except Exception as exc:  # budget, hard cap, transport, missing key
        return unverified(f"{type(exc).__name__}: {exc}", model_id)

    try:
        gov.record_usage(
            project_id or "entailment",
            input_tokens=int(in_tok or 0),
            output_tokens=int(out_tok or 0),
            model_id=policy.model_id,
            task_type=TaskType.SEMANTIC_VALIDATION,
            meta={"pipeline": "claim_entailment"},
        )
    except Exception:
        # Accounting must never change a verdict; the call already happened.
        pass

    record = enforce_contradiction_evidence(
        parse_entailment_verdict(raw, policy.model_id), source, claim=claim
    )
    if record.get("verdict") != "unverified":
        store_verdict(cache_key, project_id, record)
    return record


def _claim_sources(node: dict[str, Any]) -> list[str]:
    """Every source sentence this node cites — one entry per provenance row.

    The evidence is the row's ``anchor_window`` — the run of consecutive source
    sentences the matcher cleared its floors against — and only falls back to
    ``extracted_quote`` when the row predates the window (or the window was a
    single sentence, where the two are the same text). For a cited paragraph the
    row is the model's own citation, so ``extracted_quote`` is the cited
    sentence.

    Returns all of them, not the first: a paragraph that cites three sentences is
    supported by the three together, and judging it against one of them reports a
    weaker claim than the source carries. Never ``meta.provenance.excerpt``,
    which falls back to the claim text itself and would make the check a
    tautology.
    """
    out: list[str] = []
    for row in node.get("provenance") or []:
        if not isinstance(row, dict):
            continue
        evidence = str(row.get("anchor_window") or "").strip() or str(
            row.get("extracted_quote") or ""
        ).strip()
        if evidence and evidence not in out:
            out.append(evidence)
    return out


def _aggregate_verdicts(verdicts: list[str]) -> str:
    """Per-citation verdicts -> the paragraph's verdict (rule of 2026-09-27).

    * any ``contradicts`` (verbatim-backed, see
      ``enforce_contradiction_evidence``)   -> ``contradicts``;
    * else all ``yes``                       -> ``yes``;
    * else ``yes`` beside only ``no``        -> ``yes`` — a window that does not
                                                mention the claim does not take
                                                away from one that states it;
                                                absence is not disagreement;
    * else any ``yes`` or ``partial``        -> ``partial`` (mixed with a missing
                                                qualifier or an unanswered call);
    * else any ``no``                        -> ``no``;
    * else                                   -> ``unverified``.

    An empty list is ``unverified``: ``all([])`` is vacuously True, so the all-yes
    test would otherwise return ``yes`` for a paragraph that carries citations but
    received no per-citation verdict.
    """
    if not verdicts:
        return "unverified"
    if any(verdict == "contradicts" for verdict in verdicts):
        return "contradicts"
    if all(verdict == "yes" for verdict in verdicts):
        return "yes"
    if any(verdict == "yes" for verdict in verdicts) and all(
        verdict in ("yes", "no") for verdict in verdicts
    ):
        return "yes"
    if any(verdict in ("yes", "partial") for verdict in verdicts):
        return "partial"
    if any(verdict == "no" for verdict in verdicts):
        return "no"
    return "unverified"


def _contradicted(verdicts: list[str]) -> bool:
    """True when any citation of the paragraph was contradicted by its source.

    Kept beside the verdict for readers of the older record, where it carried the
    ``no`` that the ``partial`` aggregate hid. Since 2026-09-27 ``no`` means "not
    stated" and only ``contradicts`` means the source says otherwise, so this is
    ``True`` exactly when the aggregate is ``contradicts``.
    """
    return any(verdict == "contradicts" for verdict in verdicts)


def _aggregate_reasoning(verdicts: list[str], citations: list[dict[str, Any]]) -> str:
    """One sentence for the paragraph — the failure first, the mix behind it.

    ``unverified`` is the visible failure of a call that could not be produced, and
    this string is what the Evidence pane shows for it. An aggregate that reads
    ``unverified over 1 citation(s)`` names the state and hides the reason
    ("entailment transport down"), which is the one thing the reader can act on and
    the reason ``unverified`` exists as a verdict rather than a silent pass. So a
    failure is reported first, and the per-citation verdicts are summarized behind
    it — the shape the record had when the check was one call per paragraph, with
    the aggregate it now needs beside it.
    """
    reasons = [str(citation.get("reasoning") or "").strip() for citation in citations]
    failures = [
        reason
        for verdict, reason in zip(verdicts, reasons)
        if verdict == "unverified" and reason
    ]
    summary = ", ".join(sorted(set(verdicts))) + f" over {len(verdicts)} citation(s)"
    detail = failures[0] if failures else next((reason for reason in reasons if reason), "")
    return _sanitize(f"{detail} ({summary})" if detail else summary)


def _unit_sources(rows: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        evidence = str(row.get("anchor_window") or "").strip() or str(row.get("extracted_quote") or "").strip()
        if evidence and evidence not in out:
            out.append(evidence)
    return out


def attach_entailment_to_tree(
    document: dict[str, Any],
    *,
    project_id: str = "",
    checker: EntailmentChecker | None = None,
    cache: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Entailment-verify every anchored claim in ``document``, in place.

    Runs after lexical anchoring (``models/jdf.py``) and writes the verdict at
    ``node.meta.provenance.entailment``. The claim unit is the sentence
    (``claim_policy.sentence_units``, 2026-09-27): a paragraph of several
    sentences is judged sentence by sentence, each against the windows its own
    citations name, and the record carries ``sentences[]`` beside the paragraph
    aggregate — a whole memo section used to read ``no`` because no one-line
    window carried all of its facts. A one-sentence paragraph is judged whole, as
    before. Pass ``cache`` to share verdicts within one compile; a verdict for the
    same (claim, evidence) already judged under the same prompt and model is
    reused from ``entailment_cache`` across compiles.
    """
    if not document:
        return document
    check = checker or check_entailment
    seen = cache if cache is not None else {}

    def _units(node: dict[str, Any]) -> list[dict[str, Any]]:
        units = sentence_units(node)
        return [dict(unit, sources=_unit_sources(unit["rows"])) for unit in units]

    # Prefetch the misses in parallel. The verdicts are independent (one model
    # call per (claim, source) pair) but were made strictly one after another:
    # a 15-paragraph draft with two citations each meant 30 sequential calls of
    # 1–3 s in the compile stream (audit 2026-09-24). The loop below is
    # unchanged; it now finds every record in ``seen``.
    pending: list[tuple[str, str]] = []
    for section in document.get("body") or []:
        if not isinstance(section, dict):
            continue
        for node in [section, *(section.get("children") or [])]:
            if not isinstance(node, dict) or str(node.get("type") or "") != "paragraph":
                continue
            for unit in _units(node):
                for source in unit["sources"]:
                    key = (unit["text"], source)
                    if key not in seen and key not in pending:
                        pending.append(key)
    if len(pending) > 1:
        workers = max(1, min(len(pending), int(os.environ.get("ASSURE_ENTAILMENT_WORKERS", "6") or 6)))

        def _one(key: tuple[str, str]) -> tuple[tuple[str, str], dict[str, Any]]:
            try:
                rec = check(key[0], key[1])
            except Exception as exc:
                rec = unverified(f"{type(exc).__name__}: {exc}")
            if not isinstance(rec, dict) or rec.get("verdict") not in VERDICTS:
                rec = unverified(f"checker returned no usable verdict: {rec!r}")
            return key, enforce_contradiction_evidence(rec, key[1], claim=key[0])

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for key, rec in pool.map(_one, pending):
                seen[key] = rec

    for section in document.get("body") or []:
        if not isinstance(section, dict):
            continue
        for node in [section, *(section.get("children") or [])]:
            if not isinstance(node, dict) or str(node.get("type") or "") != "paragraph":
                continue
            units = _units(node)
            if not any(unit["sources"] for unit in units):
                # Anchored nodes always carry a quote. A node that does not is not
                # checked and gets no verdict — the gate counts it as unanchored.
                continue
            per_citation: list[dict[str, Any]] = []
            sentences_out: list[dict[str, Any]] = []
            sentence_verdicts: list[str] = []
            for unit in units:
                if not unit["sources"]:
                    continue
                unit_verdicts: list[str] = []
                for source in unit["sources"]:
                    key = (unit["text"], source)
                    record = seen.get(key)
                    if record is None:
                        try:
                            record = check(unit["text"], source)
                        except Exception as exc:
                            record = unverified(f"{type(exc).__name__}: {exc}")
                        if not isinstance(record, dict) or record.get("verdict") not in VERDICTS:
                            record = unverified(f"checker returned no usable verdict: {record!r}")
                        record = enforce_contradiction_evidence(record, source, claim=unit["text"])
                        seen[key] = record
                    verdict = str(record.get("verdict") or "unverified")
                    unit_verdicts.append(verdict)
                    per_citation.append(
                        {
                            "source": source,
                            "verdict": verdict,
                            "reasoning": str(record.get("reasoning") or ""),
                            # The judgement's own provenance, carried up from the
                            # citation: the record's frozen shape is
                            # ``{verdict, reasoning, model, checked_at}`` and the
                            # aggregate is what a reader finds on the node.
                            "model": str(record.get("model") or ""),
                            "checked_at": str(record.get("checked_at") or ""),
                            # The conflicting source text behind a ``contradicts``
                            # (verbatim-checked), and what the model said when a
                            # contradiction was downgraded for lack of it.
                            "evidence": str(record.get("evidence") or ""),
                            "downgraded_from": str(record.get("downgraded_from") or ""),
                            "sentence_index": unit["index"],
                        }
                    )
                unit_verdict = _aggregate_verdicts(unit_verdicts)
                if unit_verdict == "no" and len(unit["sources"]) > 1:
                    # Every window alone says "not stated" and the sentence cites
                    # several: each may carry half ("2003 Honda Accord" in one
                    # line, the VIN in the next — live run 2026-09-27). One more
                    # call judges the sentence against the windows together; a
                    # "yes" there is the sentence's verdict, and the joined
                    # citation is recorded so the reader sees what was asked.
                    joined = " ".join(unit["sources"])
                    key = (unit["text"], joined)
                    record = seen.get(key)
                    if record is None:
                        try:
                            record = check(unit["text"], joined)
                        except Exception as exc:
                            record = unverified(f"{type(exc).__name__}: {exc}")
                        if not isinstance(record, dict) or record.get("verdict") not in VERDICTS:
                            record = unverified(f"checker returned no usable verdict: {record!r}")
                        record = enforce_contradiction_evidence(record, joined, claim=unit["text"])
                        seen[key] = record
                    joined_verdict = str(record.get("verdict") or "unverified")
                    per_citation.append(
                        {
                            "source": joined,
                            "verdict": joined_verdict,
                            "reasoning": str(record.get("reasoning") or ""),
                            "model": str(record.get("model") or ""),
                            "checked_at": str(record.get("checked_at") or ""),
                            "evidence": str(record.get("evidence") or ""),
                            "downgraded_from": str(record.get("downgraded_from") or ""),
                            "sentence_index": unit["index"],
                            "joined": True,
                        }
                    )
                    if joined_verdict in ("yes", "contradicts"):
                        unit_verdicts.append(joined_verdict)
                        unit_verdict = _aggregate_verdicts(unit_verdicts)
                sentence_verdicts.append(unit_verdict)
                sentences_out.append(
                    {
                        "index": unit["index"],
                        "text": unit["text"],
                        "verdict": unit_verdict,
                        "contradicted": _contradicted(unit_verdicts),
                    }
                )
            verdicts = [c["verdict"] for c in per_citation]
            # The judgements behind this paragraph, as the node reports them: one
            # model when they agree (they are the same checker under the same
            # prompt), the set when they do not, and the latest check time. An ISO
            # 8601 timestamp in one format sorts chronologically as a string.
            models = sorted({c["model"] for c in per_citation if c["model"]})
            checked_times = sorted({c["checked_at"] for c in per_citation if c["checked_at"]})
            record_out: dict[str, Any] = {
                # The paragraph aggregate is over its sentences, each of which
                # aggregates its own citations: a sentence one window carries is
                # ``yes`` even when the other sentences' windows do not mention it.
                "verdict": _aggregate_verdicts(sentence_verdicts) if len(units) > 1 else _aggregate_verdicts(verdicts),
                "contradicted": _contradicted(verdicts),
                "reasoning": _aggregate_reasoning(verdicts, per_citation),
                "model": ", ".join(models),
                "checked_at": checked_times[-1] if checked_times else "",
                "prompt_version": ENTAILMENT_PROMPT_VERSION,
                "citations": per_citation,
            }
            if len(units) > 1:
                record_out["sentences"] = sentences_out
            meta = dict(node.get("meta") or {})
            prov = dict(meta.get("provenance") or {})
            prov["entailment"] = record_out
            meta["provenance"] = prov
            node["meta"] = meta

    return document


__all__ = [
    "ENTAILMENT_PROMPT_VERSION",
    "EntailmentChecker",
    "VERDICTS",
    "attach_entailment_to_tree",
    "build_entailment_prompt",
    "check_entailment",
    "enforce_contradiction_evidence",
    "parse_entailment_verdict",
    "unverified",
]
