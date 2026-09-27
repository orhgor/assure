"""Unified audit summary for sandbox verify and draft audit_complete SSE.

Since 2026-09-27 the claim layer is counted from ``node.meta.provenance.claim``
(``services/claim_policy``, policy ``claim-v1``), not from the entailment verdict:
``verified`` / ``unsupported`` / ``contradicted`` / ``insufficient`` / ``flagged``
are the customer's buckets, ``supported`` is kept for older readers and equals
``verified``, and the gate reads them. ``partial`` / ``unverified`` / ``anchored``
stay as the entailment- and anchor-layer details they were.
"""

from __future__ import annotations

from typing import Any, Literal

try:
    from .claim_policy import (
        CLAIM_POLICY_ID,
        CONTRADICTED,
        INSUFFICIENT_EVIDENCE,
        VERIFIED,
        attach_claims_to_tree,
        claim_block,
        claim_units,
        derive_claim,
        is_claim_eligible,
        is_meta_block,
        labelled_figures,
    )
    from .confidence_spans import (
        attach_confidence_spans_to_document,
        build_confidence_spans,
        build_macro_appendix,
    )
    from .provenance_meta import attach_provenance_meta_to_tree
except ImportError:
    from services.claim_policy import (
        CLAIM_POLICY_ID,
        CONTRADICTED,
        INSUFFICIENT_EVIDENCE,
        VERIFIED,
        attach_claims_to_tree,
        claim_block,
        claim_units,
        derive_claim,
        is_claim_eligible,
        is_meta_block,
        labelled_figures,
    )
    from services.confidence_spans import (
        attach_confidence_spans_to_document,
        build_confidence_spans,
        build_macro_appendix,
    )
    from services.provenance_meta import attach_provenance_meta_to_tree

GateStatus = Literal["pass", "blocked", "review"]


def _walk_nodes(document: dict[str, Any]):
    for section in document.get("body") or []:
        if not isinstance(section, dict):
            continue
        yield section
        for child in section.get("children") or []:
            if isinstance(child, dict):
                yield child


def _entailment_verdict(node: dict[str, Any]) -> str:
    """``node.meta.provenance.entailment.verdict`` — "" when never checked."""
    meta = node.get("meta")
    prov = meta.get("provenance") if isinstance(meta, dict) else None
    if not isinstance(prov, dict):
        return ""
    record = prov.get("entailment")
    if not isinstance(record, dict):
        return ""
    return str(record.get("verdict") or "")


def _anchoring_quote(node: dict[str, Any]) -> str:
    """The source sentence this node is anchored to — "" when there is none.

    The quote the lexical matcher stamped (``models/jdf.py``); read from the
    node's provenance row, never from ``meta.provenance.excerpt``, which falls
    back to the claim text itself. The entailment check reads the same row's
    ``anchor_window`` — the run of sentences the anchor was scored against — so
    this is the sentence *cited*, not the whole evidence behind it.
    """
    for row in node.get("provenance") or []:
        if isinstance(row, dict) and str(row.get("extracted_quote") or "").strip():
            return str(row["extracted_quote"]).strip()
    return ""


def _claim_for_count(node: dict[str, Any]) -> dict[str, Any]:
    """The node's claim block — persisted, or derived without sources.

    A node no compile has derived a block for (a fixture, a tree cached before the
    policy existed) is judged here on what it carries, with no source corpus:
    unanchored → UNSUPPORTED, anchored → INSUFFICIENT_EVIDENCE ("source not
    supplied"). The derivation is on a copy, so a counter never writes to the tree;
    ``attach_claims_to_tree`` is the writer.
    """
    block = claim_block(node)
    if block is not None:
        return block
    return derive_claim(dict(node, meta=dict(node.get("meta") or {})), sources=None)


