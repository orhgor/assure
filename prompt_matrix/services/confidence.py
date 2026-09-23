"""Confidence aggregation for verified JDF documents.

Score layers are separate and must stay separate:

- ``parse_quality`` — how well the parser read the source document
  (parser-reported parse/OCR confidence; the unknown is ``None`` upstream and
  reported as unknown here, never smoothed into a fake number).
- ``structured_quality`` — what the parse recovered beyond text: tables,
  images, figures, layout.
- ``verification_quality`` — what the audit stack proved afterwards:
  provenance, entailment, Z3, and Red-Hat findings.
- ``document_quality`` — the one number the reader sees, combining the three
  only at the end. Red-Hat feeds verification directly: an open finding
  lowers the node score and names itself in the reason.

Scores are floats in 0..100. A score is a computed estimate; the honest-unknown
rule applies to *confidence pass-through* (parse/OCR values), which stay None
when the parser did not report one — 0 is never substituted for unknown.
"""

from __future__ import annotations

from typing import Any

#: Breakdown keys, fixed order for stable payloads.
BREAKDOWN_KEYS = (
    "parse",
    "ocr",
    "tables",
    "figures",
    "images",
    "layout",
    "provenance",
    "entailment",
    "z3",
    "redhat",
)

_LOW_CONFIDENCE_THRESHOLD = 60.0

#: Unknown-signal baseline. Not a fabricated per-document score: it is the
#: neutral weight a dimension carries when nothing measured it, applied
#: uniformly and named in reasons.
_NEUTRAL = 60.0


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def _to_percent(confidence: float | int | None) -> float | None:
    """A parser confidence (0..1 or 0..100) to a 0..100 score, or None when unknown."""
    if confidence is None:
        return None
    try:
        value = float(confidence)
    except (TypeError, ValueError):
        return None
    if value <= 1.0:
        value *= 100.0
    return _clamp(value)


def _collect_nodes(tree: dict[str, Any]) -> list[dict[str, Any]]:
    """Recursively collect all nodes from JDF body."""
    nodes: list[dict[str, Any]] = []

    def collect(section: Any) -> None:
        if not isinstance(section, dict):
            return
        nodes.append(section)
        for child in section.get("children") or []:
            collect(child)

    for section in tree.get("body") or []:
        collect(section)
    return nodes


def _open_redhat_findings(node: dict[str, Any]) -> list[dict[str, Any]]:
    """Open Red-Hat findings on one node — the durable Red-Hat signal."""
    findings = (node.get("annotations") or {}).get("redhat") or []
    return [
        f
        for f in findings
        if isinstance(f, dict) and str(f.get("status") or "open") == "open"
    ]


def _node_parse_scores(
    node: dict[str, Any], substrate_meta: dict[str, Any] | None
) -> tuple[float | None, float | None]:
    """Parse/OCR confidence for a node: node meta first, substrate meta fallback.

    Both stay ``None`` when nothing anywhere reported one — a truthiness check
    would turn a parser-reported 0.0 into "unknown", which is exactly the
    fabrication this module exists to prevent.
    """
    node_meta = node.get("meta") or {}
    parse_conf = node_meta.get("parse_confidence")
    if parse_conf is None:
        parse_conf = (substrate_meta or {}).get("parse_confidence")
    ocr_conf = node_meta.get("ocr_confidence")
    if ocr_conf is None:
        ocr_conf = (substrate_meta or {}).get("ocr_confidence")
    return _to_percent(parse_conf), _to_percent(ocr_conf)


def _node_provenance_score(node: dict[str, Any]) -> float:
    return 100.0 if node.get("provenance") else 0.0


def _node_entailment_score(node: dict[str, Any]) -> float:
    for prov in node.get("provenance") or []:
        entailment = prov.get("entailment") if isinstance(prov, dict) else None
        if not entailment:
            continue
        verdict = str(entailment.get("verdict") or "").lower()
        if verdict == "supported":
            return 100.0
        if verdict == "partial":
            return 70.0
        if verdict == "not_supported":
            return 30.0
        if verdict == "contradicted":
            return 10.0
    return 0.0


