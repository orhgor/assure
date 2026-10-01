"""Presentation rules the intake pages share (``/parsing``, ``/parsing/<id>``,
the shell's Fields tab mirrors them in ``prototype/shell.js``).

Three sections, one rule (customer handoff 2026-09-27, "Evidence-State
Confusion": *never treat "not found" as "needs review"*):

* ``not_found`` — the report says the value is not on the document
  (``field_state == "not_found"``, ``evidence_state`` in
  ``not_on_document`` / ``unreadable``), or an older report has no value and
  nothing routed it to a person. These rows offer "Enter value" only.
* ``review`` — ``field_extractor.field_needs_review``: a routing action other
  than ``none`` / ``field_not_found``, or a disputed / rejected state.
* ``found`` — everything else: a value, accepted or awaiting nothing.

The order of the tests matters: an unreadable page whose absence routes
``retry_parsure`` is *not found* here (the reviewer cannot act on the value)
and *counted* as review by ``field_needs_review`` (the rescan is a person's
decision) — the two figures can legitimately differ by those rows, and the
pages say so rather than hide one of them.
"""

from __future__ import annotations

from typing import Any

try:
    from .field_extractor import field_needs_review
except ImportError:  # pragma: no cover — prompt_matrix/ as sys.path root
    from services.field_extractor import field_needs_review  # type: ignore

NOT_FOUND_EVIDENCE = ("not_on_document", "unreadable")

#: ``signature_quality.quality`` in words. The 2026-09-27 vocabulary first;
#: the earlier one (clear / faint / incomplete / stamped / questionable) is
#: still read so a stored report renders.
SIGNATURE_WORDS = {
    "present_clear": "Signature present, clear",
    "present_ambiguous": "Signature present, ambiguous",
    "missing": "Signature missing",
    "stamp": "A stamp, not a signature",
    "printed_name": "A printed name, not a signature",
    "unreadable": "Signature area unreadable",
    "clear": "Signature present, clear",
    "faint": "Signature faint",
    "incomplete": "Signature incomplete",
    "stamped": "A stamp, not a signature",
    "questionable": "Signature hard to read",
}

#: ``value_quality.quality`` in words (only shown when not ``valid``).
VALUE_QUALITY_WORDS = {
    "invalid_format": "Wrong shape for this field",
    "garbage": "Unreadable characters",
    "header_or_label": "A label, not a value",
    "address_fragment": "Part of an address, not the value",
}


def field_section(field: dict[str, Any]) -> str:
    """``not_found`` / ``review`` / ``found`` for one field dict."""
    if not isinstance(field, dict):
        return "found"
    if str(field.get("field_state") or "") == "not_found":
        return "not_found"
    if str(field.get("evidence_state") or "") in NOT_FOUND_EVIDENCE:
        return "not_found"
    needs = field_needs_review(field)
    if field.get("value") is None and not needs:
        return "not_found"
    return "review" if needs else "found"


def section_counts(fields: list[dict[str, Any]]) -> dict[str, int]:
    """``review`` / ``found`` / ``not_found`` partition the fields; ``read``
    counts every field holding a value whichever section it sits in. The list
    card led with "0 found" for a document whose four values all needed review
    (compliance-bound or below the auto-accept line) and the user read it as
    "no field data" (2026-09-29) — the value count is the fact a reader wants
    first, the review count is the policy's verdict on it."""
    counts = {"review": 0, "found": 0, "not_found": 0, "read": 0}
    for f in fields:
        counts[field_section(f)] += 1
        if isinstance(f, dict) and f.get("value") is not None and str(f.get("field_type") or "") != "signature":
            counts["read"] += 1
    return counts


def searched_summary(field: dict[str, Any]) -> dict[str, Any] | None:
    """What an absent field's search covered — ``{pages, nodes, chars,
    readability}`` from ``evidence`` (kind ``absent``), or ``None`` when the
    report did not record a search. Counts, not claims: a report without the
    block yields nothing rather than zeros."""
    ev = field.get("evidence") if isinstance(field.get("evidence"), dict) else None
    if not ev or str(ev.get("kind") or "") != "absent":
        return None
    pages = ev.get("searched_pages")
    nodes = ev.get("searched_node_ids")
    chars = ev.get("searched_chars")
    out = {
        "pages": len(pages) if isinstance(pages, (list, tuple)) else None,
        "nodes": len(nodes) if isinstance(nodes, (list, tuple)) else None,
        "chars": int(chars) if isinstance(chars, (int, float)) else None,
        "readability": str(ev["readability"]) if ev.get("readability") not in (None, "") else None,
    }
    return out if any(v is not None for v in out.values()) else None


