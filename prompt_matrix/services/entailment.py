"""Adversarial claim entailment for anchored paragraphs.

``models/jdf.py:attach_substrate_provenance_to_tree`` anchors a paragraph to a
*run of consecutive substrate sentences* by *wording* similarity (token overlap +
numeric matching) and says so in its own TODO. This module adds the missing step:
does the anchored evidence actually *entail* the claim the paragraph makes? It is
given the same run the matcher scored (the provenance row's ``anchor_window``),
not one sentence quoted out of it — a claim the source states across two sentences
is not judged against half of them.

Verdicts (frozen contract, persisted at ``node.meta.provenance.entailment``):

    {"verdict": "yes" | "no" | "partial" | "unverified",
     "reasoning": "<one sentence>",
     "model": "qwen/qwen3-next-80b-a3b-instruct",
     "checked_at": "<ISO8601>"}

``yes`` is the only verdict that means verified. ``no``/``partial`` are real
judgements; ``unverified`` is the visible failure of a call that could not be
made or could not be parsed — never a silent pass, and never a fallback to the
lexical anchor alone.

One model call per anchored paragraph, reused within a compile and — through
``services/entailment_cache``, keyed on the claim, the evidence window, the
composed prompt, the model and the pipeline version — across compiles too, so a
re-stated paragraph costs no second judgement. A failed call is never cached.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any, Callable

try:
    from ..cost_governance import CostGovernor, TaskType
    from .entailment_cache import load_verdict, store_verdict, verdict_cache_key
except ImportError:
    from cost_governance import CostGovernor, TaskType
    from entailment_cache import load_verdict, store_verdict, verdict_cache_key

_log = logging.getLogger(__name__)

VERDICTS = ("yes", "no", "partial", "unverified")

#: ``checker(claim, source) -> entailment record``. Injected so callers (routes,
#: tests) can swap the transport without touching the prompt or the persistence.
EntailmentChecker = Callable[[str, str], dict[str, Any]]

_MAX_REASON_CHARS = 240
_SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{6,}|Bearer\s+\S+)", re.IGNORECASE)
_VERDICT_RE = re.compile(r"verdict\s*[:\-]\s*\**\s*(yes|no|partial)\b", re.IGNORECASE)
_BARE_VERDICT_RE = re.compile(r"\b(yes|no|partial)\b", re.IGNORECASE)
_REASON_RE = re.compile(r"reason\s*[:\-]\s*(.+)", re.IGNORECASE | re.DOTALL)

_PROMPT = """\
You are an adversarial claim-entailment auditor. SOURCE is a verbatim extract \
from a document the author cited. CLAIM is a sentence the author wrote and \
attributed to that document.

Decide whether the SOURCE supports the CLAIM. Do not be charitable. Wording that \
merely overlaps is not support: ask what the SOURCE actually asserts. Do not \
assume facts the SOURCE does not state, and do not give the CLAIM the benefit of \
the doubt about numbers, parties, obligations, direction, negation, modality, or \
scope. A paraphrase that preserves the substance of the source — numbers, \
entities, modifiers — is supported. Restatement using different words is not a \
gap. Supporting detail that names the entities the user's question asked about \
is supported, not partial.

Apply these rules in priority order and stop at the first that matches:
1. no — the SOURCE contradicts a material element of the CLAIM.
2. no — the CLAIM states a number, date, percentage, or amount that the SOURCE \
does not contain. A figure the SOURCE lacks is fabricated, never partial.
3. partial — the SOURCE supports the CLAIM's central assertion, but the CLAIM \
also asserts another material element (party, obligation, scope, or modality) \
that the SOURCE neither states nor contradicts.
4. yes — the SOURCE states or directly entails every material element of the CLAIM.

If the SOURCE is unrelated to the CLAIM, answer no.

Answer with exactly these two lines and nothing else:
VERDICT: yes|no|partial
REASON: one sentence

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


