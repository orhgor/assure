"""Dynamic field discovery for documents no schema fits (plan Part 4.2, 2026-09-27).

A ``<family>_unknown`` or ``uncertain`` document has no taxonomy fields, so the
report used to end at ``fields: []`` — the customer read that as a dead end
("document_type = uncertain, fields = {}", 2026-09-27). This pass lists the
``Label: value`` pairs that are actually on the page as
``report["discovered_fields"]``. They are **not** taxonomy fields: they carry
no field state, no routing action and no confidence score, they are not
counted in ``review_summary`` or ``fields_total``, and ``taxonomy_field`` is
``false`` on each — so the honest count of schema fields found stays zero
while the reviewer still sees what the page says.

Two sources, both grounded in the page text:

* ``heuristic_label_value`` — a regex over each line: a short label (≤ 48
  characters, starts with a letter), a colon, a value on the same line.
  Values that are themselves labels, blanks or underscores are skipped. The
  span is located through the layout (``field_extractor.page_layout``) so
  the pair carries the node/element it sits on.
* ``llm_grounded_discovery`` — only when ``PARSURE_LLM_EXTRACTION`` is on and
  the caller allows a model call: the model is asked for label/value pairs
  with a verbatim quote, and a pair is kept only when the quote is found in
  a page (``llm_extraction.find_verbatim``) and the value is found inside
  that quote — the same rule the grounded field pass applies. The stored
  value is the document's own characters at the located range.

Bounded: ``MAX_DISCOVERED`` pairs per document, one model call, the module's
timeout; every failure is a note and an ``execution.discovery`` status, never
an exception into the report build.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Callable

try:
    from . import field_extractor as fx
    from . import llm_extraction as lx
    from . import table_extraction as te
except ImportError:  # pragma: no cover - flat-import fallback
    import field_extractor as fx  # type: ignore
    import llm_extraction as lx  # type: ignore
    import table_extraction as te  # type: ignore

log = logging.getLogger(__name__)

MAX_DISCOVERED = 40
MAX_LABEL_WORDS = 6
#: Characters of page text offered to the model (first pages first).
DISCOVERY_TEXT_CHARS = 6000
DISCOVERY_TIMEOUT_S = 45.0
DISCOVERY_METHODS = ("heuristic_label_value", "llm_grounded_discovery")

_PAIR_RE = re.compile(
    r"^[ \t]*(?P<label>[A-Za-z][A-Za-z0-9 .'/&()#\-]{1,48}?)[ \t]*:[ \t]+(?P<value>\S[^\n]{0,159}?)[ \t]*$",
    re.M,
)
_BLANK_VALUE_RE = re.compile(r"^[_\s.\-–—:]*$")


def applies_to(document_type: Any) -> bool:
    """Discovery runs for the types that have no schema: ``uncertain`` and
    ``<family>_unknown``."""
    name = str(document_type or "")
    if name in ("uncertain", "unknown", "other", ""):
        return True
    return name.endswith("_unknown") and name[: -len("_unknown")] in fx.DOCUMENT_FAMILIES


def slug_name(label: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", str(label or "").lower()).strip("_")
    s = re.sub(r"_+", "_", s)
    return s[:64] or "field"


def _locate(layout: list[list[dict]] | None, page_index: int, start: int, end: int) -> dict[str, Any]:
    span: dict[str, Any] = {"page": page_index + 1, "span_type": "text_range", "start_char": start, "end_char": end, "node_id": None, "element_id": None}
    segments = (layout or [[]])[page_index] if layout and page_index < len(layout) else []
    seg = fx._segment_at(segments, start) if segments else None
    if seg:
        span["node_id"] = seg.get("node_id")
        span["element_id"] = seg.get("element_id")
        if seg.get("bbox"):
            span["span_type"] = "bbox_relative"
            span["bbox"] = seg["bbox"]
        rel_start = start - int(seg.get("start") or 0)
        el = fx._element_at(seg, rel_start)
        if el:
            span["element_id"] = el.get("element_id") or span["element_id"]
            if isinstance(el.get("bbox"), list) and len(el["bbox"]) == 4:
                span["span_type"], span["bbox"] = "bbox_relative", el["bbox"]
    return span


def _pair(name: str, label: str, value: str, span: dict[str, Any], method: str, **extra: Any) -> dict[str, Any]:
    return {"name": name, "label": label, "value": value, "page": span.get("page"), "span": span, "method": method,
            "taxonomy_field": False, "grounding_source": "text", **extra}


def discover_heuristic(texts: list[str], layout: list[list[dict]] | None = None, *, limit: int = MAX_DISCOVERED) -> list[dict[str, Any]]:
    """``Label: value`` pairs per page, first occurrence of each label wins."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page_index, text in enumerate(texts or []):
        for m in _PAIR_RE.finditer(text or ""):
            label = " ".join(m.group("label").split())
            value = m.group("value").strip()
            if len(label.split()) > MAX_LABEL_WORDS or _BLANK_VALUE_RE.match(value) or fx._LABEL_LIKE.match(value):
                continue
            if value.upper() == value and len(value.split()) <= 3 and value.rstrip(":").upper() in {w.upper() for w in fx._HEADER_WORDS}:
                continue
            name = slug_name(label)
            if name in seen:
                continue
            seen.add(name)
            out.append(_pair(name, label, value, _locate(layout, page_index, m.start("value"), m.end("value")), "heuristic_label_value"))
            if len(out) >= limit:
                return out
    return out


