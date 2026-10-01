"""Raw candidates — the schema-agnostic facts layer (customer plan V4 Part 1, 2026-09-28).

Rule: the pipeline never depends on the document type to produce raw facts.
Facts first (this module), schema selection second (``v1_orchestrator.
settle_segment_type``), projection third (:func:`project_candidates`), remap
without loss (``v1_orchestrator.reextract_for_type`` re-projects the stored
pool before it re-reads a page). ``report["raw_candidates"]`` is built for
EVERY segment, typed or not — the delta against ``field_discovery.applies_to``,
which lists ``discovered_fields`` only for pages no schema fits.

Why (customer review of the site-report photo, 2026-09-28): the type was
resolved first and only that schema's fields were extracted, so a page whose
labels were legible reported ``fields 0/0`` and every downstream claim about it
was a manufactured "field not found". The raw pool keeps what the page says
regardless of what the classifier decided.

Sources — every readable source the pipeline already produced; the layer spends
no model call of its own:

* ``layout_text`` — the ``Label: value`` pair regexes of ``field_discovery``
  (``iter_pairs``) run over each layout element's own text (``field_extractor.
  page_layout``: jdf-cli elements, OCR lines, tree paragraphs), so the pair
  carries the element it sits on.
* ``discovery`` — the same regexes over the joined page text
  (``field_discovery.discover_heuristic``): pairs that span line breaks or sit
  where no layout element exists.
* ``table_cell`` — the cells of the tables ``services/table_extraction``
  collected (header + row label as the label).
* ``textract`` — Textract's ``KEY_VALUE_SET`` forms when the scan backend
  produced them (``bundle["forms"]``).
* ``image_vision`` — the facts ``services/vision`` extracted from page rasters
  when ``PARSURE_VISION`` is on. Model-derived, so **corroboration** applies: a
  fact whose value text is found verbatim on the stored page text
  (``llm_extraction.find_verbatim``, the rule ``field_discovery`` applies to the
  model's discovery pairs) keeps ``corroborated: true`` and outranks every
  deterministic source; one that is not is ``corroborated: false`` and ranks
  below all of them — a hallucinated read never outranks a real OCR read.

Each candidate carries **provenance only**: ``name_hint``, ``raw_text``
(verbatim), ``normalized_value`` (always None here — normalisation is the
schema layer's job), ``label_anchor``, ``page``, ``node_id``, ``element_id``,
``source_span``, ``source_kind``, ``source_priority``, ``corroborated``,
``preferred``, ``trace``, ``candidate_id``. No ``confidence`` (a decision
confidence without a value shape is the V1 "garbage looks perfect" bug) and no
``evidence_state`` (``FIELD_STATES`` / ``ROUTING_ACTIONS`` / ``EVIDENCE_STATES``
belong to mapped fields).

Merge and precedence (plan Part 1.2, exact):

* duplicates — same folded label (case/punctuation-insensitive) + same page +
  same folded value — collapse to the candidate that sorts first under
  :func:`sort_key`;
* overlaps — same/overlapping span, different text — both stay; the one that
  sorts first is ``preferred: true``;
* every ordering decision uses the one key ``(source_priority desc,
  corroborated desc, page asc, start_char asc, source_kind, raw_text)`` — two
  implementations of this file must produce byte-identical pools;
* no cap on deterministic candidates (:data:`MAX_PER_SEGMENT` is ``None`` since
  2026-09-29 — user decision: the fields of an unknown form are whatever the
  page says, and a CMS-1500 read by Textract yields 230 candidates; the plan's
  "~60" cap dropped 11 of them). A cap, if ever set, applies per segment;
* the pool is append-only: :func:`merge_pool` keeps every stored candidate
  as it is and only appends ones whose ``candidate_id`` is new. A rerun adds;
  it never rewrites or deletes.

Projection (plan Part 1.3, :func:`project_candidates`) maps candidates onto a
schema by matching each ``FieldSpec`` anchor against the candidate's label; a
candidate is never consumed — it may map into several fields and stays in the
pool either way. Outcomes are logged (``report["projection"]``) with the words
``mapped`` / ``unmapped`` / ``conflicting`` / ``review_needed``; the field
itself carries only the existing vocabulary (``review_needed`` is the label for
``unverified`` + ``manual_review`` below the confidence thresholds, never a
state).
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, Iterable, Iterator

try:
    from . import field_discovery as fd
    from . import field_extractor as fx
    from . import llm_extraction as lx
except ImportError:  # pragma: no cover - flat-import fallback
    import field_discovery as fd  # type: ignore
    import field_extractor as fx  # type: ignore
    import llm_extraction as lx  # type: ignore

log = logging.getLogger(__name__)

SOURCE_KINDS = ("model_read", "textract", "table_cell", "layout_text", "discovery", "image_vision")
#: Plan Part 1.2: ``textract > table_cell > layout_text > discovery > image_vision``
#: (uncorroborated); a corroborated vision read (verbatim on the page) outranks all.
SOURCE_PRIORITY: dict[str, int] = {"model_read": 5, "textract": 5, "table_cell": 4, "layout_text": 3, "discovery": 2, "image_vision": 1}
CORROBORATED_VISION_PRIORITY = 6
MAX_PER_SEGMENT: int | None = None
#: Label/value pairs the heuristic discovery offers the pool (its own bound).
DISCOVERY_LIMIT = 400
#: A candidate without a character span sorts after every spanned one on its page.
NO_SPAN = 10 ** 9
SORT_KEY = ("source_priority desc", "corroborated desc", "page asc", "start_char asc", "source_kind", "raw_text")
CANDIDATE_KEYS = ("candidate_id", "name_hint", "raw_text", "normalized_value", "label_anchor", "page", "node_id", "element_id",
                  "source_span", "source_kind", "source_priority", "corroborated", "preferred", "trace")
#: Words the projection log uses for a field's outcome (labels, never states).
PROJECTION_OUTCOMES = ("mapped", "unmapped", "conflicting", "review_needed")
#: ``extraction_method`` of a field the projection built from a candidate.
CANDIDATE_METHOD = "raw_candidate"
#: A cell projected from a table needs one value among the matching cells;
#: ``table_extraction`` owns the total-row / single-row judgement.
_MAX_LABEL_WORDS = 6


def fold(text: Any) -> str:
    """Case- and punctuation-insensitive form of a label or value: the
    alphanumeric runs, lower-cased, single-spaced."""
    return " ".join(re.findall(r"[a-z0-9]+", str(text or "").lower()))


def _start(c: dict[str, Any]) -> int:
    span = c.get("source_span") if isinstance(c.get("source_span"), dict) else {}
    s = span.get("start_char")
    return int(s) if isinstance(s, int) and not isinstance(s, bool) else NO_SPAN


def _end(c: dict[str, Any]) -> int:
    span = c.get("source_span") if isinstance(c.get("source_span"), dict) else {}
    e = span.get("end_char")
    return int(e) if isinstance(e, int) and not isinstance(e, bool) else NO_SPAN


def sort_key(c: dict[str, Any]) -> tuple:
    """The one ordering key (:data:`SORT_KEY`). Priority and corroboration
    descend, then page and start ascend, then the source kind and the raw text
    lexically — nothing is left to insertion order."""
    return (-int(c.get("source_priority") or 0), 0 if c.get("corroborated") else 1, int(c.get("page") or 0), _start(c),
            str(c.get("source_kind") or ""), str(c.get("raw_text") or ""))


def dedupe_key(c: dict[str, Any]) -> tuple[str, int, str]:
    return (fold(c.get("label_anchor")), int(c.get("page") or 0), fold(c.get("raw_text")))


def candidate_id(c: dict[str, Any]) -> str:
    """Content-derived id: the same fact from the same source at the same place
    is the same candidate on every run, so :func:`merge_pool` can append only
    what is new."""
    start = _start(c)
    key = f"{int(c.get('page') or 0)}|{c.get('source_kind')}|{fold(c.get('label_anchor'))}|{fold(c.get('raw_text'))}|{'' if start == NO_SPAN else start}"
    return "rc-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _candidate(*, name_hint: str, raw_text: str, label_anchor: str, page: int, span: dict[str, Any] | None, source_kind: str, trace: str,
               corroborated: bool = True, node_id: Any = None, element_id: Any = None) -> dict[str, Any]:
    priority = CORROBORATED_VISION_PRIORITY if (source_kind == "image_vision" and corroborated) else SOURCE_PRIORITY[source_kind]
    span_out: dict[str, Any] = {"span_type": "text_range", "start_char": None, "end_char": None}
    if isinstance(span, dict):
        span_out.update({k: v for k, v in span.items() if k not in ("page", "node_id", "element_id")})
    return {
        "candidate_id": None,
        "name_hint": fd.slug_name(name_hint),
        "raw_text": str(raw_text),
        "normalized_value": None,
        "label_anchor": " ".join(str(label_anchor).split()),
        "page": int(page),
        "node_id": str(node_id) if node_id is not None else None,
        "element_id": str(element_id) if element_id is not None else None,
        "source_span": span_out,
        "source_kind": source_kind,
        "source_priority": priority,
        "corroborated": bool(corroborated),
        "preferred": True,
        "trace": str(trace)[:200],
    }


# --------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------

def layout_text_candidates(texts: list[str], layout: list[list[dict]] | None) -> Iterator[dict[str, Any]]:
    """Pairs read inside each layout element's own text; the span is located
    through the same layout so the candidate names the element (OCR line,
    jdf-cli element or tree paragraph) it sits on."""
    for page_index, segments in enumerate(layout or []):
        for seg in segments or []:
            text = str(seg.get("text") or "")
            base = int(seg.get("start") or 0)
            for label, value, vs, ve in fd.iter_pairs(text):
                start, end = base + vs, base + ve
                span = fd._locate(layout, page_index, start, end)
                yield _candidate(name_hint=label, raw_text=value, label_anchor=label, page=page_index + 1, span=span, source_kind="layout_text",
                                 trace=f"layout_text: pair regex in element {span.get('element_id') or seg.get('node_id') or '?'}",
                                 node_id=span.get("node_id"), element_id=span.get("element_id"))


def discovery_candidates(texts: list[str], layout: list[list[dict]] | None) -> Iterator[dict[str, Any]]:
    """``field_discovery.discover_heuristic`` over the joined page text."""
    for pair in fd.discover_heuristic(texts, layout, limit=DISCOVERY_LIMIT):
        span = dict(pair.get("span") or {})
        yield _candidate(name_hint=pair.get("label") or pair.get("name") or "", raw_text=pair.get("value") or "", label_anchor=pair.get("label") or "",
                         page=int(pair.get("page") or 1), span=span, source_kind="discovery",
                         trace=f"discovery: {pair.get('method')}", node_id=span.get("node_id"), element_id=span.get("element_id"))


def table_cell_candidates(tables: Iterable[dict[str, Any]] | None) -> Iterator[dict[str, Any]]:
    """Every non-empty cell of every collected table. The label is the column
    header (with the row label beside it in the span) or, without headers, the
    row's first cell; table cells have no character offset in the page text."""
    for table in tables or []:
        if not isinstance(table, dict):
            continue
        page = int(table.get("page") or 1)
        headers = [str(h or "").strip() for h in (table.get("headers") or [])]
        rows = table.get("rows") or []
        for r, row in enumerate(rows):
            if not isinstance(row, list):
                continue
            cells = [str(c or "").strip() for c in row]
            row_label = cells[0] if cells else ""
            for c, cell in enumerate(cells):
                if not cell:
                    continue
                header = headers[c] if c < len(headers) else ""
                if c == 0 and len(cells) > 1 and (headers or row_label):
                    continue  # the row label is the anchor of the other cells, not a value
                label = header or row_label or f"column {c + 1}"
                span = {"span_type": "table_cell", "start_char": None, "end_char": None, "table_id": table.get("table_id"), "row": r, "col": c,
                        "row_label": row_label or None, "bbox": table.get("bbox")}
                yield _candidate(name_hint=label, raw_text=cell, label_anchor=label, page=page, span=span, source_kind="table_cell",
                                 trace=f"table_cell: {table.get('table_id')} r{r} c{c}", node_id=table.get("node_id"), element_id=table.get("element_id"))