def _provenance_counts(document: dict[str, Any]) -> dict[str, int]:
    """Claim-eligible paragraphs, bucketed by grounding, by claim verdict and by
    entailment.

    Three layers, counted separately because they answer different questions:

    ``anchored`` / ``unanchored``   grounding — the paragraph has (or has not) a
                                    source sentence.
    ``verified`` / ``unsupported`` / ``contradicted`` / ``insufficient``
                                    the claim verdict (``claim_policy``, rules in
                                    order; VERIFIED needs a verbatim quote).
    ``flagged``                     claims with any flag (high-risk wording the
                                    quote does not carry, an inconsistent figure).
    ``supported``                   == ``verified``. Kept for readers of the older
                                    payload, where it counted ``yes`` OR ``partial``;
                                    since 2026-09-27 ``partial`` is not verified.
    ``partial`` / ``unverified``    the entailment layer's own detail buckets.
    ``unchecked``                   anchored but never entailment-checked; feeds the
                                    reason text only.
    """
    counts = {
        "eligible": 0,
        "anchored": 0,
        "supported": 0,
        "partial": 0,
        "unsupported": 0,
        "unanchored": 0,
        "unverified": 0,
        "unchecked": 0,
        "verified": 0,
        "contradicted": 0,
        "insufficient": 0,
        "flagged": 0,
        # Not claims: statements about the draft or the source (``kind: meta``).
        # Counted apart so a memo template's "missing items" / "confidence"
        # sections do not hold every document at review (2026-09-27).
        "meta": 0,
        # The sentence-level layer: the claim unit is the sentence, and a
        # paragraph of several sentences contributes each of them here.
        "sentences": 0,
        "sentences_verified": 0,
        "sentences_unsupported": 0,
        "sentences_contradicted": 0,
        "sentences_insufficient": 0,
        "sentences_flagged": 0,
    }
    for node in _walk_nodes(document):
        if str(node.get("type") or "") != "paragraph":
            continue
        if not is_claim_eligible(str(node.get("content") or "")):
            continue
        block = _claim_for_count(node)
        if is_meta_block(block):
            counts["meta"] += 1
            continue
        counts["eligible"] += 1
        if _anchoring_quote(node):
            counts["anchored"] += 1
        else:
            counts["unanchored"] += 1
        verdict = _entailment_verdict(node)
        if verdict == "partial":
            counts["partial"] += 1
        elif verdict == "unverified":
            counts["unverified"] += 1
        elif not verdict and node.get("provenance"):
            counts["unchecked"] += 1
        claim_verdict = str(block.get("verdict") or "")
        if claim_verdict == VERIFIED:
            counts["verified"] += 1
        elif claim_verdict == CONTRADICTED:
            counts["contradicted"] += 1
        elif claim_verdict == INSUFFICIENT_EVIDENCE:
            counts["insufficient"] += 1
        else:
            counts["unsupported"] += 1
        if block.get("flags"):
            counts["flagged"] += 1
        for unit in claim_units(block):
            counts["sentences"] += 1
            unit_verdict = str(unit.get("verdict") or "")
            if unit_verdict == VERIFIED:
                counts["sentences_verified"] += 1
            elif unit_verdict == CONTRADICTED:
                counts["sentences_contradicted"] += 1
            elif unit_verdict == INSUFFICIENT_EVIDENCE:
                counts["sentences_insufficient"] += 1
            else:
                counts["sentences_unsupported"] += 1
            if unit.get("flags"):
                counts["sentences_flagged"] += 1
    counts["supported"] = counts["verified"]
    return counts


# The buckets a payload reports. `unchecked` is deliberately not among them: it
# only feeds the reason text, so it never reaches a surface as a number.
_PROVENANCE_REPORTED = (
    "eligible",
    "anchored",
    "supported",
    "partial",
    "unsupported",
    "unanchored",
    "unverified",
    "verified",
    "contradicted",
    "insufficient",
    "flagged",
    "meta",
)


def _reported_stats(counts: dict[str, int]) -> dict[str, int]:
    """``_provenance_counts`` projected onto the reported buckets, in one shape.

    Every surface that reports provenance counters (the audit summary, the
    export gate, a replayed compile) builds them here, so a payload persisted or
    cached by an earlier compile cannot hand a surface numbers the current tree
    no longer supports.
    """
    return {key: int(counts.get(key) or 0) for key in _PROVENANCE_REPORTED}