def _node_z3_score(node: dict[str, Any]) -> float:
    z3_ann = (node.get("annotations") or {}).get("z3") or []
    if not z3_ann:
        return _NEUTRAL  # no checks ran: unknown, not failing
    violations = [z for z in z3_ann if z.get("status") == "violation"]
    return 20.0 if violations else 100.0


def _redhat_findings_score(findings: list[dict[str, Any]]) -> tuple[float, list[str]]:
    """Red-Hat contribution: open findings lower the score, by severity."""
    if not findings:
        # No open findings is a clean bill from the Red-Hat pass, not a neutral
        # shrug — the pass ran and said nothing was open.
        return 100.0, []
    high = sum(1 for f in findings if str(f.get("severity") or "").lower() == "high")
    medium = sum(1 for f in findings if str(f.get("severity") or "").lower() == "medium")
    low = len(findings) - high - medium
    score = 100.0 - 35.0 * high - 15.0 * medium - 5.0 * low
    parts = [f"Red-Hat: {len(findings)} open finding(s)"]
    if high:
        parts.append(f"{high} high")
    if medium:
        parts.append(f"{medium} medium")
    return _clamp(score), [" · ".join(parts)]


def _compute_node_confidence(
    node: dict[str, Any], substrate_meta: dict[str, Any] | None = None
) -> tuple[float, str, str]:
    """Confidence for a single node from real signals only.

    Parse/OCR come from reported confidence, provenance/entailment/Z3 from the
    audit annotations, and Red-Hat findings lower the score directly — an open
    finding is a scored, named reason, never a side note.
    """
    parse_score, ocr_score = _node_parse_scores(node, substrate_meta)
    parse_component = parse_score if parse_score is not None else _NEUTRAL
    ocr_component = ocr_score if ocr_score is not None else _NEUTRAL

    provenance_score = _node_provenance_score(node)
    entailment_score = _node_entailment_score(node)
    z3_score = _node_z3_score(node)
    redhat_score, redhat_reasons = _redhat_findings_score(
        _open_redhat_findings(node)
    )

    score = round(
        0.25 * parse_component
        + 0.10 * ocr_component
        + 0.20 * provenance_score
        + 0.15 * entailment_score
        + 0.20 * z3_score
        + 0.10 * redhat_score
    )

    reasons: list[str] = []
    if parse_score is None:
        reasons.append("parse confidence unknown")
    else:
        reasons.append(f"parse confidence {round(parse_score)}")
    if ocr_score is None:
        reasons.append("OCR confidence unknown")
    else:
        reasons.append(f"OCR confidence {round(ocr_score)}")
    if not node.get("provenance"):
        reasons.append("no provenance on this node")
    if entailment_score == 0.0:
        reasons.append("no entailment verdict")
    z3_ann = (node.get("annotations") or {}).get("z3") or []
    if not z3_ann:
        reasons.append("Z3 checks not run")
    elif any(z.get("status") == "violation" for z in z3_ann):
        reasons.append("Z3 violations present")
    reasons.extend(redhat_reasons)

    verdict = score_to_verdict(score)
    return float(score), verdict, "; ".join(reasons)


def score_to_verdict(score: float) -> str:
    if score >= 80:
        return "supported"
    elif score >= 60:
        return "partial"
    elif score >= 40:
        return "not_supported"
    elif score >= 20:
        return "contradicted"
    return "unverified"


def _get_verdict_reason(score: float) -> str:
    if score >= 80:
        return "Source explicitly carries the claim."
    elif score >= 60:
        return "Source supports part of the claim but misses qualifiers or details."
    elif score >= 40:
        return "Source does not carry this claim."
    elif score >= 20:
        return "Source explicitly states the opposite of the claim."