def textract_form_candidates(forms: Iterable[dict[str, Any]] | None, texts: list[str], layout: list[list[dict]] | None) -> Iterator[dict[str, Any]]:
    """Textract ``KEY_VALUE_SET`` pairs; the span is the value found verbatim
    in the page text when it is there (Textract gives no offsets)."""
    for i, form in enumerate(forms or []):
        if not isinstance(form, dict):
            continue
        key = str(form.get("key") or "").strip()
        value = str(form.get("value") or "").strip()
        if not key or not value:
            continue
        page = int(form.get("page") or 1)
        text = texts[page - 1] if 0 < page <= len(texts) else ""
        hit = lx.find_verbatim(text, value) if text else None
        span = fd._locate(layout, page - 1, hit[0], hit[1]) if hit else {"span_type": "textract_kv", "start_char": None, "end_char": None}
        # The reader's own value box (then the label's) is where the value is
        # on the page — tighter than the OCR line the text match lands on, and
        # the only box when the value is not verbatim in the page text
        # (review 2026-10-01: such fields had no position at all).
        for box in (form.get("value_bbox"), form.get("key_bbox")):
            if isinstance(box, dict) and all(isinstance(box.get(k), (int, float)) for k in ("x", "y", "w", "h")):
                span = dict(span, span_type="bbox_relative",
                            bbox=[round(box["x"], 4), round(box["y"], 4), round(box["x"] + box["w"], 4), round(box["y"] + box["h"], 4)])
                break
        for flag in ("handwritten", "illegible", "kind"):
            if form.get(flag):
                span = dict(span, **{flag: form[flag]})
        # The model reader's pairs say so (user finding 2026-10-01: Opus-read
        # fields were labelled "Textract" in the UI).
        kind = "model_read" if form.get("reader") == "model" else "textract"
        yield _candidate(name_hint=key, raw_text=value, label_anchor=key, page=page, span=span, source_kind=kind,
                         trace=f"{kind}: KEY_VALUE_SET #{i}", node_id=span.get("node_id"), element_id=span.get("element_id"))