def _eligible_and_anchored(document: dict[str, Any]) -> tuple[int, int]:
    """Count claim-eligible paragraph nodes and how many carry a source sentence.

    Eligibility is ``claim_policy.is_claim_eligible``: the matcher's floor, or two
    content tokens with a figure, a date, an exclusion word or a high-risk term. Headers/stubs are
    excluded. Anchored means the paragraph has a matched source sentence (the
    matcher's quote) — its *truthfulness* is the separate verdict read from
    ``provenance_stats.supported``; see ``_provenance_counts``.
    """
    counts = _provenance_counts(document)
    return counts["eligible"], counts["anchored"]


def _finding_counts(document: dict[str, Any] | None) -> dict[str, int]:
    """Red-Hat findings on the tree, by state — the history beside the counters.

    ``provenance_stats`` counts claims in the document as it stands; it cannot
    say that a claim was once unsupported and a revision answered the warning,
    because the paragraph that carried the finding was rewritten. Measured on the
    demo project: applying the one open finding took the document from
    ``8 anchored / 7 supported / 1 unsupported`` to ``7 anchored / 7 supported / 0
    unsupported / 1 unanchored``, and the finding itself was gone from the tree —
    a document with an open warning and then, one revision later, a document that
    reads as if it never had one.

    So the findings are counted beside the claims, from the tree's own
    annotations:

    ``open``        a warning nothing has answered yet.
    ``resolved``    a revision rewrote the paragraph it was raised on
                    (``models.jdf.resolve_findings_on_rewrite``), which is what
                    ``resolved_by_revision_id`` / ``resolved_by_version`` /
                    ``resolved_by_mutation_type`` record.
    ``remediated_unsupported``
                    of those, the ones raised on a paragraph the audit read as
                    unsupported (verdict ``no``, or a citation contradicted). This
                    is the number that says the fall in ``unsupported`` was work
                    done, not an audit that came back clean.
    ``dismissed``   closed without a rewrite.
    ``remediated_unanchored``
                    of the resolved ones, the paragraphs that held a citation
                    when the finding was raised and hold none now — the anchor the
                    rewrite did not inherit, so the count of unsupported claims
                    fell because a paragraph lost its source rather than because
                    one was grounded. The document total is
                    ``provenance_stats.unanchored``; this is the part of it that
                    remediation explains, which the total cannot say.
    """
    counts = {
        "open": 0,
        "resolved": 0,
        "remediated_unsupported": 0,
        "dismissed": 0,
        "remediated_unanchored": 0,
    }
    for node in _walk_nodes(document or {}):
        for finding in (node.get("annotations") or {}).get("redhat") or []:
            if not isinstance(finding, dict):
                continue
            status = str(finding.get("status") or "open")
            if status not in counts:
                continue
            counts[status] += 1
            if status != "resolved":
                continue
            if str(finding.get("prior_verdict") or "") == "no" or finding.get(
                "prior_contradicted"
            ):
                counts["remediated_unsupported"] += 1
            if str(finding.get("prior_anchor_quote") or "").strip() and not _anchoring_quote(node):
                counts["remediated_unanchored"] += 1
    return counts