def _parse_quality(
    tree: dict[str, Any], substrate_meta: dict[str, Any] | None
) -> tuple[float, list[str]]:
    """Parse quality from parser-reported confidence only.

    When the parser reported neither parse nor OCR confidence the quality is
    the neutral baseline and the reason says so — the unknown is surfaced, not
    papered over with an optimistic 85.
    """
    meta = tree.get("meta") or {}
    parse_conf = _to_percent((substrate_meta or {}).get("parse_confidence"))
    if parse_conf is None:
        parse_conf = _to_percent(meta.get("parse_confidence"))
    ocr_conf = _to_percent((substrate_meta or {}).get("ocr_confidence"))
    if ocr_conf is None:
        ocr_conf = _to_percent(meta.get("ocr_confidence"))

    reasons: list[str] = []
    if parse_conf is None and ocr_conf is None:
        reasons.append("parse/OCR confidence unknown (parser reported none)")
        return _NEUTRAL, reasons
    if parse_conf is None:
        reasons.append("parse confidence unknown (parser reported none)")
    else:
        reasons.append(f"parser reported parse confidence {round(parse_conf)}")
    if ocr_conf is None:
        reasons.append("OCR confidence unknown (parser reported none)")
    else:
        reasons.append(f"parser reported OCR confidence {round(ocr_conf)}")
    known = [c for c in (parse_conf, ocr_conf) if c is not None]
    return round(sum(known) / len(known)), reasons


def _asset_counts(
    tree: dict[str, Any], substrate_meta: dict[str, Any] | None
) -> dict[str, float | None]:
    """Reported table/image/figure counts, parse metadata over tree nodes.

    Only counts the parse metadata actually names count here; a kind the
    parser never reported falls back to the tree's own nodes so a tree built
    from structured nodes is not read as empty.
    """
    meta = tree.get("meta") or {}
    summary = (substrate_meta or {}).get("asset_summary") or meta.get("asset_summary") or {}
    summary = summary if isinstance(summary, dict) else {}
    nodes = _collect_nodes(tree)
    tree_counts = {
        "tables": sum(1 for n in nodes if n.get("type") == "table"),
        "images": sum(
            1
            for n in nodes
            if n.get("type") == "image"
            and (n.get("meta") or {}).get("asset_kind") != "figure"
        ),
        "figures": sum(
            1
            for n in nodes
            if n.get("type") == "image"
            and (n.get("meta") or {}).get("asset_kind") == "figure"
        ),
    }
    counts: dict[str, float | None] = {}
    for kind in ("tables", "images", "figures"):
        value = (substrate_meta or {}).get(kind + "_count") if substrate_meta else None
        if value is None:
            value = meta.get(kind.rstrip("s") + "_count")
        if value is None:
            value = summary.get(kind.rstrip("s"))
        if value is None:
            value = tree_counts[kind]
        counts[kind] = value
    return counts
def _structured_quality(
    tree: dict[str, Any], substrate_meta: dict[str, Any] | None
) -> tuple[float, list[dict[str, Any]], list[str]]:
    """Structured-content quality from real asset counts, never text guesses.

    A document whose parse found no tables/images/figures reports those as
    missing assets and scores lower — it may genuinely be a text-only
    document, but the score must say what was and was not recovered.
    """
    counts = _asset_counts(tree, substrate_meta)
    has_parse_meta = (
        bool((substrate_meta or {}).get("parser_name"))
        or bool((tree.get("meta") or {}).get("parser_name"))
    )
    reasons: list[str] = []
    missing_assets: list[dict[str, Any]] = []
    score = 40.0
    present_kinds: list[str] = []
    absent_kinds: list[str] = []
    for kind in ("tables", "images", "figures"):
        count = counts[kind]
        if count is not None and count > 0:
            score += 20.0
            present_kinds.append(kind)
        elif count is not None:
            absent_kinds.append(kind.rstrip("s"))
            missing_assets.append({"kind": kind.rstrip("s"), "count": 0})
        else:
            score += 13.33  # not reported: neither present nor missing
    if has_parse_meta and absent_kinds:
        reasons.append("parse found no " + ", ".join(absent_kinds))
    if present_kinds:
        reasons.append("parse carries structured assets: " + ", ".join(present_kinds))
    return _clamp(score), missing_assets, reasons