def signature_quality(field: dict[str, Any]) -> dict[str, Any] | None:
    """``{quality, words, next_check, basis, page}`` or ``None``."""
    sq = field.get("signature_quality")
    if isinstance(sq, str):
        sq = {"quality": sq}
    if not isinstance(sq, dict) or not sq.get("quality"):
        return None
    q = str(sq.get("quality") or "").lower()
    return {
        "quality": q,
        "words": SIGNATURE_WORDS.get(q, q.replace("_", " ").capitalize()),
        "next_check": str(sq["next_check"]) if sq.get("next_check") else None,
        "basis": str(sq["basis"]) if sq.get("basis") else None,
        "page": sq.get("page"),
    }


def value_quality(field: dict[str, Any]) -> dict[str, Any] | None:
    """``{quality, words, basis}`` when the value failed shape validation;
    ``None`` when it is valid or the report carries no block."""
    vq = field.get("value_quality")
    if not isinstance(vq, dict):
        return None
    q = str(vq.get("quality") or "").lower()
    if not q or q == "valid":
        return None
    return {"quality": q, "words": VALUE_QUALITY_WORDS.get(q, q.replace("_", " ").capitalize()), "basis": str(vq.get("basis") or "") or None}


def verification_basis(field: dict[str, Any]) -> str | None:
    """The verification sentence, or ``None``. ``verification_basis`` is the
    report's own words; a field with ``verification_confidence`` ``None`` and
    no basis reads as "not verified" only when the report did not verify it —
    never a PASS the code did not earn."""
    vb = field.get("verification_basis")
    if isinstance(vb, str) and vb.strip():
        return vb.strip()
    if "verification_confidence" in field and field.get("verification_confidence") is None:
        return "not verified"
    return None


def snapshot_view(report: dict[str, Any], integrity: dict[str, Any] | None) -> dict[str, Any] | None:
    """``{content_hash, short_hash, algorithm, stamped_at, ok}`` or ``None`` when
    the report was saved before snapshots existed."""
    snap = report.get("snapshot") if isinstance(report.get("snapshot"), dict) else None
    if not snap or not snap.get("content_hash"):
        return None
    h = str(snap["content_hash"])
    ok = integrity.get("ok") if isinstance(integrity, dict) else None
    return {
        "content_hash": h,
        "short_hash": h[:12],
        "algorithm": snap.get("algorithm"),
        "stamped_at": snap.get("stamped_at"),
        "ok": bool(ok) if ok is not None else None,
        "expected": (integrity or {}).get("expected") if isinstance(integrity, dict) else None,
        "actual": (integrity or {}).get("actual") if isinstance(integrity, dict) else None,
    }


def replay_view(report: dict[str, Any]) -> dict[str, Any]:
    """The replay block for the record page: attempts / max, eligibility, stop
    rule, history, last proof. Missing keys read as unknown (``None``), not 0."""
    rp = report.get("replay") if isinstance(report.get("replay"), dict) else {}
    history = rp.get("history") if isinstance(rp.get("history"), list) else []
    attempts = rp.get("attempts")
    if attempts is None and history:
        attempts = len(history)
    max_attempts = rp.get("max_attempts")
    return {
        "eligible": bool(rp.get("eligible")),
        "reasons": [str(r) for r in (rp.get("reasons") or [])],
        "replayed": bool(rp.get("replayed")),
        "attempts": int(attempts) if isinstance(attempts, (int, float)) else None,
        "max_attempts": int(max_attempts) if isinstance(max_attempts, (int, float)) else None,
        "stop_rule": str(rp["stop_rule"]) if rp.get("stop_rule") else None,
        "history": history,
        "last_proof": rp.get("last_proof") if isinstance(rp.get("last_proof"), dict) else None,
        "exhausted": (
            isinstance(attempts, (int, float)) and isinstance(max_attempts, (int, float)) and attempts >= max_attempts
        ),
    }


