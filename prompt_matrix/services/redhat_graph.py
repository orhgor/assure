"""Automated Red-Hat over the intake evidence graph (policy ``rh-graph-v1``).

The customer's benchmark plan (2026-09-26) makes Red-Hat a first-class
adversarial critique of the *structured* evidence a Parsure intake report
carries — not of the prose a model wrote. This module reads one saved report
(``v1_orchestrator.build_report``'s shape: ``classification``, ``fields[]``
with their anchors and three confidences, ``pages[]`` with quality and OCR,
``conflicts[]``, ``graph_integrity``, ``replay``), the JDF tree when there is
one, the corrections / disputes / events recorded against the report, and
the export state when an export is being judged, and returns findings a
reviewer can act on. It is a second Red-Hat pass, distinct from
``tasks/redhat.py`` (the multipass audit of a compiled draft): that one asks
a model to argue with a draft; this one checks the graph the draft will be
built on. The dossier lists both, labelled "intake graph critique" and
"draft audit".

Rules first, model second. Every check in ``RULES`` is a deterministic
function over the report — the signal it reads is stated in its docstring
and every finding names the rule — so the same report yields the same
findings on every run. The one model-assisted check
(``unsupported_claim_check``) asks the ``REDHAT`` policy model, over the
found fields and the page text they anchor to, which values the quoted text
does not support; it keeps an answer only when the model's quote is found
verbatim in that page's text (``llm_extraction.find_verbatim``, the same
grounding rule the extractor applies) and drops the rest with a note. It is
bounded (``LLM_TIMEOUT_S``), off with ``PARSURE_REDHAT_LLM=0``, and never
raises: a backend that is missing or slow is one line in
``report["redhat"]["notes"]``.

What a finding is::

    {"id": "rh-structural-1", "rule": "wrong_document_family", "policy": "rh-graph-v1",
     "title": "...", "severity": "high" | "medium" | "low",
     "class": "structural" | "evidentiary" | "export",
     "anchor": {"node_id", "element_id", "page", "field", "kind", ["note"]},
     "rationale": "one plain sentence"}

The anchor is never empty: a finding about a field names that field's node
and page, a finding about a page names the page's first node, and a finding
about the document as a whole names the document root and says so
(``anchor.kind = "document_root"``, ``anchor.note``). Findings are not
"open"/"closed" in V1 — there is no dismiss action — so a high finding on a
report stays a review item until the report is re-read
(``derive_trust_state`` treats one as ``review_required``, never
``verified``).

``critique_report`` is called by ``v1_orchestrator.run_after_parse`` once
the report is built and its conflicts attached; ``attach_findings`` writes
the block into ``report["redhat"]`` and prepends the high findings to
``review_summary.reasons`` so the card, the queue and the record page read
them without knowing this module. Failure isolation matches the
orchestrator's: nothing here raises into the ingest.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import datetime, timezone
from typing import Any, Callable

try:
    from . import llm_extraction as lx
except ImportError:
    import llm_extraction as lx  # type: ignore

log = logging.getLogger(__name__)

POLICY = "rh-graph-v1"
SEVERITIES = ("high", "medium", "low")
CLASSES = ("structural", "evidentiary", "export")
#: Hard wall-clock bound for the one model call (seconds). The local
#: qwen2.5:1.5b answers in 2–6 s; a stalled provider must not hold the
#: ingest worker longer than the parse itself.
LLM_TIMEOUT_S = 45
#: Page quality below this is low-quality evidence (the card's "Page quality
#: is low" threshold in web.parsing_page, 2026-09-25).
PAGE_QUALITY_LOW = 0.5
#: jdf-cli OCR confidence below this on a page that carries found fields.
OCR_LOW = 0.7
#: A page with more text than this whose found fields all sit on one node is
#: chunked too coarsely to show field evidence (``TEXT_DENSITY_FULL_CHARS``
#: in the orchestrator: one declarations page is 1,500–3,000 characters).
COARSE_CHUNK_CHARS = 1500
#: Confidence flattening: this share of the found fields (of at least
#: ``FLAT_MIN_FOUND``) carrying one identical extraction confidence.
FLAT_SHARE = 0.8
FLAT_MIN_FOUND = 3
LOW_QUALITY_FLAGS = frozenset({"low_res", "blurry", "low_contrast", "no_text"})
AMBIGUOUS_SIGNATURE = frozenset({"questionable", "faint", "incomplete", "stamped"})
#: Export language that claims more than a non-verified state can carry
#: (the customer's "Formal Verification Certificate" of 2026-09-26).
CERTIFICATE_WORDS = re.compile(r"\b(certificate|certified|certif\w*|formally verified|guarantee[sd]?)\b", re.I)
FALLBACK_RENDERERS = frozenset({"text", "fallback", "text_fallback", "pdf_ast_text"})
#: Characters of page text offered to the model per page and in total; the
#: REDHAT policy caps input at 8,000 tokens (cost_governance.MAX_INPUT_TOKENS).
LLM_PAGE_CHARS = 6000
LLM_TOTAL_CHARS = 20000


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _num(value: Any) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _words(name: Any) -> str:
    return str(name or "").replace("_", " ").strip() or "field"


# --------------------------------------------------------------------------
# Context and anchors
# --------------------------------------------------------------------------


@dataclass
class Context:
    report: dict[str, Any]
    tree: dict[str, Any] | None = None
    corrections: list[dict[str, Any]] = dc_field(default_factory=list)
    disputes: list[dict[str, Any]] = dc_field(default_factory=list)
    events: list[dict[str, Any]] = dc_field(default_factory=list)
    export_state: dict[str, Any] | None = None

    @property
    def fields(self) -> list[dict[str, Any]]:
        return [f for f in (self.report.get("fields") or []) if isinstance(f, dict)]

    @property
    def found(self) -> list[dict[str, Any]]:
        return [f for f in self.fields if f.get("value") is not None]

    @property
    def pages(self) -> list[dict[str, Any]]:
        return [p for p in (self.report.get("pages") or []) if isinstance(p, dict)]

    @property
    def classification(self) -> dict[str, Any]:
        c = self.report.get("classification")
        return c if isinstance(c, dict) else {}

    def tree_node_ids(self) -> set[str]:
        ids: set[str] = set()
        if not isinstance(self.tree, dict):
            return ids
        stack = list(self.tree.get("body") or [])
        while stack:
            node = stack.pop()
            if not isinstance(node, dict):
                continue
            if node.get("id"):
                ids.add(str(node["id"]))
            stack.extend(node.get("children") or [])
        return ids

    def tree_root_id(self) -> str | None:
        if not isinstance(self.tree, dict):
            return None
        for node in self.tree.get("body") or []:
            if isinstance(node, dict) and node.get("id"):
                return str(node["id"])
        return None


def root_anchor(ctx: Context, *, page: int | None = None, field: str | None = None) -> dict[str, Any]:
    """The document root, named as such: the tree's first body node when
    there is a tree, else the report's document id, else its report id."""
    report = ctx.report
    node = ctx.tree_root_id() or report.get("document_id") or report.get("report_id") or "document"
    return {
        "node_id": str(node),
        "element_id": None,
        "page": page,
        "field": field,
        "kind": "document_root",
        "note": "no closer node on the graph; the document root is named",
    }


