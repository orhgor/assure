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

One model call per anchored paragraph, cached only within a single compile.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Callable

try:
    from ..cost_governance import CostGovernor, TaskType
except ImportError:
    from cost_governance import CostGovernor, TaskType

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
You are an adversarial claim-entailment auditor. SOURCE is a verbatim sentence \
from a document the author cited. CLAIM is a sentence the author wrote and \
attributed to that document.

Decide whether the SOURCE supports the CLAIM. Do not be charitable. Wording that \
merely overlaps is not support: ask what the SOURCE actually asserts. Do not \
assume facts the SOURCE does not state, and do not give the CLAIM the benefit of \
the doubt about numbers, parties, obligations, direction, negation, modality, or \
scope.

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

    Never raises: every failure becomes an ``unverified`` record carrying the
    reason, so a broken call degrades the gate to "not verified" rather than
    falling back to the lexical anchor or (worse) a pass.
    """
    gov = CostGovernor()
    model_id = ""
    try:
        model_id = gov.policy_for(TaskType.SEMANTIC_VALIDATION).model_id
    except Exception:
        pass

    messages = [{"role": "user", "content": build_entailment_prompt(claim, source)}]
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

    return parse_entailment_verdict(raw, policy.model_id)


def _claim_source(node: dict[str, Any]) -> tuple[str, str]:
    """(claim, anchored source evidence) for a paragraph node.

    The evidence is the provenance row's ``anchor_window`` — the run of consecutive
    source sentences the matcher actually cleared its floors against — and only
    falls back to ``extracted_quote`` when the row predates the window (or the
    window was a single sentence, where the two are the same text). Checking the
    claim against one sentence of a two-sentence anchor asks the model to judge a
    claim against evidence the matcher never used, and it answers ``partial`` for
    the half the sentence does not carry: the paragraph is then reported as a
    weaker claim than the source it was anchored to.

    Never ``meta.provenance.excerpt``, which falls back to the claim text itself
    and would make the check a tautology.
    """
    claim = str(node.get("content") or "").strip()
    rows = node.get("provenance") or []
    source = ""
    for row in rows:
        if not isinstance(row, dict):
            continue
        window = str(row.get("anchor_window") or "").strip()
        quote = str(row.get("extracted_quote") or "").strip()
        if window or quote:
            source = window or quote
            break
    return claim, source


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
    one compile; verdicts are never reused across compiles.
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
            claim, source = _claim_source(node)
            if not claim or not source:
                # Anchored nodes always carry a quote. A node that does not is not
                # checked and gets no verdict — the gate counts it as unanchored.
                continue
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
            meta = dict(node.get("meta") or {})
            prov = dict(meta.get("provenance") or {})
            prov["entailment"] = dict(record)
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