def _verification_scores(
    nodes: list[dict[str, Any]],
) -> tuple[dict[str, float], list[str]]:
    """Provenance/entailment/Z3/Red-Hat across all nodes — the audit half."""
    n = len(nodes) or 1
    provenance = sum(_node_provenance_score(node) for node in nodes) / n
    entailment = sum(_node_entailment_score(node) for node in nodes) / n
    z3 = sum(_node_z3_score(node) for node in nodes) / n
    redhat_open = 0
    redhat_total = 0.0
    for node in nodes:
        findings = _open_redhat_findings(node)
        redhat_open += len(findings)
        score, _ = _redhat_findings_score(findings)
        redhat_total += score
    redhat = redhat_total / n
    reasons: list[str] = []
    if redhat_open:
        reasons.append(
            f"{redhat_open} open Red-Hat finding(s) lower verification quality"
        )
    if provenance == 0.0:
        reasons.append("no node carries provenance")
    return {
        "provenance": round(provenance, 1),
        "entailment": round(entailment, 1),
        "z3": round(z3, 1),
        "redhat": round(redhat, 1),
    }, reasons


def _layout_score(
    tree: dict[str, Any], substrate_meta: dict[str, Any] | None
) -> tuple[float, list[str]]:
    meta = tree.get("meta") or {}
    layout = (
        _to_percent((substrate_meta or {}).get("layout_confidence"))
        if substrate_meta
        else None
    )
    if layout is None:
        layout = _to_percent(meta.get("layout_confidence"))
    if layout is not None:
        return layout, [f"layout confidence {round(layout)}"]
    return _NEUTRAL, ["layout confidence unknown (parser reported none)"]