def compute_gate_status(
    z3_status: str | None,
    redhat_count: int,
    unsupported_count: int = 0,
    *,
    contradicted: int = 0,
    insufficient: int = 0,
    flagged: int = 0,
    verified: int | None = None,
) -> GateStatus:
    """Z3 + Red-Hat + claim counts -> one pre-flight gate state.

    * Z3 ``VIOLATION``                                   → ``blocked``
    * any contradicted / unsupported / insufficient /
      flagged claim, or an open Red-Hat finding          → ``review``
    * ``verified == 0`` when the caller reports it       → ``review`` (a document
      with nothing verified is not a pass, however clean)
    * otherwise                                          → ``pass``

    Z3 ``SKIPPED`` (no metric to check) no longer holds the gate on its own: the
    claim layer is the verification, and a document whose every claim is VERIFIED
    against its source is a pass whether or not it carried a metric (rule of
    2026-09-27). Before 2026-09-23 this read only Z3 and the Red-Hat count, so a
    document with 1 supported and 5 contradicted paragraphs went out as ``pass``.
    """
    status = (z3_status or "").upper()
    if status == "VIOLATION":
        return "blocked"
    if redhat_count > 0 or unsupported_count > 0 or contradicted > 0 or insufficient > 0 or flagged > 0:
        return "review"
    if verified is not None and verified <= 0:
        return "review"
    if verified is None and status != "PASS":
        # Legacy call shape (no claim counts): only a Z3 PASS earns the gate.
        return "review"
    return "pass"