def vision_candidates(vision: dict[str, Any] | None, texts: list[str], layout: list[list[dict]] | None) -> Iterator[dict[str, Any]]:
    """``report["vision"]`` facts as candidates, corroborated against the page text."""
    if not isinstance(vision, dict):
        return
    model = vision.get("model")
    for entry in vision.get("pages") or []:
        if not isinstance(entry, dict):
            continue
        page = int(entry.get("page") or 0)
        text = texts[page - 1] if 0 < page <= len(texts) else ""
        for fact in entry.get("facts") or []:
            if not isinstance(fact, dict) or fact.get("value") in (None, ""):
                continue
            name = str(fact.get("name") or "fact")
            raw = str(fact.get("value")).strip()
            hit = lx.find_verbatim(text, raw) if text and raw else None
            if hit:
                span = fd._locate(layout, page - 1, hit[0], hit[1])
            else:
                span = {"span_type": "image", "start_char": None, "end_char": None, "bbox": fact.get("bbox")}
            evidence = str(fact.get("evidence") or "")[:100]
            yield _candidate(name_hint=name, raw_text=raw, label_anchor=name.replace("_", " "), page=page, span=span, source_kind="image_vision",
                             corroborated=hit is not None, trace=f"image_vision: {model}; {'verbatim on page' if hit else 'NOT on page text'}; evidence: {evidence}",
                             node_id=span.get("node_id"), element_id=span.get("element_id"))