def field_anchor(ctx: Context, f: dict[str, Any]) -> dict[str, Any]:
    """The node a field's value sits on (or was searched from), its jdf-cli
    element id and page; the document root when the field carries none."""
    span = f.get("source_span") if isinstance(f.get("source_span"), dict) else {}
    evidence = f.get("evidence") if isinstance(f.get("evidence"), dict) else {}
    page = span.get("page")
    if page is None:
        pages = [p for p in (span.get("pages") or []) if isinstance(p, int)]
        page = pages[0] if pages else evidence.get("page")
    node = span.get("node_id") or f.get("tree_node_id") or f.get("field_source_node_id")
    if not node:
        return root_anchor(ctx, page=page if isinstance(page, int) else None, field=f.get("name"))
    return {
        "node_id": str(node),
        "element_id": str(f.get("field_source_node_id")) if f.get("field_source_node_id") else None,
        "page": page if isinstance(page, int) else None,
        "field": f.get("name"),
        "kind": "field",
    }


def page_anchor(ctx: Context, page_no: int) -> dict[str, Any]:
    """The first node the layout records on that page, else the first found
    field's node on it, else the document root with the page number."""
    layout = ctx.report.get("_layout")
    if isinstance(layout, list) and 0 < page_no <= len(layout):
        for seg in layout[page_no - 1] or []:
            if isinstance(seg, dict) and seg.get("node_id"):
                return {"node_id": str(seg["node_id"]), "element_id": str(seg["node_id"]), "page": page_no, "field": None, "kind": "page"}
    for f in ctx.found:
        a = field_anchor(ctx, f)
        if a.get("page") == page_no and a.get("kind") == "field":
            return {**a, "field": None, "kind": "page"}
    return root_anchor(ctx, page=page_no)


def anchor_label(anchor: dict[str, Any] | None) -> str:
    """"Page 2 · el-3", "Field insured name · p1e0", "Document root · doc-1"."""
    a = anchor if isinstance(anchor, dict) else {}
    node = a.get("element_id") or a.get("node_id")
    parts: list[str] = []
    if a.get("field"):
        parts.append(f"Field {_words(a['field'])}")
    if a.get("page") is not None:
        parts.append(f"Page {a['page']}")
    if a.get("kind") == "document_root":
        # The fallback anchor says so: the field / page it concerns (when
        # known) and then the root it hangs off, never a bare node id.
        parts.append(f"Document root · {node}" if node else "Document root")
    elif node:
        parts.append(str(node))
    return " · ".join(parts) or "Document"


def _finding(rule: str, cls: str, severity: str, title: str, rationale: str, anchor: dict[str, Any], **extra: Any) -> dict[str, Any]:
    assert cls in CLASSES and severity in SEVERITIES
    out = {"rule": rule, "policy": POLICY, "title": title, "severity": severity, "class": cls, "anchor": anchor, "rationale": rationale}
    out.update(extra)
    return out


# --------------------------------------------------------------------------
# Rules — each reads one signal, each returns zero or more findings
# --------------------------------------------------------------------------