def discovery_prompt(texts: list[str]) -> str:
    budget = DISCOVERY_TEXT_CHARS
    parts: list[str] = []
    for i, text in enumerate(texts or []):
        t = (text or "").strip()
        if not t or budget <= 0:
            continue
        parts.append(f"=== PAGE {i + 1} ===\n{t[:budget]}")
        budget -= len(t[:budget])
    return "\n".join([
        "You are reading an insurance document whose type is not known. List the labelled facts the document states.",
        "Answer with ONE JSON array and nothing else. Each item is an object",
        '{"label": "<the label as written>", "value": "<the value exactly as written>", "quote": "<a verbatim fragment copied from the document that contains the value>", "page": <page number>}.',
        "Copy quotes and values character for character. Do not invent, infer, normalise or compute values. At most 40 items.",
        "",
        "Document text:",
        *parts,
        "=== END ===",
        "",
        "JSON:",
    ])


def _parse_items(answer: str) -> list[dict[str, Any]]:
    text = (answer or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.M)
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return []
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


def discover_with_model(
    texts: list[str],
    layout: list[list[dict]] | None = None,
    *,
    completion: Callable[[str], str] | None = None,
    project_id: str | None = None,
    notes: list[str] | None = None,
    timeout_s: float = DISCOVERY_TIMEOUT_S,
    limit: int = MAX_DISCOVERED,
    exclude: set[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Grounded model pass; ``(pairs, stats)``. Never raises."""
    notes_out = notes if notes is not None else []
    stats: dict[str, Any] = {"status": "skipped", "model": None, "candidates": 0, "grounded": 0, "rejected": 0, "ms": 0.0, "reason": None}
    if not lx.llm_extraction_enabled():
        stats["reason"] = "PARSURE_LLM_EXTRACTION is off"
        return [], stats
    pages = [t or "" for t in (texts or [])]
    if not any(p.strip() for p in pages):
        stats["reason"] = "no page text"
        return [], stats
    started = time.monotonic()
    try:
        model_id = lx.current_model_id() if completion is None else "injected"
        call = completion if completion is not None else (lambda p: lx.default_completion(p, project_id=project_id))
        answer = lx._call_with_timeout(lambda: call(discovery_prompt(pages)), timeout_s)
    except Exception as exc:  # noqa: BLE001 — advisory pass
        stats.update(status="failed", reason=f"{type(exc).__name__}: {exc}"[:200], ms=round((time.monotonic() - started) * 1000.0, 3))
        notes_out.append(f"field discovery (model) skipped: {stats['reason']}")
        return [], stats
    stats["model"] = model_id
    items = _parse_items(answer)
    stats["candidates"] = len(items)
    out: list[dict[str, Any]] = []
    seen = set(exclude or set())
    for item in items:
        label, value, quote = item.get("label"), item.get("value"), item.get("quote")
        if not (isinstance(label, str) and label.strip() and isinstance(value, str) and value.strip() and isinstance(quote, str) and quote.strip()):
            stats["rejected"] += 1
            continue
        located = None
        for page_index, text in enumerate(pages):
            hit = lx.find_verbatim(text, quote)
            if hit is None:
                continue
            qs, qe = hit
            inner = lx.find_verbatim(text[qs:qe], value)
            if inner is None:
                break
            located = (page_index, qs + inner[0], qs + inner[1])
            break
        if located is None:
            stats["rejected"] += 1
            continue
        name = slug_name(label)
        if name in seen:
            continue
        seen.add(name)
        page_index, vs, ve = located
        raw = pages[page_index][vs:ve].strip()
        out.append(_pair(name, " ".join(label.split()), raw, _locate(layout, page_index, vs, ve), "llm_grounded_discovery",
                         grounding_quote=quote.strip(), grounding_model=model_id))
        if len(out) >= limit:
            break
    stats.update(status="ran", grounded=len(out), ms=round((time.monotonic() - started) * 1000.0, 3))
    if not items:
        stats["reason"] = "model answer carried no JSON array"
    return out, stats


def run_discovery(
    document_type: Any,
    texts: list[str],
    layout: list[list[dict]] | None,
    *,
    completion: Callable[[str], str] | None = None,
    project_id: str | None = None,
    notes: list[str],
    llm: bool = True,
    execution: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """The hook ``v1_orchestrator.extract_segment_fields`` calls for a segment
    with no schema. Appends the pairs to the current ``extraction_scope`` (so
    ``build_report`` can put them on the report) and records
    ``execution["discovery"] = {status, pairs, heuristic, llm_grounded, model,
    reason}``. Returns the pairs. Never raises."""
    stats: dict[str, Any] = {"status": "not_run", "pairs": 0, "heuristic": 0, "llm_grounded": 0, "model": None, "reason": None}
    pairs: list[dict[str, Any]] = []
    try:
        if not applies_to(document_type):
            stats["reason"] = "typed document — taxonomy fields apply"
        else:
            pairs = discover_heuristic(texts, layout)
            stats["heuristic"] = len(pairs)
            if llm:
                more, model_stats = discover_with_model(texts, layout, completion=completion, project_id=project_id, notes=notes,
                                                        exclude={p["name"] for p in pairs}, limit=max(0, MAX_DISCOVERED - len(pairs)))
                pairs.extend(more)
                stats["llm_grounded"] = len(more)
                stats["model"] = model_stats.get("model")
                stats["model_status"] = model_stats.get("status")
                if model_stats.get("reason"):
                    stats["reason"] = f"model pass: {model_stats['reason']}"
            else:
                stats["model_status"] = "skipped"
                stats["reason"] = "model pass not run on this path (label pass only)"
            stats["status"] = "completed"
            stats["pairs"] = len(pairs)
            if pairs:
                notes.append(f"field discovery: {len(pairs)} label/value pair(s) listed for {document_type} ({stats['heuristic']} heuristic, {stats['llm_grounded']} model-grounded)")
    except Exception as exc:  # noqa: BLE001 — advisory pass
        log.exception("field discovery failed")
        stats.update(status="failed", reason=f"{type(exc).__name__}: {exc}"[:200])
        notes.append(f"field discovery failed: {stats['reason']}")
    scope = te.current_scope()
    if scope is not None:
        scope.discovered.extend(pairs)
        if stats["status"] != "not_run" or scope.discovery is None:
            scope.discovery = stats
    if execution is not None and (stats["status"] != "not_run" or "discovery" not in execution):
        execution["discovery"] = stats
    return pairs