def uncertainty_view(classification: dict[str, Any]) -> dict[str, Any] | None:
    """``classification.uncertainty`` when the type is uncertain or
    ``<family>_unknown``; ``None`` otherwise (or for a report saved before
    the block existed — the page then shows the type's ``basis`` alone)."""
    u = classification.get("uncertainty") if isinstance(classification, dict) else None
    if not isinstance(u, dict):
        return None
    return {
        "reason_codes": [str(r) for r in (u.get("reason_codes") or [])],
        "keywords_matched": [str(k) for k in (u.get("keywords_matched") or [])],
        "family": str(u["family"]) if u.get("family") else None,
        "fields_searched": [str(f) for f in (u.get("fields_searched") or [])],
        "fields_found": [str(f) for f in (u.get("fields_found") or [])],
        "basis": str(u["basis"]) if u.get("basis") else None,
    }


def page_coverage_view(report: dict[str, Any]) -> list[dict[str, Any]]:
    """``page_coverage`` rows (mixed bundles): page → segment → type → fields
    found → readability. Empty for a report without the block."""
    rows = report.get("page_coverage") if isinstance(report.get("page_coverage"), list) else []
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        out.append({
            "page": r.get("page"),
            "segment": r.get("segment"),
            "document_type": r.get("document_type"),
            "fields_found": int(r["fields_found"]) if isinstance(r.get("fields_found"), (int, float)) else None,
            "readability": r.get("readability"),
        })
    return out


def low_quality_pages(report: dict[str, Any]) -> dict[str, Any] | None:
    """``{pages, threshold}`` from ``quality_report`` when it lists them."""
    qr = report.get("quality_report") if isinstance(report.get("quality_report"), dict) else {}
    pages = qr.get("low_quality_pages")
    if not isinstance(pages, list) or not pages:
        return None
    thr = qr.get("low_quality_threshold")
    return {"pages": [p for p in pages if isinstance(p, (int, float))], "threshold": float(thr) if isinstance(thr, (int, float)) else None}


# ---------------------------------------------------------------------------
# Execution model surfaces (customer plan todos/fable_execution_plan.md Part 6,
# 2026-09-27). Every block here may be absent on an older report: each helper
# returns None / "not recorded" for a missing key and never a default number.
# ---------------------------------------------------------------------------

#: The field badge vocabulary, as the plan names it (6.1): Not found / Suspect /
#: Unverified / Accepted / Review — plus the two states a person produced.
BADGE_WORDS = {
    "not_found": "Not found",
    "suspect": "Suspect",
    "unverified": "Unverified",
    "accepted": "Accepted",
    "review": "Review",
    "disputed": "Disputed",
    "rejected": "Rejected",
}


def field_badge(field: dict[str, Any]) -> dict[str, str]:
    """``{key, words}`` from ``field_state`` / ``evidence_state`` /
    ``review_required``: a person's verdict first (disputed / rejected), then
    the extractor's (not found, suspect), then whether anyone must look."""
    state = str(field.get("field_state") or "")
    if state in ("disputed", "rejected"):
        return {"key": state, "words": BADGE_WORDS[state]}
    section = field_section(field)
    if section == "not_found":
        return {"key": "not_found", "words": BADGE_WORDS["not_found"]}
    if str(field.get("evidence_state") or "") == "found_suspect":
        return {"key": "suspect", "words": BADGE_WORDS["suspect"]}
    if section == "review" or field.get("review_required") is True:
        return {"key": "review", "words": BADGE_WORDS["review"]}
    if state == "accepted":
        return {"key": "accepted", "words": BADGE_WORDS["accepted"]}
    return {"key": "unverified", "words": BADGE_WORDS["unverified"]}


def provenance_label(field: dict[str, Any]) -> dict[str, str] | None:
    """``{kind, words}`` — "from label" / "from table" / "from model <id>" /
    "from image" — read from ``grounding_source``, then ``grounding_model``,
    then ``extraction_method``. None when the report says nothing about it."""
    source = str(field.get("grounding_source") or "").lower()
    model = str(field.get("grounding_model") or "")
    method = str(field.get("extraction_method") or "").lower()
    kind = None
    if source in ("label_anchor", "label"):
        kind = "label"
    elif source == "table" or method == "table" or model == "table":
        kind = "table"
    elif source == "vision" or model == "vision":
        kind = "image"
    elif source in ("llm", "model") or (model and model not in ("label_anchor", "table", "vision")):
        kind = "model"
    elif model == "label_anchor":
        kind = "label"
    if kind is None:
        return None
    if kind == "model":
        return {"kind": "model", "words": f"from model {model}" if model and model not in ("llm", "model") else "from model", "model": model}
    return {"kind": kind, "words": {"label": "from label", "table": "from table", "image": "from image"}[kind]}