def wrong_document_family(ctx: Context) -> list[dict[str, Any]]:
    """Reads ``classification.validation.agrees`` and
    ``classification.schema_mismatch``. The type's family contradicting the
    page's cues is high (the schema is wrong for the page); a mismatch with
    no forced schema (``<family>_unknown``) is medium — nothing wrong was
    extracted, but no schema fits yet."""
    c = ctx.classification
    validation = c.get("validation") if isinstance(c.get("validation"), dict) else {}
    agrees = validation.get("agrees")
    mismatch = bool(c.get("schema_mismatch"))
    if agrees is not False and not mismatch:
        return []
    doc_type = c.get("document_type") or "uncertain"
    override = next((e for e in ctx.events if e.get("event_type") == "classification_overridden"), None)
    if agrees is False:
        family = validation.get("family") or "unknown"
        type_family = validation.get("type_family") or "unknown"
        rationale = (f"The page's cues name the {_words(family)} family but the type {_words(doc_type)} belongs to {_words(type_family)}"
                     + ("; the type was set by a reviewer" if override else "") + ".")
        return [_finding("wrong_document_family", "structural", "high", "Document family disagrees with the type", rationale, root_anchor(ctx))]
    suggestion = c.get("suggestion") if isinstance(c.get("suggestion"), dict) else {}
    hint = f"; the nearest schema is {_words(suggestion.get('document_type'))}" if suggestion.get("document_type") else ""
    return [_finding("wrong_document_family", "structural", "medium", "No schema of the page's family fits",
                     f"The report is typed {_words(doc_type)} with schema_mismatch set: its fields are not applicable to this page{hint}.", root_anchor(ctx))]


def _page_char_counts(ctx: Context) -> dict[int, int]:
    counts: dict[int, int] = {}
    texts = ctx.report.get("_page_texts")
    if isinstance(texts, list):
        for i, t in enumerate(texts):
            counts[i + 1] = len(str(t or "").strip())
        return counts
    for p in ctx.pages:
        no = p.get("page")
        density = _num(p.get("text_density"))
        if isinstance(no, int) and density is not None:
            counts[no] = int(round(density * COARSE_CHUNK_CHARS))
    return counts


def _page_element_counts(ctx: Context) -> dict[int, int]:
    counts: dict[int, int] = {}
    layout = ctx.report.get("_layout")
    if isinstance(layout, list):
        for i, segs in enumerate(layout):
            counts[i + 1] = len([s for s in (segs or []) if isinstance(s, dict) and s.get("node_id")])
    return counts


def coarse_chunking(ctx: Context) -> list[dict[str, Any]]:
    """Reads the found fields' ``field_source_node_id`` per page against the
    page's element count (``_layout``) or text length (``_page_texts`` /
    ``pages[].text_density``). Two or more found fields on one page sharing a
    single node while the page holds more than one element or more than
    ``COARSE_CHUNK_CHARS`` characters means the chunk hides where each value
    sits — medium, structural."""
    by_page: dict[int, list[dict[str, Any]]] = {}
    for f in ctx.found:
        a = field_anchor(ctx, f)
        if a.get("kind") != "field" or a.get("page") is None:
            continue
        by_page.setdefault(int(a["page"]), []).append(f)
    chars = _page_char_counts(ctx)
    elements = _page_element_counts(ctx)
    out: list[dict[str, Any]] = []
    for page_no, fields in sorted(by_page.items()):
        if len(fields) < 2:
            continue
        nodes = {str(f.get("field_source_node_id") or f.get("tree_node_id")) for f in fields}
        if len(nodes) != 1:
            continue
        n_el, n_chars = elements.get(page_no, 0), chars.get(page_no, 0)
        if n_el <= 1 and n_chars <= COARSE_CHUNK_CHARS:
            continue
        why = f"{n_el} elements" if n_el > 1 else f"{n_chars:,} characters"
        out.append(_finding(
            "coarse_chunking", "structural", "medium", "One chunk carries every found field on the page",
            f"All {len(fields)} found fields on page {page_no} name the same node {next(iter(nodes))} while the page has {why}; the chunk hides where each value sits.",
            {**field_anchor(ctx, fields[0]), "field": None, "kind": "page"},
        ))
    return out


def unstable_or_missing_ids(ctx: Context) -> list[dict[str, Any]]:
    """Reads ``field_source_node_id`` on found fields, ``graph_integrity.
    orphans`` and ``node_id_policy`` on the report. A found value with no
    node is medium (its address is lost); orphans counted by the report are
    medium; a report that names no id policy is low — the ids may be
    positional (``el-0``) and change on the next read."""
    out: list[dict[str, Any]] = []
    missing = [f for f in ctx.found if not f.get("field_source_node_id") and not f.get("tree_node_id")]
    if missing:
        names = ", ".join(_words(f.get("name")) for f in missing[:6])
        out.append(_finding("unstable_or_missing_ids", "structural", "medium", "Found value without a node id",
                            f"{len(missing)} found field{'s' if len(missing) != 1 else ''} ({names}) carry no field_source_node_id; the value cannot be addressed on the graph.",
                            field_anchor(ctx, missing[0])))
    gi = ctx.report.get("graph_integrity") if isinstance(ctx.report.get("graph_integrity"), dict) else {}
    orphans = int(gi.get("orphans") or 0)
    if orphans > 0:
        out.append(_finding("unstable_or_missing_ids", "structural", "medium", "Fields without any node on the graph",
                            f"graph_integrity counts {orphans} orphan field{'s' if orphans != 1 else ''}: {gi.get('basis') or 'no basis recorded'}.",
                            root_anchor(ctx)))
    if not ctx.report.get("node_id_policy"):
        out.append(_finding("unstable_or_missing_ids", "structural", "low", "No node id policy recorded",
                            "The report does not say how its node ids are derived, so they may be positional and change on the next read.",
                            root_anchor(ctx)))
    return out