def _record(verdict: str, reasoning: str, model: str) -> dict[str, Any]:
    return {
        "verdict": verdict,
        "reasoning": _sanitize(reasoning),
        "model": model,
        "checked_at": datetime.now(UTC).isoformat(),
    }


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
    reason_match = _REASON_RE.search(text)
    if reason_match is not None:
        reasoning = reason_match.group(1)
    else:
        reasoning = text[match.end() :]
    return _record(verdict, reasoning or "(no reasoning given)", model)


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
    cache_key = verdict_cache_key(claim, source, prompt, model_id) if model_id else ""
    cached = load_verdict(cache_key)
    if cached is not None:
        _log.info("[entailment-cache] hit %s", cache_key)
        return cached

    messages = [{"role": "user", "content": prompt}]
    try:
        policy = gov.preflight(project_id or "entailment", TaskType.SEMANTIC_VALIDATION, messages)
        executor = gov.executor or gov._default_executor  # noqa: SLF001
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

    record = parse_entailment_verdict(raw, policy.model_id)
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
    """Per-citation verdicts -> the paragraph's verdict.

    A synthesis paragraph cites several sentences and each carries part of it, so
    a strict entailment reader answers ``no`` to a citation that covers one clause
    of a four-clause claim. Judging the whole SET in one call hides that: the
    joined evidence is compared against the whole paragraph and ``no`` comes back
    for the set, which is what shipped for a while and made a real two-policy
    renewal memo read ``anchored 4, supported 0, unsupported 4`` - every
    paragraph judged contradicted. Counting ``no`` alone also reports a summary as
    a contradiction, which it is not.

    Per citation the mixture is visible: all citations support -> ``supported``;
    all fail -> ``unsupported``; support mixed with failure -> ``partial``,
    because the paragraph is carried by some of what it cites.
    """
    if not verdicts:
        return "unverified"
    if all(verdict == "yes" for verdict in verdicts):
        return "yes"
    if all(verdict == "no" for verdict in verdicts):
        return "no"
    if any(verdict in ("yes", "partial") for verdict in verdicts):
        return "partial"
    return "unverified"


def attach_entailment_to_tree(
    document: dict[str, Any],
    *,
    project_id: str = "",
    checker: EntailmentChecker | None = None,
    cache: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Entailment-verify every anchored paragraph in ``document``, in place.

    Runs after lexical anchoring (``models/jdf.py``) and writes the verdict at
    ``node.meta.provenance.entailment``. Pass ``cache`` to share verdicts within
    one compile; a verdict for the same (claim, evidence) already judged under the
    same prompt and model is reused from ``entailment_cache`` across compiles, so a
    compile that re-states a paragraph costs no model call for it.
    """
    if not document:
        return document
    check = checker or check_entailment
    seen = cache if cache is not None else {}

    for section in document.get("body") or []:
        if not isinstance(section, dict):
            continue
        for node in [section, *(section.get("children") or [])]:
            if not isinstance(node, dict) or str(node.get("type") or "") != "paragraph":
                continue
            claim = str(node.get("content") or "").strip()
            sources = _claim_sources(node)
            if not claim or not sources:
                # Anchored nodes always carry a quote. A node that does not is not
                # checked and gets no verdict — the gate counts it as unanchored.
                continue
            # One entailment call per citation, then aggregate. The joined-evidence
            # form is wrong for a summary: no single sentence states every element
            # of a paragraph that aggregates a dozen, and neither does the set when
            # the reader is strict, so every paragraph landed ``no``.
            per_citation: list[dict[str, Any]] = []
            verdicts: list[str] = []
            for source in sources:
                key = (claim, source)
                record = seen.get(key)
                if record is None:
                    try:
                        record = check(claim, source)
                    except Exception as exc:
                        record = unverified(f"{type(exc).__name__}: {exc}")
                    if not isinstance(record, dict) or record.get("verdict") not in VERDICTS:
                        record = unverified(f"checker returned no usable verdict: {record!r}")
                    seen[key] = record
                verdict = str(record.get("verdict") or "unverified")
                verdicts.append(verdict)
                per_citation.append(
                    {
                        "source": source,
                        "verdict": verdict,
                        "reasoning": str(record.get("reasoning") or ""),
                    }
                )
            record_out: dict[str, Any] = {
                "verdict": _aggregate_verdicts(verdicts),
                "reasoning": ", ".join(sorted(set(verdicts))) + f" over {len(verdicts)} citation(s)",
                "citations": per_citation,
            }
            meta = dict(node.get("meta") or {})
            prov = dict(meta.get("provenance") or {})
            prov["entailment"] = record_out
            meta["provenance"] = prov
            node["meta"] = meta

    return document


__all__ = [
    "EntailmentChecker",
    "VERDICTS",
    "attach_entailment_to_tree",
    "build_entailment_prompt",
    "check_entailment",
    "parse_entailment_verdict",
    "unverified",
]