def grounding_view(field: dict[str, Any]) -> dict[str, Any] | None:
    """The grounding block for one field: the verbatim quote (≤240 chars as
    stored), where it sits (page, chars, node / element, or table cell) and
    which model offered it. None when the field carries no quote and no span."""
    quote = field.get("grounding_quote")
    span = field.get("grounding_span") if isinstance(field.get("grounding_span"), dict) else {}
    if not (isinstance(quote, str) and quote.strip()) and not span:
        return None
    start, end = span.get("start_char"), span.get("end_char")
    return {
        "quote": quote.strip() if isinstance(quote, str) else None,
        "page": span.get("page"),
        "start_char": int(start) if isinstance(start, (int, float)) else None,
        "end_char": int(end) if isinstance(end, (int, float)) else None,
        "node_id": span.get("node_id") or field.get("tree_node_id") or field.get("field_source_node_id"),
        "element_id": span.get("element_id"),
        "table_id": span.get("table_id"),
        "row": span.get("row"),
        "col": span.get("col"),
        "model": str(field.get("grounding_model")) if field.get("grounding_model") else None,
        "source": str(field.get("grounding_source")) if field.get("grounding_source") else None,
    }


#: The execution steps in the order the pipeline runs them (plan 2.x), with
#: the words a reviewer reads. ``counts`` names the payload keys shown as
#: "label N"; ``reason`` is printed when the step did not run.
EXECUTION_STEPS = (
    ("laya", "LAYA", (("escalate", "escalate"), ("human_review", "human review")), ("policy",)),
    ("z3", "Z3 verification", (("violations", "violations"),), ("async_deferred",)),
    ("redhat_graph", "Red-Hat graph", (("findings", "findings"), ("high", "high")), ("policy", "model_check")),
    ("llm_grounding", "LLM grounding", (("fields_offered", "offered"), ("fields_grounded", "grounded"), ("candidates_rejected", "rejected")), ("model", "ms")),
    ("rerun", "Rerun", (("passes", "passes"),), ("improved", "stop_rule")),
    ("vision", "Vision", (("pages_analyzed", "pages"), ("facts", "facts")), ("model", "ms")),
    ("tables", "Tables", (("tables", "tables"), ("fields_from_tables", "fields from tables")), ()),
    # Plan V4 (2026-09-28): the schema-agnostic pool and its projection onto the type.
    ("raw_candidates", "Raw candidates", (("candidates", "candidates"), ("corroborated_vision", "vision corroborated")), ()),
    ("projection", "Projection", (("mapped", "mapped"), ("conflicting", "conflicting"), ("review_needed", "review needed")), ("document_type",)),
    ("page_quality", "Page quality", (), ("pages_lifted",)),
)

STATUS_WORDS = {
    "completed": "completed", "ran": "ran", "pass": "pass", "violation": "violation", "skipped": "skipped",
    "error": "error", "not_run": "not run", "disabled": "disabled", "failed": "failed",
}


def _yes_no(value: Any) -> str:
    return "yes" if value is True else ("no" if value is False else str(value))