def broken_connectivity(ctx: Context) -> list[dict[str, Any]]:
    """Reads ``tree_node_id`` on found fields when a tree is in hand. A found
    field with ``tree_node_id`` null while a tree exists is medium (the
    import path lost the link); a ``tree_node_id`` the tree does not contain
    is high (the link points at nothing)."""
    if not isinstance(ctx.tree, dict) or not ctx.tree.get("body"):
        return []
    ids = ctx.tree_node_ids()
    out: list[dict[str, Any]] = []
    dangling = [f for f in ctx.found if f.get("tree_node_id") and str(f["tree_node_id"]) not in ids]
    unlinked = [f for f in ctx.found if not f.get("tree_node_id")]
    if dangling:
        names = ", ".join(f"{_words(f.get('name'))} → {f['tree_node_id']}" for f in dangling[:4])
        out.append(_finding("broken_connectivity", "structural", "high", "Field points at a node the tree does not have",
                            f"{len(dangling)} found field{'s' if len(dangling) != 1 else ''} name a tree_node_id absent from the saved tree ({names}).",
                            field_anchor(ctx, dangling[0])))
    if unlinked:
        names = ", ".join(_words(f.get("name")) for f in unlinked[:6])
        out.append(_finding("broken_connectivity", "structural", "medium", "Found value not linked to the saved tree",
                            f"A tree exists but {len(unlinked)} found field{'s' if len(unlinked) != 1 else ''} ({names}) carry tree_node_id null; Review cannot jump to the node.",
                            field_anchor(ctx, unlinked[0])))
    return out


def not_applicable_vs_not_found(ctx: Context) -> list[dict[str, Any]]:
    """Reads ``evidence_state`` and ``reason`` on absent fields against
    ``classification.validation.agrees``. ``not_on_document`` while the
    family disagrees is medium (the field may be not applicable, not
    absent); ``unreadable`` with a reason that says "not found" is medium
    (an unreadable page proves nothing about absence); an absent field with
    no evidence state at all is low (older report; the absence is
    unqualified)."""
    c = ctx.classification
    validation = c.get("validation") if isinstance(c.get("validation"), dict) else {}
    disagrees = validation.get("agrees") is False
    out: list[dict[str, Any]] = []
    absent = [f for f in ctx.fields if f.get("value") is None and f.get("field_type") != "signature"]
    if disagrees:
        confused = [f for f in absent if f.get("evidence_state") == "not_on_document"]
        if confused:
            names = ", ".join(_words(f.get("name")) for f in confused[:6])
            out.append(_finding("not_applicable_vs_not_found", "evidentiary", "medium", "Absent fields may be not applicable, not missing",
                                f"{len(confused)} field{'s' if len(confused) != 1 else ''} ({names}) read not_on_document while the page's family disagrees with the type; they may not belong on this document at all.",
                                field_anchor(ctx, confused[0])))
    unreadable_as_missing = [f for f in absent if f.get("evidence_state") == "unreadable" and "not found" in str(f.get("reason") or "").lower()]
    if unreadable_as_missing:
        names = ", ".join(_words(f.get("name")) for f in unreadable_as_missing[:6])
        out.append(_finding("not_applicable_vs_not_found", "evidentiary", "medium", "Unreadable page counted as a missing value",
                            f"{len(unreadable_as_missing)} field{'s' if len(unreadable_as_missing) != 1 else ''} ({names}) are marked unreadable yet their reason says not found; an unreadable page proves nothing about absence.",
                            field_anchor(ctx, unreadable_as_missing[0])))
    unqualified = [f for f in absent if not f.get("evidence_state")]
    if unqualified and ctx.fields:
        names = ", ".join(_words(f.get("name")) for f in unqualified[:6])
        out.append(_finding("not_applicable_vs_not_found", "evidentiary", "low", "Absence recorded without an evidence state",
                            f"{len(unqualified)} absent field{'s' if len(unqualified) != 1 else ''} ({names}) carry no evidence_state; the report does not say whether the page was readable.",
                            field_anchor(ctx, unqualified[0])))
    return out


def _signature_quality(value: Any) -> tuple[str | None, str | None, Any]:
    """``(quality, basis, present)`` from a dict or a bare quality word."""
    if isinstance(value, dict):
        return (str(value.get("quality")).lower() if value.get("quality") else None, value.get("basis"), value.get("present"))
    if isinstance(value, str) and value.strip():
        return value.strip().lower(), None, None
    return None, None, None


def signature_ambiguity(ctx: Context) -> list[dict[str, Any]]:
    """Reads ``signature_quality`` on the signature field (or
    ``quality_report.signature``). ``questionable`` / ``faint`` /
    ``incomplete`` / ``stamped`` is medium — the heuristic cannot confirm a
    hand-signed mark; ``missing`` where the type carries a signature field is
    low. A person's accept on the field lowers the ambiguity to low but does
    not erase it (the plan: Red-Hat is not a labelling shortcut)."""
    sig_field = next((f for f in ctx.fields if f.get("field_type") == "signature" or str(f.get("name") or "").endswith("signature")), None)
    source: Any = sig_field.get("signature_quality") if sig_field else None
    if source is None:
        qr = ctx.report.get("quality_report") if isinstance(ctx.report.get("quality_report"), dict) else {}
        source = qr.get("signature")
    quality, basis, present = _signature_quality(source)
    if quality is None:
        return []
    anchor = field_anchor(ctx, sig_field) if sig_field else root_anchor(ctx)
    accepted = bool(sig_field and sig_field.get("field_state") == "accepted")
    if quality in AMBIGUOUS_SIGNATURE:
        sev = "low" if accepted else "medium"
        tail = f" — {basis}" if basis else ""
        return [_finding("signature_ambiguity", "evidentiary", sev, f"Signature is {quality}",
                         f"The signature reads {quality}{tail}" + ("; a reviewer accepted it, the mark itself is still unconfirmed." if accepted else "."), anchor)]
    if quality == "missing" or present is False:
        if sig_field is None:
            return []
        return [_finding("signature_ambiguity", "evidentiary", "low", "Signature expected but not found",
                         f"The {_words(ctx.classification.get('document_type'))} schema carries a signature field and none was found" + (f" — {basis}" if basis else "") + ".", anchor)]
    return []