# --------------------------------------------------------------------------
# Merge
# --------------------------------------------------------------------------

def _overlaps(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if a.get("page") != b.get("page"):
        return False
    a0, a1, b0, b1 = _start(a), _end(a), _start(b), _end(b)
    if NO_SPAN in (a0, a1, b0, b1):
        return False
    return a0 < b1 and b0 < a1


def merge_new(candidates: Iterable[dict[str, Any]], *, documents: list[dict] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Dedupe, order, mark ``preferred`` on overlaps, cap per segment, stamp
    ids — the Part 1.2 rules over one build's candidates. Returns the pool
    and the counts (``deduped``, ``capped``, ``overlaps``)."""
    best: dict[tuple, dict[str, Any]] = {}
    deduped = 0
    for c in candidates:
        key = dedupe_key(c)
        cur = best.get(key)
        if cur is None:
            best[key] = c
        else:
            deduped += 1
            if sort_key(c) < sort_key(cur):
                best[key] = c
    pool = sorted(best.values(), key=sort_key)
    overlaps = 0
    for i, a in enumerate(pool):
        for b in pool[i + 1:]:
            if b["page"] != a["page"]:
                continue
            if _overlaps(a, b) and fold(a["raw_text"]) != fold(b["raw_text"]):
                overlaps += 1
                if b.get("preferred") is not False:
                    b["preferred"] = False  # ``a`` sorts first: it is the preferred read of that span
    segments = [list(d.get("pages") or []) for d in (documents or []) if isinstance(d, dict)] or [sorted({c["page"] for c in pool})]
    kept: list[dict[str, Any]] = []
    capped = 0
    seen_pages: set[int] = set()
    for pages in segments:
        page_set = {int(p) for p in pages}
        seen_pages |= page_set
        seg = [c for c in pool if c["page"] in page_set]
        kept.extend(seg if MAX_PER_SEGMENT is None else seg[:MAX_PER_SEGMENT])
        capped += 0 if MAX_PER_SEGMENT is None else max(0, len(seg) - MAX_PER_SEGMENT)
    rest = [c for c in pool if c["page"] not in seen_pages]
    kept.extend(rest if MAX_PER_SEGMENT is None else rest[:MAX_PER_SEGMENT])
    capped += 0 if MAX_PER_SEGMENT is None else max(0, len(rest) - MAX_PER_SEGMENT)
    kept.sort(key=sort_key)
    for c in kept:
        c["candidate_id"] = candidate_id(c)
    return kept, {"deduped": deduped, "overlaps": overlaps, "capped": capped}


def merge_pool(existing: list[dict[str, Any]] | None, new: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Append-only merge: every stored candidate stays as it is (its
    ``preferred`` flag included); a new candidate is appended when its
    ``candidate_id`` is not in the pool. Returns ``(pool, added)``."""
    pool = [c for c in (existing or []) if isinstance(c, dict)]
    ids = {c.get("candidate_id") for c in pool}
    added = 0
    for c in new:
        cid = c.get("candidate_id") or candidate_id(c)
        if cid in ids:
            continue
        c["candidate_id"] = cid
        pool.append(c)
        ids.add(cid)
        added += 1
    return pool, added


def build_pool(
    texts: list[str],
    layout: list[list[dict]] | None,
    *,
    tables: Iterable[dict[str, Any]] | None = None,
    forms: Iterable[dict[str, Any]] | None = None,
    vision: dict[str, Any] | None = None,
    documents: list[dict] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The deterministic pool for one report build (plus vision facts when the
    caller already has them). Never raises: a source that fails is counted
    under ``failed_sources`` and the others still land."""
    texts = [t or "" for t in (texts or [])]
    collected: list[dict[str, Any]] = []
    by_source: dict[str, int] = {k: 0 for k in SOURCE_KINDS}
    failed: dict[str, str] = {}
    sources: list[tuple[str, Any]] = [
        ("layout_text", lambda: layout_text_candidates(texts, layout)),
        ("discovery", lambda: discovery_candidates(texts, layout)),
        ("table_cell", lambda: table_cell_candidates(tables)),
        ("textract", lambda: textract_form_candidates(forms, texts, layout)),
        ("image_vision", lambda: vision_candidates(vision, texts, layout)),
    ]
    for kind, fn in sources:
        try:
            for c in fn():
                by_source[c.get("source_kind") or kind] = by_source.get(c.get("source_kind") or kind, 0) + 1
                collected.append(c)
        except Exception as exc:  # noqa: BLE001 — one source must not empty the pool
            log.exception("raw_candidates: %s source failed", kind)
            failed[kind] = f"{type(exc).__name__}: {exc}"[:200]
    pool, counts = merge_new(collected, documents=documents)
    stats = {
        "status": "completed",
        "candidates": len(pool),
        "collected": len(collected),
        "by_source": by_source,
        "corroborated_vision": sum(1 for c in pool if c["source_kind"] == "image_vision" and c["corroborated"]),
        "uncorroborated_vision": sum(1 for c in pool if c["source_kind"] == "image_vision" and not c["corroborated"]),
        "max_per_segment": MAX_PER_SEGMENT,
        "precedence": list(SORT_KEY),
        **counts,
        "failed_sources": failed or None,
    }
    return pool, stats


def candidates_for_pages(pool: list[dict[str, Any]] | None, pages: Iterable[int]) -> list[dict[str, Any]]:
    page_set = {int(p) for p in pages}
    return sorted([c for c in (pool or []) if isinstance(c, dict) and int(c.get("page") or 0) in page_set], key=sort_key)


# --------------------------------------------------------------------------
# Projection
# --------------------------------------------------------------------------

def label_matches(spec: fx.FieldSpec, label: Any) -> bool:
    """Does one of the spec's anchors name this label? The anchor is compiled
    exactly as the label pass compiles it (word boundary before, no letter or
    apostrophe after) and must occur in a label of at most six words."""
    text = " ".join(str(label or "").split())
    if not text or len(text.split()) > _MAX_LABEL_WORDS:
        return False
    for anchor in spec.anchors:
        try:
            if re.search(r"(?<![a-z])" + anchor + r"(?!'|[a-z])", text, re.I):
                return True
        except re.error:  # pragma: no cover - anchors are validated by the registry
            continue
    return False


def _values_equal(spec: fx.FieldSpec, a: Any, b: Any) -> bool:
    if spec.field_type in ("money", "number"):
        try:
            return abs(float(a) - float(b)) < 0.005
        except (TypeError, ValueError):
            return False
    return fold(a) == fold(b)


def _field_from_candidate(spec: fx.FieldSpec, c: dict[str, Any], *, texts: list[str], layout: list[list[dict]], parser_name: str | None,
                          parse_confidence: float | None, ocr_confidence: float | None, page_quality: list[float | None],
                          visual_pages: list[dict | None]) -> dict[str, Any] | None:
    """The contract record for a candidate under ``spec`` — the same builder the
    label pass and the grounded pass use, so shape, confidence and provenance
    are judged the same way. None when the candidate has no span on the page
    text (a table cell or an uncorroborated vision read cannot be re-found) or
    when its text does not have the field's value shape."""
    page_index = int(c.get("page") or 0) - 1
    if not 0 <= page_index < len(texts):
        return None
    text = texts[page_index] or ""
    start, end = _start(c), _end(c)
    raw = str(c.get("raw_text") or "")
    if start == NO_SPAN or end == NO_SPAN:
        hit = lx.find_verbatim(text, raw) if raw else None
        if hit is None:
            return None
        start, end = hit
    elif fold(text[start:end]) != fold(raw):
        hit = lx.find_verbatim(text, raw) if raw else None
        if hit is None:
            return None
        start, end = hit
    raw = text[start:end]
    field = fx.build_found_field(
        spec, page_index=page_index, raw=raw, start=start, end=end, parser_name=parser_name, parse_confidence=parse_confidence,
        ocr_confidence=ocr_confidence, page_quality=list(page_quality or []), visual_pages=list(visual_pages or []), layout=list(layout or []),
        method=CANDIDATE_METHOD, page_text=text,
    )
    if field.get("value") is None:
        return None
    field["grounding_source"] = CANDIDATE_METHOD
    field["grounding_model"] = str(c.get("source_kind"))
    field["candidate_source"] = {"candidate_id": c.get("candidate_id"), "source_kind": c.get("source_kind"), "source_priority": c.get("source_priority"),
                                 "label_anchor": c.get("label_anchor"), "corroborated": c.get("corroborated")}
    fx.mark_low_quality_page(field, list(page_quality or []))
    return field


def project_candidates(
    document_type: str,
    candidates: list[dict[str, Any]] | None,
    fields: list[dict[str, Any]],
    *,
    texts: list[str],
    layout: list[list[dict]] | None,
    parser_name: str | None,
    parse_confidence: float | None,
    ocr_confidence: float | None,
    page_quality: list[float | None] | None,
    visual_pages: list[dict | None] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Map the pool onto ``document_type``'s schema (plan Part 1.3).

    For each spec (signatures excepted — ink is the probe's question) the
    candidates whose label an anchor names are tried in :func:`sort_key`
    order; the first whose text has the field's value shape becomes the
    field when the label pass left it empty or holding debris. When the label
    pass already holds a valid value: an agreeing candidate is logged
    ``mapped`` beside it; a disagreeing one is ``conflicting`` — the field
    keeps the label pass's value unless the candidate came from Textract, a
    table cell or a corroborated vision read (higher than any text regex),
    and either way the pair lands in ``log["conflicts"]`` for
    ``report["conflicts"]``. Table cells map only when every matching cell
    agrees (``table_extraction`` owns the total-row judgement). Candidates are
    never removed from the pool. Returns the fields and the projection log."""
    # Textract's line-confidence mean is the parser's confidence for an OCR
    # parse (as extract_fields already treats it, 2026-09-29): a candidate
    # mapped from the pool otherwise fell to parser_default[textract] 0.80 while
    # the reader had reported 0.95 — the user read that as "Textract says 100 %,
    # the UI says 10 %".
    if parse_confidence is None and ocr_confidence is not None:
        parse_confidence = float(ocr_confidence)
    specs = [s for s in (fx.FIELD_TAXONOMY.get(document_type) or []) if s.field_type != "signature"]
    pool = sorted([c for c in (candidates or []) if isinstance(c, dict)], key=sort_key)
    by_name = {f.get("name"): f for f in fields if isinstance(f, dict)}
    log_fields: dict[str, dict[str, Any]] = {}
    cand_outcomes: dict[str, dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    out_fields = list(fields)
    counts = {k: 0 for k in PROJECTION_OUTCOMES}
    for spec in specs:
        matching = [c for c in pool if label_matches(spec, c.get("label_anchor"))]
        if not matching:
            continue
        table_cells = [c for c in matching if c.get("source_kind") == "table_cell"]
        if table_cells and len({fold(c.get("raw_text")) for c in table_cells}) > 1:
            for c in table_cells:
                cand_outcomes.setdefault(c["candidate_id"], {"outcome": "unmapped", "field": spec.name, "reason": "ambiguous table cells"})
            matching = [c for c in matching if c.get("source_kind") != "table_cell"]
            if not matching:
                continue
        existing = by_name.get(spec.name)
        has_value = bool(existing) and existing.get("value") is not None
        built: dict[str, Any] | None = None
        chosen: dict[str, Any] | None = None
        for c in matching:
            built = _field_from_candidate(spec, c, texts=texts, layout=layout or [], parser_name=parser_name, parse_confidence=parse_confidence,
                                          ocr_confidence=ocr_confidence, page_quality=list(page_quality or []), visual_pages=list(visual_pages or []))
            if built is not None:
                chosen = c
                break
            cand_outcomes.setdefault(c["candidate_id"], {"outcome": "unmapped", "field": spec.name, "reason": "no value shape for this field / not re-found on the page"})
        if built is None or chosen is None:
            continue
        if not has_value:
            if existing is not None:
                built["segment"] = existing.get("segment", built.get("segment"))
                out_fields = [built if f is existing else f for f in out_fields]
            else:
                out_fields.append(built)
            by_name[spec.name] = built
            log_fields[spec.name] = {"outcome": "mapped", "candidate_id": chosen["candidate_id"], "source_kind": chosen["source_kind"],
                                     "replaced": "found_suspect" if existing is not None and existing.get("evidence_state") == "found_suspect" else "absent"}
            cand_outcomes[chosen["candidate_id"]] = {"outcome": "mapped", "field": spec.name}
            counts["mapped"] += 1
            continue
        if _values_equal(spec, existing.get("value"), built.get("value")):
            log_fields[spec.name] = {"outcome": "mapped", "candidate_id": chosen["candidate_id"], "source_kind": chosen["source_kind"], "replaced": None,
                                     "agrees_with": existing.get("extraction_method")}
            cand_outcomes[chosen["candidate_id"]] = {"outcome": "mapped", "field": spec.name, "agrees": True}
            counts["mapped"] += 1
            continue
        # Conflict: both facts stay — the field is flagged, the candidate stays in the pool.
        take_candidate = int(chosen.get("source_priority") or 0) >= SOURCE_PRIORITY["table_cell"]
        winner = built if take_candidate else existing
        loser_value = existing.get("value") if take_candidate else built.get("value")
        if take_candidate:
            built["segment"] = existing.get("segment", built.get("segment"))
            out_fields = [built if f is existing else f for f in out_fields]
            by_name[spec.name] = built
        winner["candidate_conflict"] = {"candidate_id": chosen["candidate_id"], "source_kind": chosen["source_kind"], "candidate_value": built.get("value"),
                                        "field_value": existing.get("value"), "kept": "candidate" if take_candidate else "label_pass"}
        conflicts.append({
            "field": spec.name, "kind": "candidate_conflict", "kept": "candidate" if take_candidate else "label_pass",
            "values": [
                {"source": "field", "method": existing.get("extraction_method"), "value": existing.get("value"), "page": (existing.get("source_span") or {}).get("page")},
                {"source": "candidate", "candidate_id": chosen["candidate_id"], "source_kind": chosen["source_kind"], "value": built.get("value"), "page": chosen.get("page")},
            ],
            "dropped_value": loser_value,
        })
        log_fields[spec.name] = {"outcome": "conflicting", "candidate_id": chosen["candidate_id"], "source_kind": chosen["source_kind"],
                                 "kept": "candidate" if take_candidate else "label_pass"}
        cand_outcomes[chosen["candidate_id"]] = {"outcome": "conflicting", "field": spec.name}
        counts["conflicting"] += 1
    for c in pool:
        cand_outcomes.setdefault(c["candidate_id"], {"outcome": "unmapped", "field": None})
    counts["unmapped"] = sum(1 for o in cand_outcomes.values() if o["outcome"] == "unmapped")
    log_out = {
        "status": "completed",
        "document_type": document_type,
        "candidates": len(pool),
        "specs": len(specs),
        "counts": counts,
        "fields": log_fields,
        "candidates_log": cand_outcomes,
        "conflicts": conflicts,
        "method": CANDIDATE_METHOD,
    }
    return out_fields, log_out


def finalize_projection(log: dict[str, Any] | None, fields: list[dict[str, Any]]) -> dict[str, Any] | None:
    """After the decision policy ran: a mapped field that the policy left
    ``unverified`` / ``manual_review`` below the confidence thresholds is
    labelled ``review_needed`` in the log — the field carries only the
    existing vocabulary."""
    if not isinstance(log, dict):
        return log
    by_name = {f.get("name"): f for f in fields if isinstance(f, dict)}
    for name, entry in (log.get("fields") or {}).items():
        f = by_name.get(name)
        if not f or entry.get("outcome") != "mapped":
            continue
        ec = float(f.get("extraction_confidence") or 0.0)
        vc = f.get("verification_confidence")
        below = ec < fx.EXTRACTION_THRESHOLD or vc is None or float(vc) < fx.VERIFICATION_THRESHOLD
        if f.get("value") is not None and f.get("field_state") == "unverified" and f.get("routing_action") == "manual_review" and below:
            entry["outcome"] = "review_needed"
            entry["field_state"] = f.get("field_state")
            entry["routing_action"] = f.get("routing_action")
    counts = {k: 0 for k in PROJECTION_OUTCOMES}
    for entry in (log.get("fields") or {}).values():
        counts[entry["outcome"]] = counts.get(entry["outcome"], 0) + 1
    counts["unmapped"] = sum(1 for o in (log.get("candidates_log") or {}).values() if o.get("outcome") == "unmapped")
    log["counts"] = counts
    log.update(counts)  # top-level mirrors for renderers that read one payload level (parsure_view.EXECUTION_STEPS)
    return log


def merge_projection_logs(prev: dict[str, Any] | None, new: dict[str, Any] | None) -> dict[str, Any] | None:
    """Combine per-segment logs into the report's one block (counts added,
    field and candidate entries unioned; the first segment's type is kept and
    the others listed)."""
    if not isinstance(prev, dict):
        return new
    if not isinstance(new, dict):
        return prev
    out = dict(prev)
    out["counts"] = {k: int(prev.get("counts", {}).get(k, 0)) + int(new.get("counts", {}).get(k, 0)) for k in PROJECTION_OUTCOMES}
    out["fields"] = {**(prev.get("fields") or {}), **(new.get("fields") or {})}
    out["candidates_log"] = {**(prev.get("candidates_log") or {}), **(new.get("candidates_log") or {})}
    out["conflicts"] = list(prev.get("conflicts") or []) + list(new.get("conflicts") or [])
    out["candidates"] = int(prev.get("candidates") or 0) + int(new.get("candidates") or 0)
    out["specs"] = int(prev.get("specs") or 0) + int(new.get("specs") or 0)
    types = [t for t in (prev.get("document_types") or [prev.get("document_type")]) if t]
    if new.get("document_type") not in types:
        types.append(new.get("document_type"))
    out["document_types"] = types
    return out


# --------------------------------------------------------------------------
# Readability evidence for the page-quality recalibration (plan Part 4)
# --------------------------------------------------------------------------

def looks_like_debris(text: Any) -> bool:
    """The ``value_shape`` garbage rule: under half the characters are letters or digits."""
    s = str(text or "").strip()
    if not s:
        return True
    return sum(ch.isalnum() for ch in s) / len(s) < 0.5


def grounding_success_by_page(fields: list[dict[str, Any]], *, discovered: list[dict[str, Any]] | None = None,
                              candidates: list[dict[str, Any]] | None = None, page_count: int) -> list[float | None]:
    """Per page, the share of located reads that were usable: fields with a
    shape-valid value count for, ``found_suspect`` reads count against;
    discovery pairs and corroborated vision reads whose text is not debris
    count for. None for a page with no located read at all — the page score
    is then left as the probe measured it."""
    valid = [0] * max(0, int(page_count or 0))
    suspect = [0] * max(0, int(page_count or 0))

    def _page_of(f: dict[str, Any]) -> int | None:
        span = f.get("source_span") if isinstance(f.get("source_span"), dict) else {}
        p = span.get("page") or f.get("page")
        return int(p) if isinstance(p, int) and 0 < p <= len(valid) else None

    for f in fields or []:
        if not isinstance(f, dict) or f.get("field_type") == "signature":
            continue
        p = _page_of(f)
        if p is None:
            continue
        if f.get("evidence_state") == "found_suspect":
            suspect[p - 1] += 1
        elif f.get("value") is not None and (f.get("value_quality") or {}).get("quality", "valid") == "valid":
            valid[p - 1] += 1
    for d in discovered or []:
        if isinstance(d, dict) and d.get("value") is not None:
            p = _page_of(d)
            if p is not None:
                (suspect if looks_like_debris(d.get("value")) else valid)[p - 1] += 1
    for c in candidates or []:
        if isinstance(c, dict) and c.get("source_kind") == "image_vision" and c.get("corroborated"):
            p = int(c.get("page") or 0)
            if 0 < p <= len(valid):
                (suspect if looks_like_debris(c.get("raw_text")) else valid)[p - 1] += 1
    out: list[float | None] = []
    for v, s in zip(valid, suspect):
        out.append(round(v / (v + s), 3) if (v + s) else None)
    return out


#: Sources whose candidates are a document's own labelled facts — the reading
#: of a key/value pair on the page, not a model's inference.
DYNAMIC_FIELD_SOURCES = ("model_read", "textract", "discovery")


def dynamic_fields(pool: list[dict[str, Any]], projection: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The document's fields as the page states them, schema or no schema.

    User decision 2026-09-29: the PDFs have no known field list, so the
    key/value pairs Textract's FORMS analysis and the label/value discovery read
    are fields in their own right — every one, in reading order, beside the
    schema's projection. Each carries the verbatim label and value, the page,
    the box, the source and, when the projection mapped the candidate onto a
    schema field, that field's name and outcome. Nothing here is inferred and
    nothing carries a confidence the reader did not report (Textract's own
    pair confidence rides in ``trace`` when the source kept it).
    """
    log = (projection or {}).get("candidates_log") if isinstance(projection, dict) else None
    out: list[dict[str, Any]] = []
    for c in pool or []:
        if c.get("source_kind") not in DYNAMIC_FIELD_SOURCES:
            continue
        label = str(c.get("label_anchor") or c.get("name_hint") or "").strip()
        value = str(c.get("raw_text") or "").strip()
        if not label or not value:
            continue
        span = c.get("source_span") if isinstance(c.get("source_span"), dict) else {}
        mapped = (log or {}).get(c.get("candidate_id")) if isinstance(log, dict) else None
        out.append({
            "label": label,
            "value": value,
            "name_hint": c.get("name_hint"),
            "page": c.get("page"),
            "bbox": span.get("bbox") or c.get("bbox"),
            "source": c.get("source_kind"),
            "preferred": c.get("preferred", True),
            "candidate_id": c.get("candidate_id"),
            "element_id": c.get("element_id"),
            "node_id": c.get("node_id"),
            "trace": c.get("trace"),
            "schema_field": (mapped or {}).get("field"),
            "projection": (mapped or {}).get("outcome"),
        })
    return out