def execution_view(report: dict[str, Any]) -> dict[str, Any]:
    """``{recorded, ran_at, steps: [{key, label, recorded, status, status_key,
    counts: [str], meta: [str], reason}]}``. A report without ``execution``
    yields every step as "not recorded" — the panel says so rather than
    guessing from other blocks. ``ran_at`` is the block's own timestamp, else
    ``snapshot.stamped_at``, else None: no time is invented."""
    ex = report.get("execution") if isinstance(report.get("execution"), dict) else None
    snap = report.get("snapshot") if isinstance(report.get("snapshot"), dict) else {}
    ran_at = (ex or {}).get("ran_at") or snap.get("stamped_at") or None
    steps = []
    for key, label, counts, meta_keys in EXECUTION_STEPS:
        block = (ex or {}).get(key) if ex else None
        if not isinstance(block, dict):
            steps.append({"key": key, "label": label, "recorded": False, "status": "not recorded", "status_key": "not_recorded", "counts": [], "meta": [], "reason": None})
            continue
        status_raw = str(block.get("status") or "").lower()
        status_key = status_raw or "not_recorded"
        status = STATUS_WORDS.get(status_raw, status_raw.replace("_", " ") or "not recorded")
        count_words = []
        for k, words in counts:
            v = block.get(k)
            if isinstance(v, bool):
                count_words.append(f"{words} {_yes_no(v)}")
            elif isinstance(v, (int, float)):
                count_words.append(f"{int(v)} {words}")
            elif isinstance(v, (list, tuple)):
                count_words.append(f"{len(v)} {words}")
        meta_words = []
        for k in meta_keys:
            v = block.get(k)
            if v in (None, ""):
                continue
            if k == "ms" and isinstance(v, (int, float)):
                meta_words.append(f"{int(v)} ms")
            elif k in ("improved", "escalate", "human_review"):
                meta_words.append(f"{k.replace('_', ' ')} {_yes_no(v)}")
            else:
                meta_words.append(f"{k.replace('_', ' ')} {v}")
        reasons = block.get("reasons") if isinstance(block.get("reasons"), list) else []
        reason = block.get("reason") or ("; ".join(str(r) for r in reasons) if reasons else None)
        steps.append({"key": key, "label": label, "recorded": True, "status": status, "status_key": status_key,
                      "counts": count_words, "meta": meta_words, "reason": str(reason) if reason else None})
    # The raw pool rides on the execution view so ``/parsing/<id>`` renders it
    # without a new key in the record ``web.py`` assembles (V-B, 2026-09-29).
    return {"recorded": ex is not None, "ran_at": str(ran_at) if ran_at else None, "steps": steps,
            "raw_candidates": raw_candidates_view(report),
            "dynamic_fields": dynamic_fields_view(report),
            "quality_help": quality_help_view(report)}


#: ``source_kind`` in words, the same five ``raw_candidates.SOURCE_KINDS``.
RAW_SOURCE_WORDS = {
    "layout_text": "layout text", "table_cell": "table cell", "image_vision": "image vision",
    "model_read": "model read", "textract": "Textract", "discovery": "discovery",
}
RAW_OUTCOME_WORDS = {"mapped": "mapped", "unmapped": "unmapped", "conflicting": "conflicting", "review_needed": "review needed"}


def raw_candidates_view(report: dict[str, Any]) -> dict[str, Any]:
    """``report.raw_candidates`` for the record page (customer finding V-B,
    2026-09-29: the pool existed since plan V4 but no page showed it, so a
    reviewer could not see what the page says when the schema layer was empty).

    ``{count, recorded, status, stats: [str], rows: [{candidate_id, label,
    raw_text, page, source_kind, source_words, preferred, corroborated,
    outcome, outcome_words, field, node_id, element_id}]}``. ``label`` is
    ``label_anchor`` else ``name_hint``; ``raw_text`` is verbatim. The outcome
    is ``projection.candidates_log[candidate_id]`` (``execution.projection``
    when the report block is absent) and ``none`` when no log names the
    candidate — never inferred from the fields. ``stats`` is
    ``execution.raw_candidates`` in words (status, count, non-zero by-source
    counts, failed sources); ``recorded`` False when that block is missing.
    Candidates carry no confidence and none is derived here."""
    pool = [c for c in (report.get("raw_candidates") or []) if isinstance(c, dict)] if isinstance(report.get("raw_candidates"), list) else []
    ex = report.get("execution") if isinstance(report.get("execution"), dict) else {}
    proj = report.get("projection") if isinstance(report.get("projection"), dict) else (ex.get("projection") if isinstance(ex.get("projection"), dict) else {})
    log = proj.get("candidates_log") if isinstance(proj.get("candidates_log"), dict) else {}
    has_overlap = any(c.get("preferred") is False for c in pool)
    rows = []
    for c in pool:
        entry = log.get(c.get("candidate_id")) if c.get("candidate_id") is not None else None
        outcome = str((entry or {}).get("outcome") or "").lower() if isinstance(entry, dict) else ""
        if outcome not in RAW_OUTCOME_WORDS:
            outcome = "none"
        field = (entry or {}).get("field") if isinstance(entry, dict) else None
        kind = str(c.get("source_kind") or "")
        page = c.get("page")
        if page is None and isinstance(c.get("source_span"), dict):
            page = c["source_span"].get("page")
        rows.append({
            "candidate_id": c.get("candidate_id"),
            "label": str(c.get("label_anchor") or c.get("name_hint") or "—"),
            "raw_text": "" if c.get("raw_text") is None else str(c.get("raw_text")),
            "page": page,
            "source_kind": kind,
            "source_words": RAW_SOURCE_WORDS.get(kind, kind.replace("_", " ") or "unknown source"),
            "preferred": c.get("preferred") is not False,
            "show_preferred": has_overlap and c.get("preferred") is True,
            "corroborated": c.get("corroborated") if isinstance(c.get("corroborated"), bool) else None,
            "uncorroborated": kind == "image_vision" and c.get("corroborated") is False,
            "outcome": outcome,
            "outcome_words": (f"mapped → {field}" if outcome == "mapped" and field else RAW_OUTCOME_WORDS.get(outcome, "no projection")),
            "field": field,
            "node_id": c.get("node_id"),
            "element_id": c.get("element_id"),
        })
    block = ex.get("raw_candidates") if isinstance(ex.get("raw_candidates"), dict) else None
    stats: list[str] = []
    if block:
        status_raw = str(block.get("status") or "").lower()
        if status_raw:
            stats.append(STATUS_WORDS.get(status_raw, status_raw.replace("_", " ")))
        if isinstance(block.get("candidates"), (int, float)):
            stats.append(f"{int(block['candidates'])} candidates")
        by = block.get("by_source") if isinstance(block.get("by_source"), dict) else {}
        for kind in list(RAW_SOURCE_WORDS) + [k for k in by if k not in RAW_SOURCE_WORDS]:
            n = by.get(kind)
            if isinstance(n, (int, float)) and n > 0:
                stats.append(f"{RAW_SOURCE_WORDS.get(kind, str(kind).replace('_', ' '))} {int(n)}")
        failed = block.get("failed_sources") if isinstance(block.get("failed_sources"), dict) else {}
        if failed:
            stats.append("source failed: " + ", ".join(RAW_SOURCE_WORDS.get(k, str(k)) for k in failed))
        if block.get("reason"):
            stats.append(str(block["reason"]))
    return {"count": len(rows), "recorded": block is not None, "status": str(block.get("status") or "") if block else None,
            "stats": stats, "rows": rows}