def low_quality_evidence(ctx: Context) -> list[dict[str, Any]]:
    """Reads ``pages[].quality_score``, ``pages[].flags`` and
    ``pages[].ocr_confidence``. A page under ``PAGE_QUALITY_LOW``, flagged
    low_res / blurry / low_contrast / no_text, or OCR-read under ``OCR_LOW``
    is medium when a found field sits on it (the values rest on weak
    evidence) and low otherwise."""
    found_pages: dict[int, int] = {}
    for f in ctx.found:
        a = field_anchor(ctx, f)
        if a.get("page") is not None:
            found_pages[int(a["page"])] = found_pages.get(int(a["page"]), 0) + 1
    out: list[dict[str, Any]] = []
    for p in ctx.pages:
        no = p.get("page")
        if not isinstance(no, int):
            continue
        q = _num(p.get("quality_score"))
        ocr = _num(p.get("ocr_confidence"))
        flags = sorted(LOW_QUALITY_FLAGS & {str(fl) for fl in (p.get("flags") or [])})
        reasons: list[str] = []
        if q is not None and q < PAGE_QUALITY_LOW:
            reasons.append(f"quality {q:.2f}")
        if flags:
            reasons.append(", ".join(fl.replace("_", " ") for fl in flags))
        if ocr is not None and ocr < OCR_LOW and found_pages.get(no):
            reasons.append(f"OCR confidence {ocr:.2f}")
        if not reasons:
            continue
        n = found_pages.get(no, 0)
        sev = "medium" if n else "low"
        tail = f"; {n} found field{'s' if n != 1 else ''} rest on it" if n else "; no found field rests on it"
        out.append(_finding("low_quality_evidence", "evidentiary", sev, f"Page {no} is low-quality evidence",
                            f"Page {no}: {'; '.join(reasons)}{tail}.", page_anchor(ctx, no)))
    return out


def cross_document_conflict(ctx: Context) -> list[dict[str, Any]]:
    """Reads ``conflicts[]`` (``field_extractor.cross_document_conflicts``).
    Every entry is high, anchored to the conflicting field; an open dispute
    or a correction on that field is named in the rationale."""
    out: list[dict[str, Any]] = []
    fields_by_name = {str(f.get("name")): f for f in ctx.fields}
    for c in ctx.report.get("conflicts") or []:
        if not isinstance(c, dict):
            continue
        name = str(c.get("field") or "")
        f = fields_by_name.get(name)
        values = c.get("values") if isinstance(c.get("values"), list) else []
        shown = ", ".join(repr(v.get("value")) for v in values[:3] if isinstance(v, dict))
        extras: list[str] = []
        if any(str(d.get("field_name")) == name and str(d.get("status") or "open") == "open" for d in ctx.disputes):
            extras.append("a dispute is open on it")
        if any(str(k.get("field_name")) == name for k in ctx.corrections):
            extras.append("it was corrected")
        rationale = (c.get("summary") or f"{_words(name)} differs across {len(values) or 'two'} documents" + (f" ({shown})" if shown else "")) + ("; " + "; ".join(extras) if extras else "") + "."
        out.append(_finding("cross_document_conflict", "evidentiary", "high", f"{_words(name).capitalize()} conflicts with another document", rationale,
                            field_anchor(ctx, f) if f else root_anchor(ctx, field=name or None)))
    return out


def export_overclaiming(ctx: Context) -> list[dict[str, Any]]:
    """Reads ``export_state.trust_state`` and ``export_state.title`` /
    ``language`` (the dossier's ``build_verification_state`` output). Any
    certificate language while the trust state is not ``verified`` is high,
    class export."""
    es = ctx.export_state if isinstance(ctx.export_state, dict) else None
    if not es:
        return []
    trust = str(es.get("trust_state") or "").lower()
    if trust == "verified":
        return []
    text = " ".join(str(es.get(k) or "") for k in ("title", "subtitle", "language", "status_band"))
    hit = CERTIFICATE_WORDS.search(text)
    if not hit:
        return []
    return [_finding("export_overclaiming", "export", "high", "Export uses certificate language before verification",
                     f"The export reads “{hit.group(0)}” while the trust state is {trust or 'not recorded'}; only a verified state may carry that word.",
                     root_anchor(ctx))]


def confidence_flattening(ctx: Context) -> list[dict[str, Any]]:
    """Reads ``extraction_confidence`` / ``verification_confidence`` /
    ``provenance_confidence`` and ``confidence_basis`` on found fields. A
    field whose three confidences are one number with a basis that says
    "default" is medium (the layers were not measured apart); at least
    ``FLAT_MIN_FOUND`` found fields of which ``FLAT_SHARE`` share one
    identical extraction confidence is low (the number does not distinguish
    the fields)."""
    out: list[dict[str, Any]] = []
    flat = []
    for f in ctx.found:
        e, v, p = _num(f.get("extraction_confidence")), _num(f.get("verification_confidence")), _num(f.get("provenance_confidence"))
        if e is None or v is None or p is None:
            continue
        if e == v == p and "default" in str(f.get("confidence_basis") or "").lower():
            flat.append(f)
    if flat:
        names = ", ".join(_words(f.get("name")) for f in flat[:6])
        out.append(_finding("confidence_flattening", "evidentiary", "medium", "Extraction, verification and provenance confidence are one default number",
                            f"{len(flat)} field{'s' if len(flat) != 1 else ''} ({names}) carry the same value on all three confidences with a default basis; the layers were not measured apart.",
                            field_anchor(ctx, flat[0])))
    found = [f for f in ctx.found if _num(f.get("extraction_confidence")) is not None]
    if len(found) >= FLAT_MIN_FOUND:
        counts: dict[float, list[dict[str, Any]]] = {}
        for f in found:
            counts.setdefault(round(float(f["extraction_confidence"]), 4), []).append(f)
        value, group = max(counts.items(), key=lambda kv: len(kv[1]))
        if len(group) / len(found) >= FLAT_SHARE:
            out.append(_finding("confidence_flattening", "evidentiary", "low", "One confidence value for most found fields",
                                f"{len(group)} of {len(found)} found fields carry extraction confidence {value:.2f}; the number does not distinguish them ({group[0].get('confidence_basis') or 'no basis'}).",
                                field_anchor(ctx, group[0])))
    return out


