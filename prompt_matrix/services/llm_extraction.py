"""Grounded LLM fallback for the Parsure fields the label pass did not find.

The label-anchored regex in ``field_extractor`` reads ``Label: value`` lines.
Prose documents (a deed's "for consideration of Five Hundred ... ($585,000.00)
paid", a loss notice written as a letter) carry the same facts without the
labels, and the label pass leaves those fields empty. This module asks the
configured model where each missing value is — and then refuses to believe
it until the text agrees.

The grounding rule (spec §4 "no fabricated confidence", §8 item 6 "no
hallucination"), applied to every candidate the model returns:

1. the candidate's ``quote`` must occur verbatim in one page's text
   (whitespace collapsed, case-insensitive) — that gives the page and the
   character range;
2. the candidate's ``value`` must occur inside that quote — verbatim for
   text/name fields, and for money/number/date/VIN after the same
   normalisation ``field_extractor`` applies to a label hit (``$1,486.00
   annual`` and ``1486`` are the same money; ``03/01/2025`` and ``March 1,
   2025`` the same date).

What is stored is the document's own characters at the located range, never
the model's string; the model only supplies the place to look. A candidate
that fails either check leaves the field exactly as the label pass did:
``value=None``, ``extraction_confidence=0.0``, ``review_required=True``,
``reason="field not found"``. The model therefore cannot introduce a value —
it can only miss one.

Accepted fields carry ``extraction_method="llm_grounded"`` and their
confidence is multiplied by ``field_extractor.LLM_GROUNDED_FACTOR`` (0.85,
written into ``confidence_basis``). Compliance-bound fields still route by
the 3-rule policy exactly as label hits do.

Bounded: one model call per document (per chunk when the page texts exceed
the ``TaskType.FIELD_EXTRACTION`` input cap), a hard wall-clock timeout,
``PARSURE_LLM_EXTRACTION=0`` disables it, and every failure (no backend, no
key, connection refused, timeout, unparsable JSON) is a note in
``extraction_notes`` — never an exception into the ingest pipeline. The model
is whatever ``cost_governance`` resolves for the task: the OpenRouter policy
in production, ``ollama/qwen2.5:1.5b`` on ``ASSURE_LLM_BACKEND=ollama``.

Measured on ``tests/golden/prose`` (7 documents, 68 fields) with the local
qwen2.5:1.5b, 2026-09-25, six runs: label pass alone 30.9%; label pass +
this fallback 72–81% (the small model is not deterministic run to run);
1–6 s of model time per document, 16–22 s for the set. Every residual
error was a *miss* or a value that exists in the text but belongs to a
neighbouring field (the model quoted the property-damage liability sentence
for ``liability_limit``) — grounding proves the value is on the page, not
that the model read the right sentence, which is why the 0.85 factor keeps
grounded fields under the 0.75 auto-accept line at default parser
confidence (0.85 × 1.0 × 0.85 = 0.72) and in manual review. Current
numbers: ``scripts/validate_golden_set.py --prose`` →
``tests/golden/last_run_prose.json``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Any, Callable

try:
    from . import field_extractor as fx
except ImportError:
    import field_extractor as fx  # type: ignore

log = logging.getLogger(__name__)

#: Default hard wall-clock bound for one model call (seconds). qwen2.5:1.5b
#: on the compose Ollama answers the golden prose documents in 4–20 s
#: (last_run_prose.json); the cloud policy in well under 10 s.
DEFAULT_TIMEOUT_S = 90
#: Tokens reserved for the instructions and the field list in each prompt.
PROMPT_OVERHEAD_TOKENS = 700
#: Notes kept per document so a chatty rejection list cannot bloat the report.
MAX_NOTES = 24
#: Re-ask once when the answer is not a JSON object — the same two-passes
#: bound as ``cost_governance.MAX_RETRIES``. qwen2.5:1.5b returned prose or a
#: truncated object on 1 of 7 golden documents per run (2026-09-25); a
#: second pass usually parses. So at most two calls per chunk, never more.
JSON_RETRIES = 1

Completion = Callable[[str], str]


class LLMUnavailable(Exception):
    """The model could not be called or did not answer usably; the caller notes it and moves on."""


def llm_extraction_enabled() -> bool:
    """``PARSURE_LLM_EXTRACTION`` — on unless set to ``0``/``false``/``no``/``off``."""
    raw = os.environ.get("PARSURE_LLM_EXTRACTION", "").strip().lower()
    return raw not in ("0", "false", "no", "off")


# --------------------------------------------------------------------------
# Grounding
# --------------------------------------------------------------------------

def _collapse_with_map(text: str) -> tuple[str, list[int]]:
    """Lower-cased text with runs of whitespace folded to one space, plus the
    original offset of every kept character (so a match in the collapsed
    string maps back to a range in the page)."""
    out: list[str] = []
    offsets: list[int] = []
    prev_space = True
    for i, ch in enumerate(text):
        if ch.isspace():
            if prev_space:
                continue
            out.append(" ")
            offsets.append(i)
            prev_space = True
        else:
            out.append(ch.lower())
            offsets.append(i)
            prev_space = False
    if out and out[-1] == " ":
        out.pop()
        offsets.pop()
    return "".join(out), offsets


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def find_verbatim(page_text: str, needle: str) -> tuple[int, int] | None:
    """``(start, end)`` in ``page_text`` of ``needle`` under the grounding
    comparison (whitespace-collapsed, case-insensitive), or None."""
    needle_c = _collapse(needle)
    if not needle_c:
        return None
    hay, offsets = _collapse_with_map(page_text or "")
    pos = hay.find(needle_c)
    if pos < 0:
        return None
    return offsets[pos], offsets[pos + len(needle_c) - 1] + 1


def _locate_value(spec: fx.FieldSpec, quote_text: str, model_value: Any) -> tuple[int, int] | None:
    """Where inside ``quote_text`` the model's value is, by the field's type;
    None when it is not there (the rejection that makes this pass safe)."""
    if model_value is None:
        return None
    value_str = str(model_value).strip()
    if not value_str:
        return None
    ftype = spec.field_type
    if ftype == "money":
        want = fx._parse_money(value_str)
        if want is None:
            return None
        for m in re.finditer(fx._MONEY_RE, quote_text):
            got = fx._parse_money(m.group(0))
            if got is not None and abs(got - want) < 0.005:
                return m.span()
        return None
    if ftype == "number":
        want = fx._parse_number(value_str)
        if want is None:
            return None
        for m in re.finditer(fx._NUMBER_RE, quote_text):
            got = fx._parse_number(m.group(0))
            if got is not None and abs(got - want) < 1e-9:
                end = m.end()
                while end > m.start() and quote_text[end - 1].isspace():
                    end -= 1
                return m.start(), end
        return None
    if ftype == "date":
        want = fx._parse_date(value_str)
        if want is None:
            return None
        for m in re.finditer(fx._DATE_RE, quote_text, re.I):
            if fx._parse_date(m.group(0)) == want:
                return m.span()
        return None
    if ftype == "vin":
        want = re.sub(r"[\s\-]", "", value_str).upper()
        if len(want) != 17:
            return None
        for m in re.finditer(fx._VIN_RE, quote_text):
            after = quote_text[m.end(): m.end() + 1]
            if m.group(0).upper() == want and not re.match(r"[A-Za-z0-9]", after):
                return m.span()
        return None
    # text / name: the value itself must be in the quote, verbatim.
    span = find_verbatim(quote_text, value_str)
    if span is None:
        return None
    raw = quote_text[span[0]:span[1]]
    if ftype == "name" and (len(raw) > 80 or re.search(r"\d{3,}", raw)):
        return None
    if not raw.strip() or re.fullmatch(r"[_\s.]*", raw):
        return None
    return span


def ground_candidate(spec: fx.FieldSpec, page_texts: list[str], quote: Any, value: Any) -> tuple[int, str, int, int] | None:
    """Apply the grounding rule. ``(page_index, raw, start, end)`` of the value
    as the *document* spells it, or None when the quote is not in any page or
    the value is not in the quote."""
    if not isinstance(quote, str) or not _collapse(quote):
        return None
    for page_index, text in enumerate(page_texts):
        if not text:
            continue
        hit = find_verbatim(text, quote)
        if hit is None:
            continue
        qs, qe = hit
        quote_text = text[qs:qe]
        inner = _locate_value(spec, quote_text, value)
        if inner is None:
            return None
        vs, ve = qs + inner[0], qs + inner[1]
        raw = text[vs:ve]
        if spec.field_type in ("text", "name"):
            raw = re.sub(r"\s+", " ", raw).strip(" :;,-–—")
        elif spec.field_type == "vin":
            raw = raw.upper()
        else:
            raw = raw.strip()
        return page_index, raw, vs, ve
    return None


# --------------------------------------------------------------------------
# Prompt, chunking, response parsing
# --------------------------------------------------------------------------

_TYPE_HINT = {
    "money": "a money amount as written (e.g. $1,486.00)",
    "number": "a number as written (e.g. 6.125%)",
    "date": "a date as written (e.g. 03/01/2025 or March 1, 2025)",
    "vin": "the 17-character vehicle identification number",
    "name": "a person's or company's name",
    "text": "a short text value",
}


def build_prompt(document_type: str, specs: list[fx.FieldSpec], pages: list[tuple[int, str]]) -> str:
    """The extraction prompt: page-tagged text, the field list with type hints,
    and the strict-JSON answer shape. The instruction insists on verbatim
    quotes because the grounding step will discard anything paraphrased."""
    lines = [
        f"You are reading a {document_type.replace('_', ' ')} document. For each field below, find the value in the document text.",
        "Answer with ONE JSON object and nothing else. Keys are the field names. Each value is either null (not in the text)",
        'or an object {"quote": "<an exact, verbatim sentence or fragment copied from the document that contains the value>",',
        '"value": "<the value exactly as it appears inside that quote>", "page": <page number>}.',
        "Copy quotes character for character from the text. Do not invent, infer, normalise or compute values.",
        "If a field is not stated in the text, use null.",
        "",
        "Fields:",
    ]
    for spec in specs:
        lines.append(f'- "{spec.name}": {spec.label} — {_TYPE_HINT.get(spec.field_type, "a short text value")}')
    lines.append("")
    lines.append("Document text:")
    for page_no, text in pages:
        lines.append(f"=== PAGE {page_no} ===")
        lines.append(text.strip())
    lines.append("=== END ===")
    lines.append("")
    lines.append("JSON:")
    return "\n".join(lines)


def _count_tokens(text: str) -> int:
    try:
        try:
            from ..token_counter import count_tokens  # type: ignore
        except ImportError:
            from token_counter import count_tokens  # type: ignore
        return int(count_tokens(text))
    except Exception:
        return max(1, len(text) // 4)


def _input_cap() -> int:
    try:
        try:
            from ..cost_governance import MAX_INPUT_TOKENS, TaskType  # type: ignore
        except ImportError:
            from cost_governance import MAX_INPUT_TOKENS, TaskType  # type: ignore
        return int(MAX_INPUT_TOKENS[TaskType.FIELD_EXTRACTION])
    except Exception:
        return 6000


def chunk_pages(page_texts: list[str], budget_tokens: int) -> list[list[tuple[int, str]]]:
    """Group whole pages (1-based numbers kept) under ``budget_tokens``; a
    single page over budget is split at paragraph breaks, then lines, so no
    chunk exceeds it. Empty pages are dropped (a blanked page in a mixed-bundle
    segment is not evidence)."""
    budget = max(200, budget_tokens)
    pieces: list[tuple[int, str]] = []
    for i, text in enumerate(page_texts):
        text = (text or "").strip()
        if not text:
            continue
        if _count_tokens(text) <= budget:
            pieces.append((i + 1, text))
            continue
        parts = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
        if len(parts) == 1:
            parts = [p for p in text.split("\n") if p.strip()]
        buf: list[str] = []
        for part in parts:
            candidate = "\n\n".join(buf + [part])
            if buf and _count_tokens(candidate) > budget:
                pieces.append((i + 1, "\n\n".join(buf)))
                buf = [part]
            else:
                buf.append(part)
        if buf:
            pieces.append((i + 1, "\n\n".join(buf)))
    chunks: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    current_tokens = 0
    for page_no, text in pieces:
        n = _count_tokens(text)
        if current and current_tokens + n > budget:
            chunks.append(current)
            current, current_tokens = [], 0
        current.append((page_no, text))
        current_tokens += n
    if current:
        chunks.append(current)
    return chunks


def _as_object(parsed: Any) -> dict[str, Any] | None:
    """A dict as-is; a list of dicts merged into one (qwen2.5:1.5b wraps the
    answer in ``[ { … } ]`` and sometimes splits the fields over several
    objects — 1 of 7 golden documents per run, 2026-09-25); anything else None."""
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, list):
        merged: dict[str, Any] = {}
        for item in parsed:
            if isinstance(item, dict):
                merged.update(item)
        return merged or None
    return None


def parse_json_answer(text: str) -> dict[str, Any] | None:
    """The JSON object in a model answer (code fences, prose around it and a
    list wrapper tolerated), or None when there is none."""
    if not isinstance(text, str) or not text.strip():
        return None
    body = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I | re.M)
    candidates = [body]
    start, end = body.find("["), body.rfind("]")
    if 0 <= start < end:
        candidates.append(body[start:end + 1])
    start, end = body.find("{"), body.rfind("}")
    if 0 <= start < end:
        candidates.append(body[start:end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        obj = _as_object(parsed)
        if obj is not None:
            return obj
    return None


# --------------------------------------------------------------------------
# Model call — the app's own path, with a hard wall-clock bound
# --------------------------------------------------------------------------

def _governor():
    try:
        from .. import cost_governance as cg  # type: ignore
    except ImportError:
        import cost_governance as cg  # type: ignore
    return cg


def default_completion(prompt: str, *, project_id: str | None = None) -> str:
    """One call through ``cost_governance``: the ``FIELD_EXTRACTION`` policy's
    model (resolved for the backend) via ``CostGovernor._default_executor``,
    the same executor entailment and Red-Hat use. Usage is recorded against
    ``project_id`` when one is given and the ledger is reachable; accounting
    never changes the answer. Raises ``LLMUnavailable`` when the executor
    reports an error (it returns ``"ERROR: …"`` rather than raising)."""
    cg = _governor()
    gov = cg.CostGovernor()
    policy = gov.policy_for(cg.TaskType.FIELD_EXTRACTION)
    model = policy.litellm_model or policy.model_id
    executor = gov.executor or gov._default_executor  # noqa: SLF001
    messages = [{"role": "user", "content": prompt}]
    started = time.monotonic()
    text, in_tok, out_tok = executor(model, messages, policy.max_output_tokens, policy.caching)
    elapsed = time.monotonic() - started
    log.info("llm_extraction: model=%s in=%s out=%s elapsed=%.1fs", model, in_tok, out_tok, elapsed)
    if not isinstance(text, str) or text.startswith("ERROR:"):
        raise LLMUnavailable(f"{model}: {str(text)[7:].strip()[:200] or 'empty answer'}")
    if project_id:
        try:
            gov.record_usage(project_id, input_tokens=int(in_tok or 0), output_tokens=int(out_tok or 0),
                             model_id=policy.model_id, task_type=cg.TaskType.FIELD_EXTRACTION, meta={"pipeline": "parsure_field_extraction"})
        except Exception:
            pass
    return text


def current_model_id() -> str:
    try:
        cg = _governor()
        policy = cg.CostGovernor().policy_for(cg.TaskType.FIELD_EXTRACTION)
        return policy.litellm_model or policy.model_id
    except Exception:
        return "unknown"


def _call_with_timeout(fn: Callable[[], str], timeout_s: float) -> str:
    """Run ``fn`` on a daemon thread and wait at most ``timeout_s``. The
    executor has its own litellm timeout (PEM_TIMEOUT_SECONDS, clamped to
    80 s), but an injected or misbehaving completion must not stall the
    ingest worker; a thread left behind after a timeout finishes on its own
    and its answer is discarded."""
    box: dict[str, Any] = {}

    def _run() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 — reported to the caller as unavailable
            box["error"] = exc

    t = threading.Thread(target=_run, name="parsure-llm-extraction", daemon=True)
    t.start()
    t.join(max(0.0, float(timeout_s)))
    if t.is_alive():
        raise LLMUnavailable(f"timed out after {timeout_s:g}s")
    if "error" in box:
        exc = box["error"]
        if isinstance(exc, LLMUnavailable):
            raise exc
        raise LLMUnavailable(f"{type(exc).__name__}: {exc}") from exc
    return str(box.get("value") or "")


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def extract_missing_fields(
    document_type: str,
    page_texts: list[str],
    missing_specs: list[fx.FieldSpec],
    *,
    completion: Completion | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    notes: list[str] | None = None,
    parser_name: str | None = None,
    parse_confidence: float | None = None,
    ocr_confidence: float | None = None,
    page_quality: list[float | None] | None = None,
    visual_pages: list[dict] | None = None,
    layout: list[list[dict]] | None = None,
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    """Grounded field records for ``missing_specs``, one per spec, in order.

    A spec whose candidate passes the grounding rule comes back as a full
    contract record built by ``field_extractor.build_found_field(method=
    "llm_grounded")``; every other spec comes back as the label pass's empty
    record. ``notes`` (a list the caller owns) receives one line per skip
    or rejection — ``"llm extraction skipped: <reason>"`` when the model was
    not consulted at all. ``completion`` is ``(prompt) -> answer text``; the
    default is ``default_completion``. The signature field is never offered:
    ink presence is a quality-probe question a text model cannot answer.

    Never raises: any failure is a note and the fields stay empty.
    """
    notes_out: list[str] = notes if notes is not None else []

    def note(msg: str) -> None:
        if len(notes_out) < MAX_NOTES:
            notes_out.append(msg)

    specs = [s for s in (missing_specs or []) if s.field_type != "signature"]
    results: dict[str, dict[str, Any]] = {s.name: fx._empty_field(s) for s in (missing_specs or [])}
    ordered = lambda: [results[s.name] for s in (missing_specs or [])]  # noqa: E731
    if not specs:
        return ordered()
    if not llm_extraction_enabled():
        note("llm extraction skipped: PARSURE_LLM_EXTRACTION is off")
        return ordered()
    texts = [t or "" for t in (page_texts or [])]
    if not any(t.strip() for t in texts):
        note("llm extraction skipped: no page text")
        return ordered()

    try:
        model_id = current_model_id() if completion is None else "injected"
        budget = _input_cap() - PROMPT_OVERHEAD_TOKENS
        chunks = chunk_pages(texts, budget)
        if not chunks:
            note("llm extraction skipped: no page text")
            return ordered()
        call = completion if completion is not None else (lambda p: default_completion(p, project_id=project_id))
        pending = list(specs)
        accepted = rejected = calls = 0
        skipped = False
        started = time.monotonic()
        for chunk in chunks:
            if not pending:
                break
            prompt = build_prompt(document_type, pending, chunk)
            data = None
            for attempt in range(JSON_RETRIES + 1):
                calls += 1
                try:
                    answer = _call_with_timeout(lambda: call(prompt), timeout_s)
                except LLMUnavailable as exc:
                    note(f"llm extraction skipped: {exc}")
                    skipped = True
                    break
                data = parse_json_answer(answer)
                if data is not None:
                    break
                if attempt < JSON_RETRIES:
                    note("llm answer was not a JSON object; asked once more")
            if skipped:
                break
            if data is None:
                head = re.sub(r"\s+", " ", str(answer))[:80]
                log.warning("llm_extraction: unparsable answer from %s: %r", model_id, str(answer)[:400])
                note(f"llm extraction skipped: model answer was not a JSON object (starts: {head!r})")
                skipped = True
                break
            still_pending: list[fx.FieldSpec] = []
            for spec in pending:
                cand = data.get(spec.name)
                if cand is None:
                    still_pending.append(spec)
                    continue
                if isinstance(cand, dict):
                    quote, value = cand.get("quote"), cand.get("value")
                elif isinstance(cand, (str, int, float)):
                    quote, value = str(cand), cand
                else:
                    still_pending.append(spec)
                    continue
                if isinstance(quote, (int, float)):
                    quote = str(quote)
                grounded = ground_candidate(spec, texts, quote, value)
                if grounded is None:
                    rejected += 1
                    why = "quote not found verbatim in the text" if not (isinstance(quote, str) and any(find_verbatim(t, quote) for t in texts if t)) else "value not inside the quoted text"
                    note(f"llm candidate rejected for {spec.name}: {why}")
                    still_pending.append(spec)
                    continue
                page_index, raw, start, end = grounded
                field = fx.build_found_field(
                    spec, page_index=page_index, raw=raw, start=start, end=end, parser_name=parser_name,
                    parse_confidence=parse_confidence, ocr_confidence=ocr_confidence, page_quality=list(page_quality or []),
                    visual_pages=list(visual_pages or []), layout=list(layout or []), method="llm_grounded",
                )
                if field.get("value") is None:
                    still_pending.append(spec)
                    continue
                qs = find_verbatim(texts[page_index], quote) if isinstance(quote, str) else None
                field["grounding"] = {
                    "quote": texts[page_index][qs[0]:qs[1]][:240] if qs else None,
                    "model_value": str(value)[:120],
                    "page": page_index + 1,
                }
                results[spec.name] = field
                accepted += 1
            pending = still_pending
        elapsed = time.monotonic() - started
        if not skipped:
            note(f"llm extraction: model {model_id}, {calls} call(s), {accepted} field(s) grounded, {rejected} candidate(s) rejected, {elapsed:.1f}s")
        log.info("llm_extraction: %s accepted=%d rejected=%d calls=%d elapsed=%.1fs", document_type, accepted, rejected, calls, elapsed)
    except Exception as exc:  # noqa: BLE001 — the pipeline treats this pass as advisory
        log.exception("llm_extraction failed; fields left empty")
        note(f"llm extraction skipped: {type(exc).__name__}: {exc}")
    return ordered()