#: ``dynamic_fields[].projection`` in words and the tone the record page gives it.
DYNAMIC_PROJECTION_TONES = {"mapped": "mapped", "unmapped": "unmapped", "conflicting": "conflicting", "review_needed": "review_needed"}


def dynamic_fields_view(report: dict[str, Any]) -> dict[str, Any]:
    """``report.dynamic_fields`` for the record page (user decision 2026-09-29:
    the PDFs have no known field list, so every key/value pair the page states
    — Textract FORMS or the label/value discovery, built by
    ``raw_candidates.dynamic_fields`` — is listed as a field in its own right,
    in reading order, above the schema's projection).

    ``{count, rows: [{candidate_id, label, value, page, source, source_words,
    preferred, schema_field, projection, projection_words, node_id,
    element_id}]}``. ``label`` and ``value`` are verbatim; ``schema_field`` /
    ``projection`` repeat what the report's list already carries (the
    projection log's outcome for the candidate) and are None when the log never
    named it — nothing is inferred from the schema fields. A pair carries no
    confidence and none is derived here."""
    rows_in = [d for d in (report.get("dynamic_fields") or []) if isinstance(d, dict)] if isinstance(report.get("dynamic_fields"), list) else []
    rows = []
    for d in rows_in:
        label = str(d.get("label") or "").strip()
        value = "" if d.get("value") is None else str(d.get("value"))
        if not label:
            continue
        source = str(d.get("source") or "")
        projection = str(d.get("projection") or "").lower() or None
        if projection not in DYNAMIC_PROJECTION_TONES:
            projection = None
        schema_field = d.get("schema_field") or None
        rows.append({
            "candidate_id": d.get("candidate_id"),
            "label": label,
            "value": value,
            "page": d.get("page"),
            "source": source,
            "source_words": RAW_SOURCE_WORDS.get(source, source.replace("_", " ") or "unknown source"),
            "preferred": d.get("preferred") is not False,
            "schema_field": str(schema_field) if schema_field else None,
            "projection": projection,
            "projection_words": RAW_OUTCOME_WORDS.get(projection, "no projection") if schema_field else None,
            "node_id": d.get("node_id"),
            "element_id": d.get("element_id"),
        })
    return {"count": len(rows), "rows": rows}