def fallback_rendering(ctx: Context) -> list[dict[str, Any]]:
    """Reads ``export_state.renderer`` / ``export_state.fallback`` (the
    export's own record of which engine produced the file). A text or
    fallback renderer is high, class export: the file hides the run state
    behind a page that says an engine is missing."""
    es = ctx.export_state if isinstance(ctx.export_state, dict) else None
    if not es:
        return []
    renderer = str(es.get("renderer") or es.get("engine") or "").lower()
    if renderer in FALLBACK_RENDERERS or bool(es.get("fallback")):
        return [_finding("fallback_rendering", "export", "high", "Export was produced by a fallback renderer",
                         f"The export records renderer {renderer or 'fallback'}; a text fallback ships a page about a missing engine instead of the run state.",
                         root_anchor(ctx))]
    return []


RULES: tuple[Callable[[Context], list[dict[str, Any]]], ...] = (
    wrong_document_family,
    coarse_chunking,
    unstable_or_missing_ids,
    broken_connectivity,
    not_applicable_vs_not_found,
    signature_ambiguity,
    low_quality_evidence,
    cross_document_conflict,
    export_overclaiming,
    confidence_flattening,
    fallback_rendering,
)


# --------------------------------------------------------------------------
# Model-assisted unsupported-claim check (grounded, optional)
# --------------------------------------------------------------------------


def llm_enabled() -> bool:
    return os.environ.get("PARSURE_REDHAT_LLM", "").strip().lower() not in ("0", "false", "no", "off")


def _governor():
    try:
        from .. import cost_governance as cg  # type: ignore
    except ImportError:
        import cost_governance as cg  # type: ignore
    return cg


def backend_available() -> tuple[bool, str]:
    """``(ok, description)`` — whether the REDHAT policy resolves to a model."""
    try:
        cg = _governor()
        policy = cg.CostGovernor().policy_for(cg.TaskType.REDHAT)
        model = policy.litellm_model or policy.model_id
        if not model:
            return False, "REDHAT policy names no model"
        return True, f"{cg.llm_backend() or 'cloud'}: {model}"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def default_completion(prompt: str, *, project_id: str | None = None) -> str:
    """One call through ``cost_governance`` with the ``REDHAT`` policy — the
    same executor ``llm_extraction`` and the draft audit use. Raises
    ``llm_extraction.LLMUnavailable`` on an executor error string."""
    cg = _governor()
    gov = cg.CostGovernor()
    policy = gov.policy_for(cg.TaskType.REDHAT)
    model = policy.litellm_model or policy.model_id
    executor = gov.executor or gov._default_executor  # noqa: SLF001
    started = time.monotonic()
    text, in_tok, out_tok = executor(model, [{"role": "user", "content": prompt}], policy.max_output_tokens, policy.caching)
    log.info("redhat_graph: model=%s in=%s out=%s elapsed=%.1fs", model, in_tok, out_tok, time.monotonic() - started)
    if not isinstance(text, str) or text.startswith("ERROR:"):
        raise lx.LLMUnavailable(f"{model}: {str(text)[7:].strip()[:200] or 'empty answer'}")
    if project_id:
        try:
            gov.record_usage(project_id, input_tokens=int(in_tok or 0), output_tokens=int(out_tok or 0),
                             model_id=policy.model_id, task_type=cg.TaskType.REDHAT, meta={"pipeline": "parsure_redhat_graph"})
        except Exception:  # noqa: BLE001
            pass
    return text


def build_prompt(found: list[tuple[str, Any, int]], pages: list[tuple[int, str]]) -> str:
    """The critique question: which extracted values does the quoted page
    text NOT support. The answer must quote the document verbatim — a
    finding the model cannot quote is dropped."""
    lines = [
        "You are an adversarial reviewer of data extracted from a document. Below are the extracted fields",
        "(name, value, page) and the text of the pages they came from.",
        "Name every field whose value is NOT supported by the page text: the value is absent, contradicts the text,",
        "or belongs to a different field. Ignore formatting differences (1,486.00 vs 1486; 03/01/2025 vs March 1, 2025).",
        'Answer with strict JSON only: {"unsupported": [{"field": "<name>", "quote": "<an exact, verbatim line copied from the page text that shows what the document actually says>", "why": "<one short sentence>"}]}',
        'If every value is supported answer {"unsupported": []}. Never invent a quote.',
        "",
        "FIELDS:",
    ]
    for name, value, page in found:
        lines.append(f"- {name} = {json.dumps(value, ensure_ascii=False, default=str)} (page {page})")
    for page_no, text in pages:
        lines += ["", f"=== PAGE {page_no} ===", text]
    return "\n".join(lines)


