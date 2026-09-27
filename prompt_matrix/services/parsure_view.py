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
    counts = {"review": 0, "found": 0, "not_found": 0}
    for f in fields:
        counts[field_section(f)] += 1
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