def _flag_inconsistent_figures(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Within-document consistency: the same labelled figure stated with two values.

    "The premium is $1,250" in one claim and "the premium of $1,500" in another
    both get ``inconsistent_figure:premium`` (a flag, not a verdict change — the
    source decides which is right, and each claim's own verdict already says
    whether the source carries it). Only persisted claim blocks are flagged; the
    list returned is what ``claim_summary.inconsistencies`` reports.
    """
    seen: dict[tuple[str, str], dict[str, list[str]]] = {}
    nodes_by_id: dict[str, dict[str, Any]] = {}
    for node in _walk_nodes(document):
        if str(node.get("type") or "") != "paragraph":
            continue
        content = str(node.get("content") or "")
        block = claim_block(node)
        if not is_claim_eligible(content) or block is None or is_meta_block(block):
            continue
        node_id = str(node.get("id") or "")
        nodes_by_id[node_id] = node
        for label, kind, value in labelled_figures(content):
            seen.setdefault((label, kind), {}).setdefault(value, [])
            if node_id not in seen[(label, kind)][value]:
                seen[(label, kind)][value].append(node_id)
    out: list[dict[str, Any]] = []
    for (label, kind), values in seen.items():
        if len(values) < 2:
            continue
        node_ids = sorted({nid for ids in values.values() for nid in ids})
        if len(node_ids) < 2:
            # One paragraph stating two values for one label is not a cross-claim
            # inconsistency (a range, or before/after); it is left to entailment.
            continue
        out.append({"label": label, "kind": kind, "values": sorted(values), "node_ids": node_ids})
        flag = f"inconsistent_figure:{label}"
        for nid in node_ids:
            block = claim_block(nodes_by_id[nid])
            if block is not None and flag not in (block.get("flags") or []):
                block["flags"] = [*(block.get("flags") or []), flag]
    return out


def _claim_summary(counts: dict[str, int], inconsistencies: list[dict[str, Any]]) -> dict[str, Any]:
    """The customer's numbers, per sentence: ``total`` counts claim units
    (sentences; a one-sentence paragraph is one), ``paragraphs`` the claim-bearing
    paragraphs behind them, ``meta`` the paragraphs that are statements about the
    draft or the source and are not claims."""
    return {
        "total": int(counts.get("sentences") or 0),
        "verified": int(counts.get("sentences_verified") or 0),
        "unsupported": int(counts.get("sentences_unsupported") or 0),
        "contradicted": int(counts.get("sentences_contradicted") or 0),
        "insufficient": int(counts.get("sentences_insufficient") or 0),
        "flagged": int(counts.get("sentences_flagged") or 0),
        "paragraphs": int(counts.get("eligible") or 0),
        "meta": int(counts.get("meta") or 0),
        "policy": CLAIM_POLICY_ID,
        "inconsistencies": inconsistencies,
    }


def provenance_gate_fields(
    *,
    document: dict[str, Any] | None,
    z3_status: str,
    redhat_count: int,
    has_substrate: bool | None,
    sources: list[dict[str, Any]] | None = None,
    carry_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The provenance-derived fields of an audit payload: the stats, the claim
    summary and the gate.

    THE counter is ``_provenance_counts`` (projected by ``_reported_stats``), and
    this is the only writer of the stats and the verdict they drive — so a fresh
    compile, a compile replayed from cache and an export of a persisted gate all
    report the same numbers for the same tree.

    With ``sources`` (the substrate rows the compile was handed) every eligible
    claim is re-derived against them (``claim_policy.attach_claims_to_tree``), so a
    replayed tree is judged under the current policy. Without them the persisted
    blocks are kept and only missing ones are derived — as "source not supplied",
    never as verified.

    ``ok`` iff Z3 is not VIOLATION and every eligible claim is VERIFIED with no
    flag (and there is at least one). ``unverified_reason`` names what holds the
    gate: contradicted first, then unsupported, insufficient, flagged; a document
    with no anchor at all keeps the older grounding reasons, which say whether
    the sources were missing or simply unmatched.
    """
    inconsistencies: list[dict[str, Any]] = []
    if isinstance(document, dict):
        attach_claims_to_tree(document, sources=sources, carry_plan=carry_plan, only_missing=sources is None)
        inconsistencies = _flag_inconsistent_figures(document)
        counts = _provenance_counts(document)
    else:
        counts = {key: 0 for key in (*_PROVENANCE_REPORTED, "unchecked")}
    eligible = counts["eligible"]
    verified = counts["verified"]
    unsupported = counts["unsupported"]
    contradicted = counts["contradicted"]
    insufficient = counts["insufficient"]
    flagged = counts["flagged"]
    summary = _claim_summary(counts, inconsistencies)
    if isinstance(document, dict):
        meta = document.get("meta")
        if not isinstance(meta, dict):
            meta = document["meta"] = {}
        meta["claim_summary"] = summary
    fields: dict[str, Any] = {
        "provenance_stats": _reported_stats(counts),
        "claim_summary": summary,
        "gate_status": compute_gate_status(
            z3_status,
            redhat_count,
            unsupported,
            contradicted=contradicted,
            insufficient=insufficient,
            flagged=flagged,
            verified=verified,
        ),
        "ok": (
            str(z3_status or "").upper() != "VIOLATION"
            and eligible > 0
            and verified == eligible
            and flagged == 0
        ),
        # The claims' history, beside the claims' state: `provenance_stats` counts
        # the tree as it stands, so the revision that answered a finding reads
        # there only as a paragraph that stopped being unsupported. See
        # `_finding_counts`.
        "findings": _finding_counts(document),
    }
    if fields["ok"]:
        return fields

    if document is None:
        reason = "No document to inspect."
    elif counts["anchored"] == 0:
        # Nothing to judge: the older grounding reasons say whether the sources
        # were missing or simply unmatched.
        if has_substrate is True:
            reason = f"0 of {eligible} claims matched any source sentence."
        elif has_substrate is False:
            reason = "No sources included in this compile — output is ungrounded."
        elif eligible == 0:
            reason = "No sources uploaded — compile is ungrounded."
        else:
            reason = f"0 of {eligible} claims matched any source sentence."
    else:
        bits = []
        if contradicted:
            bits.append(f"{contradicted} claims contradicted by their source")
        if unsupported:
            bits.append(f"{unsupported} unsupported")
        if insufficient:
            bits.append(f"{insufficient} with insufficient evidence")
        if counts["unchecked"]:
            bits.append(f"{counts['unchecked']} anchored but never checked")
        if flagged:
            bits.append(f"{flagged} flagged for wording or an inconsistent figure")
        reason = f"{verified} of {eligible} claims verified" + (
            f" ({', '.join(bits)})." if bits else "."
        )
    fields["unverified"] = True
    fields["unverified_reason"] = reason
    return fields


def build_audit_summary(
    *,
    z3_results: dict[str, Any],
    redhat_critiques: list[dict[str, Any]],
    nodes: list[Any] | None = None,
    locks: list[Any] | None = None,
    document: dict[str, Any] | None = None,
    has_substrate: bool | None = None,
    node_count: int | None = None,
    lock_count: int | None = None,
    sources: list[dict[str, Any]] | None = None,
    carry_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Canonical audit payload shared by sandbox verify and draft audit_complete.

    ``sources`` / ``carry_plan`` are handed to ``provenance_gate_fields`` so the
    claim blocks are derived against the documents the compile was given.
    """
    z3_status = str(z3_results.get("status") or "SKIPPED")
    redhat_count = len(redhat_critiques)

    summary: dict[str, Any] = {
        "ok": False,
        "gate_status": compute_gate_status(z3_status, redhat_count),
        "z3_status": z3_status,
        "z3_results": z3_results,
        "redhat_critiques": redhat_critiques,
        "redhat_count": redhat_count,
        "redhat_results": redhat_critiques,
    }
    if nodes is not None:
        summary["nodes"] = nodes
        summary["node_count"] = node_count if node_count is not None else len(nodes)
    if locks is not None:
        summary["locks"] = locks
        summary["lock_count"] = lock_count if lock_count is not None else len(locks)

    if document is not None:
        spans = build_confidence_spans(document, z3_results=z3_results)
        document = attach_confidence_spans_to_document(document, spans)
        # ``attach_provenance_meta_to_tree`` replaces ``meta.provenance`` wholesale
        # and carries only ``entailment`` across; the claim block is restored after
        # it so a persisted verdict is not lost to the rebuild.
        claims_before = {
            str(node.get("id") or ""): claim_block(node) for node in _walk_nodes(document)
        }
        document = attach_provenance_meta_to_tree(document, z3_results=z3_results)
        for node in _walk_nodes(document):
            prior = claims_before.get(str(node.get("id") or ""))
            if prior is not None and claim_block(node) is None:
                meta = dict(node.get("meta") or {})
                prov = dict(meta.get("provenance") or {})
                prov["claim"] = prior
                meta["provenance"] = prov
                node["meta"] = meta
        appendix = build_macro_appendix(
            document,
            z3_results=z3_results,
            redhat_critiques=redhat_critiques,
            confidence_spans=spans,
        )
        summary["document"] = document
        summary["confidenceSpans"] = spans
        summary["confidence_spans"] = spans
        summary["audit_manifest"] = appendix
        summary["claims"] = appendix

    # The provenance layer — `supported` (a claim its citations carry, in whole
    # or in part) and the gate verdict it drives — is written in exactly one
    # place, from the document just attached, so a replayed or exported payload
    # can be rewritten by the same rule. See `provenance_gate_fields`.
    summary.update(
        provenance_gate_fields(
            document=document,
            z3_status=z3_status,
            redhat_count=redhat_count,
            has_substrate=has_substrate,
            sources=sources,
            carry_plan=carry_plan,
        )
    )
    return summary


def normalize_audit_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize mixed field names from legacy clients or responses."""
    out = dict(data)
    critiques = out.get("redhat_critiques")
    if critiques is None:
        critiques = out.get("redhat_results") or []
    out["redhat_critiques"] = critiques
    out["redhat_results"] = critiques
    out["redhat_count"] = int(
        out.get("redhat_count") if out.get("redhat_count") is not None else len(critiques)
    )
    z3 = out.get("z3_results") or {}
    z3_status = out.get("z3_status") or z3.get("status")
    out["z3_status"] = z3_status
    stats = out.get("provenance_stats") if isinstance(out.get("provenance_stats"), dict) else {}
    unsupported = int(stats.get("unsupported") or 0)
    contradicted = int(stats.get("contradicted") or 0)
    insufficient = int(stats.get("insufficient") or 0)
    flagged = int(stats.get("flagged") or 0)
    if "gate_status" not in out:
        out["gate_status"] = compute_gate_status(
            str(z3_status) if z3_status else None,
            out["redhat_count"],
            unsupported,
            contradicted=contradicted,
            insufficient=insufficient,
            flagged=flagged,
        )
    if "ok" not in out:
        out["ok"] = (
            str(z3_status or "").upper() == "PASS"
            and unsupported == 0
            and contradicted == 0
            and insufficient == 0
            and flagged == 0
        )
    return out