def unsupported_claim_check(
    ctx: Context,
    *,
    completion: Callable[[str], str] | None = None,
    llm: bool | None = None,
    timeout_s: float = LLM_TIMEOUT_S,
    notes: list[str],
) -> list[dict[str, Any]]:
    """Ask the model, keep only what the page text confirms. ``completion``
    is injectable (tests); ``llm=False`` skips; every failure is a note."""
    on = llm_enabled() if llm is None else bool(llm)
    if not on:
        notes.append("unsupported-claim check skipped: PARSURE_REDHAT_LLM=0")
        return []
    found = [f for f in ctx.found if f.get("field_type") != "signature"]
    if not found:
        notes.append("unsupported-claim check skipped: no found field to check")
        return []
    texts = ctx.report.get("_page_texts")
    if not isinstance(texts, list) or not any(str(t or "").strip() for t in texts):
        notes.append("unsupported-claim check skipped: no page text on the report")
        return []
    if completion is None:
        ok, desc = backend_available()
        if not ok:
            notes.append(f"unsupported-claim check skipped: no model backend ({desc})")
            return []
        project_id = ctx.report.get("project_id")
        call: Callable[[str], str] = lambda p: default_completion(p, project_id=project_id)  # noqa: E731
        model_desc = desc
    else:
        call = completion
        model_desc = "injected completion"
    items: list[tuple[str, Any, int]] = []
    pages_needed: set[int] = set()
    for f in found:
        page = field_anchor(ctx, f).get("page") or 1
        items.append((str(f.get("name")), f.get("value"), int(page)))
        pages_needed.add(int(page))
    budget = LLM_TOTAL_CHARS
    pages: list[tuple[int, str]] = []
    for no in sorted(pages_needed):
        if not (0 < no <= len(texts)) or budget <= 0:
            continue
        text = str(texts[no - 1] or "")[: min(LLM_PAGE_CHARS, budget)]
        budget -= len(text)
        pages.append((no, text))
    prompt = build_prompt(items, pages)
    try:
        answer = lx._call_with_timeout(lambda: call(prompt), timeout_s)  # noqa: SLF001
    except lx.LLMUnavailable as exc:
        notes.append(f"unsupported-claim check skipped: model unavailable ({exc})")
        return []
    except Exception as exc:  # noqa: BLE001
        notes.append(f"unsupported-claim check skipped: {type(exc).__name__}: {exc}")
        return []
    parsed = lx.parse_json_answer(answer)
    candidates = parsed.get("unsupported") if isinstance(parsed, dict) else None
    if not isinstance(candidates, list):
        notes.append(f"unsupported-claim check: model answer was not the expected JSON ({model_desc})")
        return []
    by_name = {str(f.get("name")): f for f in found}
    out: list[dict[str, Any]] = []
    dropped = 0
    for cand in candidates:
        if not isinstance(cand, dict):
            dropped += 1
            continue
        name = str(cand.get("field") or "")
        quote = cand.get("quote")
        f = by_name.get(name)
        if f is None or not isinstance(quote, str) or not quote.strip():
            dropped += 1
            continue
        page = int(field_anchor(ctx, f).get("page") or 1)
        hit_page = None
        order = [page] + [n for n in range(1, len(texts) + 1) if n != page]
        for no in order:
            if 0 < no <= len(texts) and lx.find_verbatim(str(texts[no - 1] or ""), quote):
                hit_page = no
                break
        if hit_page is None:
            dropped += 1
            continue
        why = str(cand.get("why") or "").strip().rstrip(".")
        out.append(_finding(
            "unsupported_claim", "evidentiary", "medium", f"{_words(name).capitalize()} is not supported by the quoted source",
            f"The page reads “{quote.strip()[:160]}”" + (f" — {why}" if why else "") + f"; the extracted value is {json.dumps(f.get('value'), ensure_ascii=False, default=str)}.",
            {**field_anchor(ctx, f), "page": hit_page}, model=model_desc, quote=quote.strip(),
        ))
    notes.append(f"unsupported-claim check ran ({model_desc}): {len(out)} grounded finding{'s' if len(out) != 1 else ''}, {dropped} dropped without a verbatim quote")
    return out


# --------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------


_SEV_ORDER = {s: i for i, s in enumerate(SEVERITIES)}
_RULE_ORDER = {r.__name__: i for i, r in enumerate(RULES)}
_RULE_ORDER["unsupported_claim"] = len(RULES)