def compute_confidence(
    jdf: dict[str, Any], substrate_meta: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Compute the confidence report for a verified JDF document.

    Parse quality, structured quality, and verification quality stay separate
    here; ``document_quality`` combines them once, at the end. Red-Hat is a
    first-class input: its open findings lower both node scores and the
    verification dimension, and every effect is named in ``reasons``.
    """
    meta = jdf.get("meta") or {}
    nodes = _collect_nodes(jdf)

    parse_quality, parse_reasons = _parse_quality(jdf, substrate_meta)
    structured_quality, missing_assets, structured_reasons = _structured_quality(
        jdf, substrate_meta or {}
    )

    node_scores: dict[str, dict[str, Any]] = {}
    low_confidence_nodes: list[dict[str, Any]] = []
    page_buckets: dict[Any, dict[str, float]] = {}
    redhat_total = 0

    for node in nodes:
        score, verdict, reason = _compute_node_confidence(node, substrate_meta)
        node_id = str(node.get("id") or "")
        node_scores[node_id] = {"score": score, "verdict": verdict, "reason": reason}
        redhat_total += len(_open_redhat_findings(node))
        if score < _LOW_CONFIDENCE_THRESHOLD:
            low_confidence_nodes.append(
                {
                    "node_id": node_id,
                    "type": node.get("type"),
                    "score": score,
                    "verdict": verdict,
                    "reason": reason,
                }
            )
        page = node.get("page") or (node.get("meta") or {}).get("page") or "1"
        bucket = page_buckets.setdefault(page, {"total": 0.0, "count": 0})
        bucket["total"] += score
        bucket["count"] += 1

    page_scores_avg = {
        str(page): round(bucket["total"] / bucket["count"], 1)
        for page, bucket in sorted(page_buckets.items(), key=lambda kv: str(kv[0]))
    }
    low_confidence_pages = [
        {"page": page, "score": page_scores_avg[page]}
        for page in page_scores_avg
        if page_scores_avg[page] < _LOW_CONFIDENCE_THRESHOLD
    ]

    verification_dim, verification_reasons = _verification_scores(nodes)
    verification_quality = round(
        0.30 * verification_dim["provenance"]
        + 0.25 * verification_dim["entailment"]
        + 0.25 * verification_dim["z3"]
        + 0.20 * verification_dim["redhat"]
    )
    layout_score, layout_reasons = _layout_score(jdf, substrate_meta)

    def _kind_presence(kind: str) -> float:
        return 100.0 if any(n.get("type") == kind for n in nodes) else 0.0

    def _image_presence() -> float:
        # Raw image assets, distinct from figures.
        return (
            100.0
            if any(
                n.get("type") == "image"
                and (n.get("meta") or {}).get("asset_kind") != "figure"
                for n in nodes
            )
            else 0.0
        )

    def _figure_presence() -> float:
        # Figures are semantic assets — image nodes flagged asset_kind=figure.
        return (
            100.0
            if any(
                n.get("type") == "image"
                and (n.get("meta") or {}).get("asset_kind") == "figure"
                for n in nodes
            )
            else 0.0
        )

    ocr_known = _to_percent(
        (substrate_meta or {}).get("ocr_confidence") if substrate_meta else None
    )
    if ocr_known is None:
        ocr_known = _to_percent(meta.get("ocr_confidence"))
    breakdown = {
        "parse": round(parse_quality, 1),
        "ocr": round(ocr_known if ocr_known is not None else _NEUTRAL, 1),
        "tables": round(_kind_presence("table"), 1),
        "figures": round(_figure_presence(), 1),
        "images": round(_image_presence(), 1),
        "layout": round(layout_score, 1),
        "provenance": verification_dim["provenance"],
        "entailment": verification_dim["entailment"],
        "z3": verification_dim["z3"],
        "redhat": verification_dim["redhat"],
    }

    reasons = list(parse_reasons) + list(structured_reasons) + list(layout_reasons)
    reasons.extend(verification_reasons)
    if redhat_total:
        reasons.append(
            f"Red-Hat: {redhat_total} open finding(s) across the document reduce the score"
        )

    document_quality = round(
        0.35 * parse_quality + 0.20 * structured_quality + 0.45 * verification_quality
    )
    document_quality = float(document_quality)

    return {
        "document_quality": document_quality,
        "parse_quality": round(parse_quality, 1),
        "structured_quality": round(structured_quality, 1),
        "verification_quality": round(verification_quality, 1),
        "breakdown": breakdown,
        "low_confidence_nodes": low_confidence_nodes,
        "low_confidence_pages": low_confidence_pages,
        "missing_assets": missing_assets,
        "reasons": reasons,
        # Legacy aliases so existing readers of `document`/`page_scores` keep
        # working; document_quality is the canonical field now.
        "document": document_quality,
        "page_scores": page_scores_avg,
        "node_scores": node_scores,
    }


def build_confidence_payload(
    jdf: dict[str, Any], substrate_meta: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Build confidence payload for API response and JDF storage.

    ``substrate_meta`` — the vault row's parse metadata (confidence, counts,
    asset summary) — flows into the score when present so the report reflects
    the real parse, not a blank.
    """
    return compute_confidence(jdf, substrate_meta=substrate_meta)


def add_confidence_to_jdf(jdf: dict[str, Any], confidence: dict[str, Any]) -> dict[str, Any]:
    """Write the full confidence report onto the JDF meta.

    Parse quality, structured quality and verification quality are written as
    three separate fields so a reader never has to untangle them from the
    combined ``confidence`` — that combination happened once, in
    ``compute_confidence``, and is not repeated or re-derived here.
    """
    meta = jdf.get("meta") or {}
    meta["confidence"] = confidence.get("document_quality")
    meta["confidenceBreakdown"] = confidence.get("breakdown")
    meta["lowConfidenceNodes"] = confidence.get("low_confidence_nodes")
    meta["lowConfidencePages"] = confidence.get("low_confidence_pages")
    meta["missingAssets"] = confidence.get("missing_assets")
    meta["parseQuality"] = confidence.get("parse_quality")
    meta["structuredQuality"] = confidence.get("structured_quality")
    meta["verificationQuality"] = confidence.get("verification_quality")
    jdf["meta"] = meta
    return jdf