def quality_help_view(report: dict[str, Any]) -> dict[str, Any] | None:
    """How the quality figures were computed, for the ``title`` of the score
    on the record page (2026-09-29): ``{lines: ["Page N: <basis>", …],
    pages: {N: "<basis>"}}`` from ``report.pages[].basis`` — the sentence the
    quality probe wrote ("ocr 0.96 × coverage 1.00; grounded reads 0.50 × ocr
    0.96 = floor 0.48 (below the probe score)"). None when no page recorded a
    basis: the hover then says nothing rather than describing a computation
    the report did not write. The lead sentence is the catalog's
    ``shell.fields.quality_help``; the template puts it first."""
    pages = [p for p in (report.get("pages") or []) if isinstance(p, dict)] if isinstance(report.get("pages"), list) else []
    lines: list[str] = []
    by_page: dict[Any, str] = {}
    for p in pages:
        basis = p.get("basis")
        if not basis:
            continue
        lines.append(f"Page {p.get('page')}: {basis}")
        by_page[p.get("page")] = str(basis)
    if not lines:
        return None
    return {"lines": lines, "pages": by_page}


def summary_breakdown(fields: list[dict[str, Any]]) -> dict[str, int]:
    """total / accepted / review / suspect / not_found, counted from the fields
    by the same rules the sections use (suspect is the subset of review whose
    evidence is ``found_suspect``)."""
    out = {"total": 0, "accepted": 0, "review": 0, "suspect": 0, "not_found": 0}
    for f in fields:
        if not isinstance(f, dict):
            continue
        out["total"] += 1
        badge = field_badge(f)["key"]
        if badge == "not_found":
            out["not_found"] += 1
        elif badge == "suspect":
            out["suspect"] += 1
            out["review"] += 1
        elif badge in ("review", "disputed", "rejected"):
            out["review"] += 1
        elif badge == "accepted":
            out["accepted"] += 1
    return out


def tables_view(report: dict[str, Any]) -> list[dict[str, Any]]:
    """``report.tables[]`` as grids: headers, rows, caption, quality words and
    ``marks`` — ``{"r,c": field_name}`` for the cells ``fields_extracted``
    names (dicts with row/col; bare names mark nothing but are listed)."""
    tables = report.get("tables") if isinstance(report.get("tables"), list) else []
    out = []
    for t in tables:
        if not isinstance(t, dict):
            continue
        headers = [str(h) for h in (t.get("headers") or [])]
        rows = [[("" if c is None else str(c)) for c in r] for r in (t.get("rows") or []) if isinstance(r, (list, tuple))]
        marks: dict[str, str] = {}
        names: list[str] = []
        for fe in t.get("fields_extracted") or []:
            if isinstance(fe, dict):
                name = str(fe.get("field") or fe.get("name") or "")
                r, c = fe.get("row"), fe.get("col")
                if name:
                    names.append(name)
                if isinstance(r, int) and isinstance(c, int) and name:
                    marks[f"{r},{c}"] = name
            elif isinstance(fe, str):
                names.append(fe)
        q = t.get("quality") if isinstance(t.get("quality"), dict) else {}
        grid = [[{"text": cell, "field": marks.get(f"{ri},{ci}")} for ci, cell in enumerate(row)] for ri, row in enumerate(rows)]
        out.append({
            "table_id": t.get("table_id"), "node_id": t.get("node_id"), "page": t.get("page"),
            "headers": headers, "rows": rows, "grid": grid, "caption": t.get("caption"),
            "quality": {"status": str(q.get("status") or "") or None, "basis": str(q.get("basis") or "") or None} if q else None,
            "marks": marks, "fields_extracted": names,
        })
    return out


def vision_view(report: dict[str, Any]) -> dict[str, Any] | None:
    """``report.vision`` for the panel: status, model, reason and one entry per
    analyzed page with quality words and its facts. None when absent."""
    v = report.get("vision") if isinstance(report.get("vision"), dict) else None
    if not v:
        return None
    pages = []
    for p in v.get("pages") or []:
        if not isinstance(p, dict):
            continue
        q = p.get("quality") if isinstance(p.get("quality"), dict) else {}
        facts = []
        for fact in p.get("facts") or []:
            if not isinstance(fact, dict):
                continue
            g = fact.get("grounding") if isinstance(fact.get("grounding"), dict) else {}
            bbox = fact.get("bbox") or g.get("bbox")
            facts.append({
                "name": str(fact.get("name") or ""), "value": fact.get("value"),
                "confidence": fact.get("confidence") if isinstance(fact.get("confidence"), (int, float)) else None,
                "evidence": str(fact.get("evidence")) if fact.get("evidence") else None,
                "model": str(fact.get("model")) if fact.get("model") else None,
                "bbox": [float(x) for x in bbox] if isinstance(bbox, (list, tuple)) and len(bbox) == 4 else None,
            })
        pages.append({
            "page": p.get("page"), "kind": p.get("kind"),
            "quality": {"status": str(q.get("status") or "") or None, "flags": [str(f) for f in (q.get("flags") or [])], "basis": str(q.get("basis") or "") or None},
            "facts": facts,
        })
    return {"status": str(v.get("status") or "") or None, "model": str(v.get("model") or "") or None,
            "reason": str(v.get("reason") or "") or None, "pages": pages}