def _number(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings.sort(key=lambda f: (_SEV_ORDER.get(f.get("severity"), 9), _RULE_ORDER.get(f.get("rule"), 99)))
    per_class: dict[str, int] = {}
    for f in findings:
        cls = f.get("class") or "structural"
        per_class[cls] = per_class.get(cls, 0) + 1
        f["id"] = f"rh-{cls}-{per_class[cls]}"
    return findings


def _counts(findings: list[dict[str, Any]]) -> tuple[dict[str, int], dict[str, int]]:
    counts = {s: sum(1 for f in findings if f.get("severity") == s) for s in SEVERITIES}
    classes = {c: sum(1 for f in findings if f.get("class") == c) for c in CLASSES}
    return counts, classes


def critique_report(
    report: dict[str, Any],
    *,
    tree: dict[str, Any] | None = None,
    corrections: list[dict[str, Any]] | None = None,
    disputes: list[dict[str, Any]] | None = None,
    events: list[dict[str, Any]] | None = None,
    export_state: dict[str, Any] | None = None,
    completion: Callable[[str], str] | None = None,
    llm: bool | None = None,
    timeout_s: float = LLM_TIMEOUT_S,
) -> dict[str, Any]:
    """Run every rule, then the grounded model check, over one report.

    Returns the ``redhat`` block ``{"policy", "ran_at", "findings", "counts",
    "classes", "notes", "rules"}`` without writing it into the report —
    ``attach_findings`` does that. ``completion`` / ``llm`` / ``timeout_s``
    control the model check (tests inject a completion; ``llm=False`` skips
    it). Never raises: a rule that fails is a note and the other rules still
    run.
    """
    notes: list[str] = []
    findings: list[dict[str, Any]] = []
    ctx = Context(
        report=report if isinstance(report, dict) else {},
        tree=tree if isinstance(tree, dict) else None,
        corrections=[c for c in (corrections or []) if isinstance(c, dict)],
        disputes=[d for d in (disputes or []) if isinstance(d, dict)],
        events=[e for e in (events or []) if isinstance(e, dict)],
        export_state=export_state if isinstance(export_state, dict) else None,
    )
    for rule in RULES:
        try:
            findings.extend(rule(ctx))
        except Exception as exc:  # noqa: BLE001 — one rule must not silence the others
            log.exception("redhat_graph: rule %s failed", rule.__name__)
            notes.append(f"{rule.__name__} did not run: {type(exc).__name__}: {exc}")
    if ctx.export_state is None:
        notes.append("export checks (export_overclaiming, fallback_rendering) had no export state to read; they run at export time")
    if ctx.tree is None:
        notes.append("broken_connectivity: no tree in hand (Sources-pane upload or tree not passed); not run")
    try:
        findings.extend(unsupported_claim_check(ctx, completion=completion, llm=llm, timeout_s=timeout_s, notes=notes))
    except Exception as exc:  # noqa: BLE001
        log.exception("redhat_graph: unsupported_claim_check failed")
        notes.append(f"unsupported-claim check did not run: {type(exc).__name__}: {exc}")
    _number(findings)
    counts, classes = _counts(findings)
    return {"policy": POLICY, "ran_at": _now(), "findings": findings, "counts": counts, "classes": classes, "notes": notes,
            "rules": [r.__name__ for r in RULES] + ["unsupported_claim"]}


def attach_findings(report: dict[str, Any], findings: list[dict[str, Any]] | dict[str, Any], *, notes: list[str] | None = None) -> dict[str, Any]:
    """Write the block into ``report["redhat"]`` and prepend the high
    findings to ``review_summary.reasons`` (as ``Red-Hat: <title>``), so the
    card, the queue and the record read them. ``findings`` may be the list
    or the dict ``critique_report`` returned."""
    ran_at = None
    if isinstance(findings, dict):
        notes = list(findings.get("notes") or []) if notes is None else notes
        ran_at = findings.get("ran_at")
        findings = list(findings.get("findings") or [])
    items = [f for f in (findings or []) if isinstance(f, dict)]
    for f in items:
        f.setdefault("policy", POLICY)
    if any("id" not in f for f in items):
        _number(items)
    counts, classes = _counts(items)
    block = {"policy": POLICY, "ran_at": ran_at or _now(), "findings": items, "counts": counts, "classes": classes, "notes": list(notes or [])}
    report["redhat"] = block
    summary = report.get("review_summary")
    if not isinstance(summary, dict):
        summary = {}
        report["review_summary"] = summary
    kept = [r for r in (summary.get("reasons") or []) if not (isinstance(r, dict) and str(r.get("reason") or "").startswith("Red-Hat: "))]
    highs: dict[str, int] = {}
    for f in items:
        if f.get("severity") == "high":
            key = f"Red-Hat: {f.get('title')}"
            highs[key] = highs.get(key, 0) + 1
    summary["reasons"] = [{"reason": k, "count": n} for k, n in highs.items()] + kept
    return block


def findings_view(report: dict[str, Any] | None) -> dict[str, Any]:
    """What a surface needs: ``{"ran", "reason", "count", "high", "medium",
    "low", "classes", "findings": [{severity, title, class, rule, anchor,
    anchor_label, rationale}], "notes"}``. ``ran`` is false with the reason
    when the report carries no ``redhat`` block."""
    block = (report or {}).get("redhat") if isinstance(report, dict) else None
    if not isinstance(block, dict):
        return {"ran": False, "reason": "no critique is recorded for this report", "count": 0, "high": 0, "medium": 0, "low": 0,
                "classes": {c: 0 for c in CLASSES}, "findings": [], "notes": []}
    items = [f for f in (block.get("findings") or []) if isinstance(f, dict)]
    counts = block.get("counts") if isinstance(block.get("counts"), dict) else _counts(items)[0]
    classes = block.get("classes") if isinstance(block.get("classes"), dict) else _counts(items)[1]
    return {
        "ran": True,
        "reason": "",
        "policy": block.get("policy") or POLICY,
        "ran_at": block.get("ran_at"),
        "count": len(items),
        "high": int(counts.get("high") or 0),
        "medium": int(counts.get("medium") or 0),
        "low": int(counts.get("low") or 0),
        "classes": {c: int(classes.get(c) or 0) for c in CLASSES},
        "findings": [
            {
                "id": f.get("id"),
                "severity": f.get("severity"),
                "title": f.get("title"),
                "class": f.get("class"),
                "rule": f.get("rule"),
                "anchor": f.get("anchor") if isinstance(f.get("anchor"), dict) else {},
                "anchor_label": anchor_label(f.get("anchor")),
                "rationale": f.get("rationale"),
            }
            for f in items
        ],
        "notes": list(block.get("notes") or []),
    }