TRIGGER_WORDS = {
    "pipeline:llm_grounded": "LLM grounding pass",
    "pipeline:redhat_targeted": "Red-Hat targeted pass",
    "replay": "Replay",
    "classification_override": "Type changed by reviewer",
}


def rerun_history_view(report: dict[str, Any]) -> dict[str, Any]:
    """The passes in ``replay.history`` (pipeline passes and replays alike):
    when, what triggered it, which fields changed, found before → after,
    improved. ``passes`` from ``replay.passes`` else the row count."""
    rp = report.get("replay") if isinstance(report.get("replay"), dict) else {}
    history = rp.get("history") if isinstance(rp.get("history"), list) else []
    rows = []
    for i, h in enumerate(history, 1):
        if not isinstance(h, dict):
            continue
        trigger = str(h.get("trigger") or "") or None
        changed = h.get("fields_changed") if isinstance(h.get("fields_changed"), list) else []
        rows.append({
            "n": i,
            "at": str(h.get("at") or h.get("replayed_at") or h.get("created_at") or "") or None,
            "trigger": trigger,
            "trigger_words": TRIGGER_WORDS.get(trigger or "", (trigger or "").replace("pipeline:", "").replace("_", " ") or None),
            "fields_changed": [str(c) for c in changed],
            "before": h.get("fields_found_before") if isinstance(h.get("fields_found_before"), (int, float)) else None,
            "after": h.get("fields_found_after") if isinstance(h.get("fields_found_after"), (int, float)) else None,
            "improved": h.get("improved") if isinstance(h.get("improved"), bool) else None,
            "outcome": h.get("outcome") or h.get("status") or h.get("result"),
        })
    passes = rp.get("passes")   # only the report's own count; the row count is not a pass count
    return {"rows": rows, "passes": int(passes) if isinstance(passes, (int, float)) else None}


def discovered_fields_view(report: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for d in report.get("discovered_fields") or []:
        if isinstance(d, dict) and d.get("name"):
            span = d.get("span") if isinstance(d.get("span"), dict) else {}
            out.append({"name": str(d["name"]), "value": d.get("value"), "page": span.get("page")})
    return out


def graph_integrity_view(report: dict[str, Any]) -> dict[str, Any] | None:
    gi = report.get("graph_integrity") if isinstance(report.get("graph_integrity"), dict) else None
    if not gi:
        return None
    score = gi.get("integrity_score")
    neg = gi.get("negative_evidence")
    orphans = gi.get("orphan_list") if isinstance(gi.get("orphan_list"), list) else None
    return {
        "score": float(score) if isinstance(score, (int, float)) else None,
        "negative_evidence": (len(neg) if isinstance(neg, list) else (int(neg) if isinstance(neg, (int, float)) else None)),
        "orphans": [str(o) for o in orphans] if orphans is not None else None,
        "ok": gi.get("ok") if isinstance(gi.get("ok"), bool) else None,
    }


def form_sentence(report: dict[str, Any]) -> str | None:
    """The unfilled-form sentence for a report flagged ``form_template``
    (``v1_orchestrator.form_template_flag``): the sentence the report recorded
    in ``extraction_notes`` ("This looks like an unfilled form: 14 numbered
    captions and 0 filled values."), else the flag's own words. None when the
    report does not carry the flag — the sentence is never composed here."""
    flags = {str(f) for f in (report.get("quality_flags") or [])}
    if "form_template" not in flags:
        return None
    for note in report.get("extraction_notes") or []:
        if isinstance(note, str) and "unfilled form" in note.lower():
            return note.strip()
    return "This looks like an unfilled form."


def form_caption_count(sentence: str | None) -> int | None:
    """The caption count named in the form sentence, or None."""
    import re as _re

    if not sentence:
        return None
    m = _re.search(r"(\d+) numbered caption", sentence)
    return int(m.group(1)) if m else None
