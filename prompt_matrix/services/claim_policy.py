"""Claim verdicts — policy ``claim-v1`` (customer policy of 2026-09-27).

Every claim-eligible paragraph gets one block at ``node.meta.provenance.claim``:

    {"policy": "claim-v1",
     "verdict": "VERIFIED" | "UNSUPPORTED" | "CONTRADICTED" | "INSUFFICIENT_EVIDENCE",
     "reason": "<one sentence>",
     "quote": "<verbatim source text>" | null,
     "quote_verbatim": bool,
     "source_id": str, "source_name": str,
     "page": int | null,                       # never a default
     "checks": {"kind": "fact" | "meta" | "enumeration",
                "entailment": yes|partial|no|contradicts|unverified|null,
                "numeric": {...},              # services/numeric_recompute
                "wording": {"flags": [...], "unsupported_terms": [...]},
                "source_quality": {"status": ok|low|missing, "basis": str},
                "sub_claims": [{"text", "status", "detail", "evidence"}]},  # enumeration only
     "flags": [...]}

The verdict is derived by rules applied in this order, stopping at the first that
decides (``derive_claim``):

0. a statement about the draft or the source itself
   ("The source does not provide…")                → UNSUPPORTED, kind ``meta``
1. no citation or anchor (and not an enumeration)  → UNSUPPORTED
2. cited source not among the supplied ones        → INSUFFICIENT_EVIDENCE (missing);
   no sources supplied at all: the entailment
   label still speaks (contradicts → CONTRADICTED,
   no / partial → UNSUPPORTED) but nothing can be
   VERIFIED — ``yes`` is INSUFFICIENT_EVIDENCE
3. cited text not verbatim in that source          → UNSUPPORTED
4. source quality insufficient (parse confidence
   under 0.5, under 200 characters of text, or
   dropped / truncated before the cited region)    → INSUFFICIENT_EVIDENCE
5. an enumeration ("policy number …, premium $…,
   limit $…") is assessed per sub-claim against the
   whole cited source: VERIFIED only when every
   sub-claim's value is verbatim under its label;
   a same-label conflict → CONTRADICTED; else
   UNSUPPORTED naming the failing sub-claims
6. no entailment answer (never ran, or failed)     → INSUFFICIENT_EVIDENCE
7. entailment ``contradicts`` (verbatim-backed, see
   ``entailment.enforce_contradiction_evidence``),
   or an arithmetic / same-label figure conflict   → CONTRADICTED
8. a figure the claim states is in neither the
   evidence window nor the cited source's text     → UNSUPPORTED (not found)
9. entailment ``yes`` and numeric ok / n.a.        → VERIFIED
   (numeric ``insufficient``                       → INSUFFICIENT_EVIDENCE)
10. entailment ``partial``                         → UNSUPPORTED (a qualifier is missing)
11. entailment ``no``                              → UNSUPPORTED

Nothing here reads a model's confidence: the entailment record carries a label
and a sentence, the numeric check is arithmetic, the verbatim test is a string
search. A claim is VERIFIED only with a quote that is verbatim in a supplied
source — rule 3 sits above every rule that can verify, so no path reaches
VERIFIED without one; an enumeration's quote is the source sentence carrying its
first sub-claim, and each sub-claim carries its own.

Form fields (2026-09-28). A citation row that names a Parsure field
(``grounding_source: "parsure_field"``, written by ``routers/draft.
attach_citations_to_tree`` for the field sentences ``services/source_carry``
appends to a form-like source) is tested on the field's verbatim page quote
(``field_quote``), not on the "Label: value" sentence, which is not itself a line
of the page. Such a unit carries ``grounding_source``, ``field`` and ``element_id``,
its ``quote`` is the field's page quote, and ``checks.source_quality.basis`` says
"from the field report". The rules above are unchanged: the quote still has to be
verbatim in the supplied source text.

High-risk wording (``HIGH_RISK_TERMS``) found in the claim whose stem is absent
from the verbatim evidence is flagged ``high_risk_wording:<term>``. A flag does
not change the verdict; the gate (``services/audit_summary``) routes a document
with any flagged claim to review.

Calibrated on the live stack 2026-09-27 (one-page auto declarations, Llama 3.3
70B drafting): a faithful, fully cited draft first read ``verified 0,
contradicted 3`` because (a) ``contradicts`` fired on facts merely absent from a
one-sentence window, (b) a listing of three unrelated amounts was summed against
a "total premium" in another sentence, (c) "collision deductible $500" and
"comprehensive deductible $250" were read as one inconsistent "deductible", and
(d) an eight-fact list paragraph had its figures searched in a three-sentence
window. Rules 0, 5, 7 and 8, the qualified labels and the explicit-relation sum
rule in ``numeric_recompute`` are the answers.
"""

from __future__ import annotations

import re
from typing import Any

try:
    from ..models.jdf import _MIN_CLAIM_TOKENS, _tokenize
    from .numeric_recompute import (
        Figure,
        extract_figures,
        format_decimal,
        labelled,
        recompute,
    )
except ImportError:
    from models.jdf import _MIN_CLAIM_TOKENS, _tokenize
    from services.numeric_recompute import (
        Figure,
        extract_figures,
        format_decimal,
        labelled,
        recompute,
    )

CLAIM_POLICY_ID = "claim-v1"

VERIFIED = "VERIFIED"
UNSUPPORTED = "UNSUPPORTED"
CONTRADICTED = "CONTRADICTED"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
VERDICTS = (VERIFIED, UNSUPPORTED, CONTRADICTED, INSUFFICIENT_EVIDENCE)

#: Below this parse confidence a source's text is not evidence (rule 4).
MIN_SOURCE_CONFIDENCE = 0.5
#: Below this many characters a source is a stub, not a document (rule 4).
MIN_SOURCE_CHARS = 200
#: A paragraph under the general claim floor (``models.jdf._MIN_CLAIM_TOKENS``) is
#: still a claim at this many content tokens when it carries a figure, a date, an
#: exclusion word or a high-risk term: "Flood is excluded." must be assessed.
MIN_SHORT_CLAIM_TOKENS = 2
#: A paragraph with at least this many value-bearing segments — and no stated
#: calculation — is an enumeration and is assessed per sub-claim (rule 5). Two,
#: not three: "The named insured is John Q. Sample and the policy period is
#: 01/15/2025 to 01/15/2026" is two facts cited to two one-line windows, and a
#: per-window entailment answers ``no`` to each because each window lacks the
#: other fact (live run 2026-09-27). A sentence that states arithmetic
#: ("rose from $1,000 to $1,200, an increase of 20%") is one claim and is never
#: split — its figures are recomputed, not looked up one by one.
MIN_ENUMERATION_ITEMS = 2

#: term as written in a claim → regex for its stem in the quote. The stem is what
#: must occur in the verbatim quote for the term to pass unflagged.
HIGH_RISK_TERMS: dict[str, str] = {
    "guaranteed": r"guarantee",
    "guarantee": r"guarantee",
    "guarantees": r"guarantee",
    "covered": r"cover",
    "coverage applies": r"coverage applies|covers|covered",
    "compliant": r"complian",
    "compliance": r"complian",
    "approved": r"approv",
    "entitled": r"entitle",
    "liable": r"liab",
    "is liable": r"liab",
    "obligated": r"obligat",
    "must pay": r"must pay|shall pay|pays|paid|payable",
    "warranted": r"warrant",
    "certified": r"certif",
    "in full": r"in full",
    "all": r"\ball\b",
    "always": r"always",
    "never": r"never",
    "violates": r"violat",
    "violated": r"violat",
    "breaches": r"breach",
    "breached": r"breach",
}
_HIGH_RISK_RE = re.compile(
    r"\b(" + "|".join(sorted((re.escape(t) for t in HIGH_RISK_TERMS), key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)
_EXCLUSION_RE = re.compile(r"\b(?:exclud(?:e|ed|es|ing)|exclusions?|except|unless)\b", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")

#: Statements about the draft or the source, not about the document's subject.
_META_RE = re.compile(
    r"^\s*(?:"
    r"the (?:information|details?|figures?|facts?|summary|data) (?:provided|above|below|here|presented|listed|stated)"
    r"|(?:this|the) (?:summary|memo|document|response|answer|draft|note) (?:is|was|does|has|reflects|covers|summari[sz]es|provides)"
    r"|the (?:provided |supplied |attached |uploaded |cited |above )?(?:source|document|declarations? page|material|text|file|page)s?"
    r"(?: (?:material|document|text|page|file)s?)? "
    # Negatives, evaluatives and "provides the following" only: "the source
    # states the deductible is $500" attributes a document fact and is a claim.
    r"(?:does not|do not|did not|doesn't|don't|is|are|was|were|lacks?|omits?|"
    r"(?:provides?|lists?|gives?|offers?|contains?|includes?) the following)"
    r"|the (?:information|details?|data|facts?|figures?|values?) (?:extracted|summari[sz]ed|reported|presented|listed|quoted|drawn|taken|shown)"
    r"|(?:the |our |my )?confidence (?:in|of|level|for)"
    r"|(?:key |the |all |these )?(?:policy |claim )?(?:details?|facts?|figures?|information|values?) (?:is|are) "
    r"(?:supported|confirmed|corroborated|backed|drawn|taken|derived|quoted|sourced) (?:by|from) the (?:source|document|declarations)"
    r"|the extracted (?:information|data|values?|figures?|facts?)"
    r"|(?:the )?sentences? .{0,40}(?:are|is) (?:repetitive|redundant|duplicated|boilerplate)"
    r"|(?:this|the) (?:extraction|assessment|review|analysis|verification) (?:is|was|has|shows?)"
    r"|based on the (?:source|document|information|provided)"
    r"|no (?:further |additional |other )?(?:information|details?) (?:is|was|are|were) (?:provided|available|given|found)"
    r"|(?:there is|there are|there was) no (?:information|mention|detail)"
    r"|all (?:information|figures|details) (?:is|are) (?:taken|drawn|quoted) (?:directly )?from"
    r")",
    re.IGNORECASE | re.MULTILINE,
)

#: Words that end a label qualifier when reading back from the head noun.
_QUALIFIER_STOP = {
    "the", "a", "an", "of", "is", "are", "was", "were", "for", "with", "per", "and", "or",
    "at", "to", "in", "on", "by", "its", "their", "this", "that", "has", "have", "be",
    "as", "from", "under", "which", "whose", "each", "any", "no", "not", "our", "your",
}
_ID_TOKEN_RE = re.compile(r"\b(?=[A-Za-z0-9-]*\d)[A-Z0-9][A-Za-z0-9-]{5,}\b")
#: A capitalised multi-word name; a middle initial may stand without its period
#: ("John Q Sample" is how the draft wrote "John Q. Sample", live run 2026-09-27).
_NAME_RE = re.compile(r"\b(?:[A-Z][A-Za-z.'-]+(?:\s+(?:[A-Z][A-Za-z.'-]+|[A-Z]\.?(?=\s+[A-Z])))+)")
#: The short label line the compiled draft puts before a paragraph's sentence
#: ("policy snapshot\nThe named insured is…"): no figure, no colon, few words.
_LABEL_LINE_RE = re.compile(r"^\s*[a-z][a-z /&-]{1,40}\n")
#: Label lines under which the draft writes about itself or the source, not about
#: the document's subject (the compile prompt's own section names).
_META_LABEL_LINES = frozenset(
    {"confidence", "missing items", "limitations", "caveats", "notes", "disclaimer", "method", "methodology"}
)
_LABEL_VALUE_RE = re.compile(
    r"^\s*(?P<label>[A-Za-z][A-Za-z /()'-]{1,40}?)\s*(?::|\bis\b|\bwas\b|\bof\b|\bare\b|\bwere\b)\s*(?P<value>.+?)\s*$"
)


_INITIAL_PERIOD_RE = re.compile(r"\b([A-Z])\.(?=\s|$)")


def collapse_whitespace(text: str) -> str:
    return _WS_RE.sub(" ", str(text or "")).strip()


def verbatim_form(text: str) -> str:
    """The form two texts are compared in for "verbatim": whitespace collapsed,
    and the period after a single-letter initial dropped ("John Q. Sample" ==
    "John Q Sample"). The compile's numbered sentence map loses that period when
    it splits sentences, so the model quotes the name without it while the PDF
    text carries it (live run 2026-09-27, the one fact a faithful draft failed
    on). Orthography of an initial is not a different fact; nothing else is
    normalised — figures, casing and words must match."""
    return _INITIAL_PERIOD_RE.sub(r"\1", collapse_whitespace(text))


def is_meta_statement(content: str) -> bool:
    """A paragraph about the draft or the source ("The source does not provide…").

    Matched at the start of any line, not only the first: the compiled draft's
    paragraphs open with a short label line ("missing items", "confidence") and
    the statement follows it (live run 2026-09-27, where both meta paragraphs
    read as facts because the pattern was anchored to the first character). A
    paragraph under one of ``_META_LABEL_LINES`` is meta by construction.
    """
    text = str(content or "")
    label = _LABEL_LINE_RE.match(text)
    if label and label.group(0).strip().lower() in _META_LABEL_LINES:
        return True
    return bool(_META_RE.search(text))


def is_claim_eligible(content: str) -> bool:
    """Whether a paragraph is a claim the policy must assess.

    The general floor is ``_MIN_CLAIM_TOKENS`` content tokens (shared with the
    lexical matcher). A shorter paragraph is still a claim at
    ``MIN_SHORT_CLAIM_TOKENS`` when it carries a figure, a date, an exclusion word
    or a high-risk term — "Flood is excluded." is three words and a coverage
    statement the customer's policy requires assessed. Titles and stubs with
    neither stay out.
    """
    text = str(content or "").strip()
    if not text:
        return False
    tokens = _tokenize(text)
    if len(tokens) >= _MIN_CLAIM_TOKENS:
        return True
    if len(tokens) < MIN_SHORT_CLAIM_TOKENS:
        return False
    return bool(extract_figures(text)) or bool(_EXCLUSION_RE.search(text)) or bool(
        _HIGH_RISK_RE.search(text)
    )


def page_of(row: dict[str, Any]) -> int | None:
    """The page the provenance row names, as an int — or ``None``. Never a default.

    The matcher writes ``page_number`` as a string ("" when the source had no page
    layout); a citation writes ``page`` from the sentence map (``None`` when the
    source did not carry pages). Neither is ever turned into 1.
    """
    for key in ("page", "page_number"):
        value = row.get(key)
        if value in (None, ""):
            continue
        try:
            page = int(str(value).strip())
        except (TypeError, ValueError):
            continue
        return page if page > 0 else None
    return None


def find_source(row: dict[str, Any], sources: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """The supplied source a provenance row cites — by id, else by file name."""
    if not sources:
        return None
    source_id = str(row.get("source_id") or "").strip()
    if source_id:
        for source in sources:
            if str(source.get("id") or source.get("source_id") or "") == source_id:
                return source
    name = str(row.get("source_name") or "").strip()
    if name:
        for source in sources:
            if str(source.get("filename") or source.get("name") or "") == name:
                return source
    return None


def quote_is_verbatim(quote: str, source_text: str) -> bool:
    """Case-sensitive substring test in ``verbatim_form`` — the only verbatim test."""
    needle = verbatim_form(quote)
    if not needle:
        return False
    return needle in verbatim_form(source_text)


def _quote_offset(quote: str, source_text: str) -> int:
    return verbatim_form(source_text).find(verbatim_form(quote))


def _carry_entry(source: dict[str, Any], carry_plan: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(carry_plan, dict):
        return None
    source_id = str(source.get("id") or source.get("source_id") or "")
    for entry in carry_plan.get("sources") or []:
        if isinstance(entry, dict) and str(entry.get("source_id") or "") == source_id:
            return entry
    return None


def source_quality(
    source: dict[str, Any],
    *,
    quote: str,
    carry_plan: dict[str, Any] | None,
) -> dict[str, str]:
    """``{"status": ok|low, "basis": ...}`` for the source the claim cites.

    ``low`` when the parser's own confidence is under ``MIN_SOURCE_CONFIDENCE``,
    when the text is under ``MIN_SOURCE_CHARS``, when the carry plan says the
    source was not carried into the compile, or when it was truncated and the
    cited text lies beyond the carried characters. An unknown confidence
    (``None``) is not low — nothing is invented in either direction; the basis
    says what was measured.
    """
    text = str(source.get("extracted_text") or "")
    confidence = source.get("parse_confidence")
    if confidence is None:
        confidence = source.get("confidence")
    try:
        confidence_value = float(confidence) if confidence is not None else None
    except (TypeError, ValueError):
        confidence_value = None
    if confidence_value is not None and confidence_value < MIN_SOURCE_CONFIDENCE:
        return {
            "status": "low",
            "basis": f"parse confidence {confidence_value:.2f} is under {MIN_SOURCE_CONFIDENCE}",
        }
    if len(text.strip()) < MIN_SOURCE_CHARS:
        return {
            "status": "low",
            "basis": f"the source carries {len(text.strip())} characters of text (under {MIN_SOURCE_CHARS})",
        }
    entry = _carry_entry(source, carry_plan)
    if entry is not None:
        if not entry.get("included"):
            return {
                "status": "low",
                "basis": "the source was not carried into the compile"
                + (f" ({entry.get('dropped_reason')})" if entry.get("dropped_reason") else ""),
            }
        if entry.get("truncated") and quote:
            offset = _quote_offset(quote, text)
            if offset > int(entry.get("chars") or 0):
                return {
                    "status": "low",
                    "basis": "the cited text lies beyond the region carried into the compile "
                    f"(source truncated at {entry.get('chars')} characters)",
                }
    basis = f"verbatim quote in a source of {len(text.strip())} characters"
    if confidence_value is not None:
        basis += f", parse confidence {confidence_value:.2f}"
    return {"status": "ok", "basis": basis}


def wording_check(claim: str, quote: str | None) -> dict[str, list[str]]:
    """High-risk terms in the claim whose stem the verbatim evidence does not carry."""
    flags: list[str] = []
    unsupported: list[str] = []
    quote_text = str(quote or "")
    for match in _HIGH_RISK_RE.finditer(str(claim or "")):
        term = match.group(1).lower()
        stem = HIGH_RISK_TERMS.get(term)
        if stem is None:
            continue
        if re.search(stem, quote_text, re.IGNORECASE):
            continue
        if term not in unsupported:
            unsupported.append(term)
            flags.append(f"high_risk_wording:{term}")
    return {"flags": flags, "unsupported_terms": unsupported}


def labelled_figures(text: str) -> list[tuple[str, str, str]]:
    """``(qualified label, kind, value)`` for each figure with a label shortly
    before it (``numeric_recompute.labelled``): "collision deductible" and
    "comprehensive deductible" are different labels. Used by the within-document
    consistency pass in ``services/audit_summary``. Dates are not labelled."""
    return [(label, fig.kind, format_decimal(fig.value)) for label, fig in labelled(text)]


# --------------------------------------------------------------------------- #
# Enumerations — sub-claims
# --------------------------------------------------------------------------- #


def _protect_figures(text: str) -> tuple[str, list[Figure]]:
    """``text`` with each figure replaced by ``\x00n\x00`` so a comma inside
    ``$1,250`` does not split a segment."""
    figures = extract_figures(text)
    out: list[str] = []
    cursor = 0
    for index, fig in enumerate(figures):
        out.append(text[cursor : fig.start])
        out.append(f"\x00{index}\x00")
        cursor = fig.end
    out.append(text[cursor:])
    return "".join(out), figures


def _restore_figures(segment: str, figures: list[Figure]) -> str:
    return re.sub(r"\x00(\d+)\x00", lambda m: figures[int(m.group(1))].text, segment)


def strip_label_line(content: str) -> str:
    """``content`` without the draft's leading label line, when it has one."""
    return _LABEL_LINE_RE.sub("", str(content or ""), count=1)


def split_sub_claims(content: str) -> list[str]:
    """The value-bearing segments of an enumeration, or ``[]`` when the paragraph
    is not one.

    Segments are split on ``;``, ``,`` and `` and `` with figures protected; a
    segment counts when it carries a figure, a ``label: value`` pair, an
    identifier (policy number, VIN) or a capitalised name after a label word. A
    paragraph with fewer than ``MIN_ENUMERATION_ITEMS`` such segments is one
    claim and is judged whole.
    """
    text = strip_label_line(content)
    if any(recompute(text, text).get("checks")) or _states_arithmetic(text):
        return []
    protected, figures = _protect_figures(text)
    raw_segments = re.split(r"\s*(?:;|,|\band\b)\s*", protected)
    segments: list[str] = []
    for raw in raw_segments:
        piece = _restore_figures(raw, figures).strip(" .:")
        if not piece:
            continue
        if ":" in piece and piece.index(":") < len(piece) - 1 and piece.split(":", 1)[0].strip():
            # "Key details are as follows: policy number AP-1" → drop the intro.
            head, tail = piece.split(":", 1)
            if not extract_figures(head) and not _ID_TOKEN_RE.search(head) and not _NAME_RE.search(head):
                if len(head.split()) > 4:
                    piece = tail.strip()
        if extract_figures(piece) or _ID_TOKEN_RE.search(piece) or _NAME_RE.search(piece) or ":" in piece:
            segments.append(piece)
    return segments if len(segments) >= MIN_ENUMERATION_ITEMS else []


_ARITHMETIC_CUE_RE = re.compile(
    r"\b(?:in total|sum of|combined|altogether|increased?|decreased?|rose|fell|grew|declined|"
    r"reduced|dropped|percent of|equals?|amounts? to|comes? to|difference|change of|"
    r"up from|down from|an increase|a decrease|a reduction)\b|% of|=|\+",
    re.IGNORECASE,
)


def _states_arithmetic(text: str) -> bool:
    """Whether the paragraph reads as a calculation (a relation between its
    figures) rather than a list of facts. Cue words only — the recompute decides
    what the figures hold; this just keeps such a sentence whole. "total premium"
    and "from 01/15/2025 to 01/15/2026" are a label and a period, not cues."""
    figures = [fig for fig in extract_figures(text) if fig.kind != "date"]
    return len(figures) >= 2 and bool(_ARITHMETIC_CUE_RE.search(text))


_BOUNDARY_LEFT_RE = re.compile(r"[.;!?](?=\s)|\n")
_BOUNDARY_RIGHT_RE = re.compile(r"[.;!?](?=\s|$)|\n")


def _sentence_around(text: str, start: int, end: int) -> str:
    """The sentence of ``text`` containing ``[start, end)``: bounded by a newline
    or by ``.``/``;``/``!``/``?`` followed by whitespace — so the ``.`` inside
    ``$1,250.00`` is not a boundary and a declarations line is one sentence."""
    lefts = [m.end() for m in _BOUNDARY_LEFT_RE.finditer(text, 0, start)]
    left = lefts[-1] if lefts else 0
    right_match = _BOUNDARY_RIGHT_RE.search(text, end)
    if right_match is None:
        right = len(text)
    else:
        right = right_match.end() if text[right_match.start()] != "\n" else right_match.start()
    return collapse_whitespace(text[left:right])


def _line_form(text: str) -> str:
    """``verbatim_form`` that keeps line breaks (a declarations page is one fact
    per line, and the line is the sentence)."""
    out = re.sub(r"[ \t]+", " ", str(text or ""))
    out = re.sub(r" ?\n ?", "\n", out).strip()
    return _INITIAL_PERIOD_RE.sub(r"\1", out)


def _find_value(value: str, source_text: str) -> str | None:
    """The source sentence carrying ``value`` verbatim (whitespace-collapsed,
    case-insensitive), or ``None``. Searched first with line breaks kept, so the
    quote is the line that carries the value; a value that spans a line break is
    found in the fully collapsed text."""
    needle = verbatim_form(value).lower()
    if len(needle) < 2:
        return None
    hay = _line_form(source_text)
    pos = hay.lower().find(needle)
    if pos >= 0:
        return _sentence_around(hay, pos, pos + len(needle))
    hay = verbatim_form(source_text)
    pos = hay.lower().find(needle)
    if pos < 0:
        return None
    return _sentence_around(hay, pos, pos + len(needle))


def assess_sub_claim(segment: str, source_text: str) -> dict[str, Any]:
    """One enumerated fact against the whole source text — deterministic.

    * with figures: every figure must appear in the source (normalised) and, when
      the segment names a label ("total premium $1,250"), the label's head noun
      must occur in the sentence that carries the figure; a same-label figure the
      source states differently is ``contradicted``;
    * without figures: the value (identifier, capitalised name, or the text after
      ``label:``) must be verbatim in the source.
    """
    text = strip_label_line(segment).strip()
    figures = extract_figures(text)
    out: dict[str, Any] = {"text": text, "status": "unsupported", "detail": "", "evidence": None}
    if figures:
        result = recompute(text, "", source_text=source_text)
        if result["status"] == "mismatch" and result.get("kind") == "arithmetic":
            out.update(status="contradicted", detail=result["detail"])
            return out
        if result["status"] == "mismatch":
            out.update(detail=result["detail"])
            return out
        source_text = _line_form(source_text)
        source_figures = extract_figures(source_text)
        labels = dict(labelled(text, figures))
        evidence_parts: list[str] = []
        for fig in figures:
            hit = next((other for other in source_figures if fig.same_value(other)), None)
            if hit is None:
                out.update(detail=f"{fig.text} not in the source")
                return out
            sentence = _sentence_around(source_text, hit.start, hit.end)
            label = next((lbl for lbl, f in labels.items() if f is fig), "")
            head = label.split()[-1] if label else ""
            if head and not re.search(rf"\b{re.escape(head)}s?\b", sentence, re.IGNORECASE):
                # The figure exists somewhere, but not under this label: search for a
                # sentence carrying both before giving up.
                alternates = [
                    _sentence_around(source_text, other.start, other.end)
                    for other in source_figures
                    if fig.same_value(other)
                ]
                sentence = next(
                    (alt for alt in alternates if re.search(rf"\b{re.escape(head)}s?\b", alt, re.IGNORECASE)),
                    "",
                )
                if not sentence:
                    out.update(detail=f"{fig.text} is in the source but not as the {label}")
                    return out
            if sentence not in evidence_parts:
                evidence_parts.append(sentence)
        out.update(status="verified", detail="figure(s) verbatim in the source", evidence=" ".join(evidence_parts))
        return out

    candidates: list[str] = []
    pair = _LABEL_VALUE_RE.match(text)
    if pair and pair.group("value").strip():
        candidates.append(pair.group("value").strip(" .'\""))
    candidates += [m.group(0) for m in _ID_TOKEN_RE.finditer(text)]
    candidates += [m.group(0) for m in _NAME_RE.finditer(text)]
    seen: list[str] = []
    for candidate in candidates:
        if candidate not in seen:
            seen.append(candidate)
    if not seen:
        out.update(status="skipped", detail="no value to check")
        return out
    for candidate in seen:
        sentence = _find_value(candidate, source_text)
        if sentence:
            out.update(status="verified", detail=f"'{candidate}' verbatim in the source", evidence=sentence)
            return out
    out.update(detail=f"'{seen[0]}' not in the source")
    return out


# --------------------------------------------------------------------------- #
# Sentences — the claim unit (2026-09-27, second calibration)
# --------------------------------------------------------------------------- #

_ABBREVIATION_RE = re.compile(
    r"(?:\b(?:Mr|Mrs|Ms|Dr|Prof|No|Nos|Inc|Ltd|Co|Corp|St|vs|etc|approx|Jr|Sr|Fig|Sec|Art|Para|p|pp)|\b[A-Z])$"
)
#: Citation remnants the compile leaves after stripping ``[S<n>]`` markers.
_REMNANT_RE = re.compile(
    r"\s*,?\s*(?:as (?:stated|shown|noted|indicated|per|described|listed|given|documented) in "
    r"(?:the )?(?:sentences?|lines?|source|document|the source(?: document| material)?)(?: and)?|"
    r"according to (?:the )?(?:sentences?|lines?|source|document)(?: and)?|as per (?:sentences?|lines?)(?: and)?|"
    r"in (?:sentences?|lines?)(?: and)?)(?: of the source(?: document| material)?)?\s*,?",
    re.IGNORECASE,
)
_LABEL_SEGMENT_RE = re.compile(r"^[a-z][a-z /&-]{1,40}$")


def strip_remnants(text: str) -> str:
    """``text`` without citation remnants ("as stated in sentence", "according to
    line") and with the punctuation they left behind tidied."""
    out = _REMNANT_RE.sub(" ", str(text or ""))
    out = re.sub(r"\s+([.,;:])", r"\1", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out.strip()


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """``(start, end)`` of each sentence of ``text`` — split at ``.``/``!``/``?``
    followed by whitespace (not inside a figure, not after an initial or an
    abbreviation) and at newlines. A short lowercase label segment on its own
    line ("claim snapshot") is not a sentence and yields no span, so the indices
    agree between the draft with its ``[S<n>]`` markers (``routers/draft.
    attach_citations_to_tree``) and the stripped content this module reads.
    """
    raw = str(text or "")
    protected = [(fig.start, fig.end) for fig in extract_figures(raw)]
    spans: list[tuple[int, int]] = []
    start = 0
    index = 0
    length = len(raw)
    while index < length:
        char = raw[index]
        boundary = False
        if char == "\n":
            boundary = True
        elif char in ".!?" and not any(a <= index < b for a, b in protected):
            nxt = raw[index + 1] if index + 1 < length else ""
            if (nxt == "" or nxt.isspace()) and not (
                char == "." and _ABBREVIATION_RE.search(raw[start : index].rstrip())
            ):
                boundary = True
        if boundary:
            spans.append((start, index + 1))
            start = index + 1
        index += 1
    if start < length:
        spans.append((start, length))
    out: list[tuple[int, int]] = []
    for a, b in spans:
        segment = raw[a:b].strip()
        if not segment or segment in ".!?" or _LABEL_SEGMENT_RE.match(segment):
            continue
        lead = len(raw[a:b]) - len(raw[a:b].lstrip())
        trail = len(raw[a:b]) - len(raw[a:b].rstrip())
        out.append((a + lead, b - trail))
    return out


def split_sentences(text: str) -> list[str]:
    raw = str(text or "")
    return [raw[a:b] for a, b in sentence_spans(raw)]


def sentence_for_offset(text: str, offset: int) -> int | None:
    """The index of the sentence a citation marker at ``offset`` ends: the
    sentence containing it, or — when only whitespace separates it from the
    previous sentence's end — that previous sentence."""
    spans = sentence_spans(text)
    for index, (a, b) in enumerate(spans):
        if a <= offset < b:
            if index > 0 and not str(text[a:offset]).strip():
                return index - 1
            return index
    previous = [index for index, (a, b) in enumerate(spans) if b <= offset]
    if previous:
        return previous[-1]
    return 0 if spans else None


def _overlap_score(quote: str, sentence: str) -> int:
    quote_tokens = set(_tokenize(quote))
    sentence_tokens = set(_tokenize(sentence))
    score = len(quote_tokens & sentence_tokens)
    quote_figures = extract_figures(quote)
    for fig in extract_figures(sentence):
        if any(fig.same_value(other) for other in quote_figures):
            score += 2
    return score


def sentence_units(node: dict[str, Any]) -> list[dict[str, Any]]:
    """The paragraph's sentences, each with the provenance rows that belong to it.

    A row written by ``attach_citations_to_tree`` carries ``sentence_index`` — the
    sentence its ``[S<n>]`` marker ended. A row without one (the lexical matcher's)
    goes to the sentence its quote overlaps most (two shared content tokens, or a
    shared figure); a row that overlaps nothing is offered to sentences that have
    no row of their own. A single-sentence paragraph keeps every row.
    """
    content = str(node.get("content") or "")
    sentences = split_sentences(content)
    rows = _rows(node)
    if len(sentences) <= 1:
        text = strip_label_line(content).strip() or content.strip()
        return [{"index": 0, "text": text, "rows": list(rows)}] if text else []
    units = [{"index": i, "text": sent, "rows": []} for i, sent in enumerate(sentences)]
    unassigned: list[dict[str, Any]] = []
    for row in rows:
        idx = row.get("sentence_index")
        if isinstance(idx, int) and 0 <= idx < len(units):
            units[idx]["rows"].append(row)
            continue
        quote = _row_quote(row)
        scores = [(_overlap_score(quote, unit["text"]), unit["index"]) for unit in units]
        best_score, best_index = max(scores)
        if best_score >= 2:
            units[best_index]["rows"].append(row)
        else:
            unassigned.append(row)
    for unit in units:
        if not unit["rows"]:
            unit["rows"] = [row for row in unassigned if _overlap_score(_row_quote(row), unit["text"]) >= 1]
    return units


# --------------------------------------------------------------------------- #
# The claim block
# --------------------------------------------------------------------------- #


def _entailment_label(node: dict[str, Any]) -> str | None:
    meta = node.get("meta")
    prov = meta.get("provenance") if isinstance(meta, dict) else None
    record = prov.get("entailment") if isinstance(prov, dict) else None
    if not isinstance(record, dict):
        return None
    verdict = str(record.get("verdict") or "").strip().lower()
    return verdict or None


def _rows(node: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in (node.get("provenance") or []) if isinstance(row, dict)]


def _row_quote(row: dict[str, Any]) -> str:
    return str(row.get("extracted_quote") or "").strip() or str(row.get("anchor_window") or "").strip()


def _field_quote(row: dict[str, Any]) -> str:
    """The verbatim page quote of a citation row that names a Parsure field
    (``grounding_source: "parsure_field"``), else ``""``. The row's
    ``extracted_quote`` is the "Label: value" sentence the model was shown; the
    page carries the field's quote, so that is what the verbatim test reads."""
    if str(row.get("grounding_source") or "") != "parsure_field":
        return ""
    return str(row.get("field_quote") or "").strip()


def _mark_field(unit: dict[str, Any], row: dict[str, Any]) -> None:
    unit["grounding_source"] = "parsure_field"
    unit["field"] = str(row.get("field") or "") or None
    unit["element_id"] = row.get("element_id")


def _field_basis(quality: dict[str, str], row: dict[str, Any]) -> dict[str, str]:
    """``source_quality`` with the field report named, when the citation is a field."""
    if not _field_quote(row):
        return quality
    page = page_of(row)
    note = f"from the field report (Parsure field {str(row.get('field') or '?')!r}"
    note += f", verbatim on page {page})" if page else ", verbatim in the source)"
    return {**quality, "basis": f"{quality.get('basis') or ''}; {note}".lstrip("; ")}


def _empty_numeric(detail: str) -> dict[str, Any]:
    return {"status": "not_applicable", "kind": None, "detail": detail, "expected": None, "stated": None, "missing": [], "checks": []}


def _lexical_fallback(text: str, sources: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """A sentence with no citation, searched in the supplied sources: the sentence
    itself verbatim (remnants stripped), else its values under their labels
    (``assess_sub_claim``). Returns ``(sub_claim, source)`` or ``(None, None)``
    when no source carries anything checkable."""
    clean = strip_remnants(text).rstrip(".")
    best: tuple[dict[str, Any], dict[str, Any]] | None = None
    for source in sources:
        source_text = str(source.get("extracted_text") or "")
        sentence = _find_value(clean, source_text) if len(clean) >= 12 else None
        if sentence:
            return {"text": text, "status": "verified", "detail": "sentence verbatim in the source", "evidence": sentence}, source
        assessed = assess_sub_claim(clean, source_text)
        if assessed["status"] == "verified":
            return assessed, source
        if best is None or (assessed["status"] == "contradicted" and best[0]["status"] != "contradicted"):
            best = (assessed, source)
    if best is None or best[0]["status"] == "skipped":
        return None, None
    return best


def _assess_unit(
    text: str,
    rows: list[dict[str, Any]],
    *,
    sources: list[dict[str, Any]] | None,
    carry_plan: dict[str, Any] | None,
    entailment: str | None,
    numeric: dict[str, Any] | None = None,
    wording: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """One claim unit — a sentence, or a one-sentence paragraph — through rules
    1–11 (module docstring). Returns the unit block; does not touch the node."""
    content = str(text or "").strip()
    cited = [row for row in rows if _row_quote(row)]
    unit: dict[str, Any] = {
        "text": content,
        "verdict": UNSUPPORTED,
        "reason": "",
        "quote": None,
        "quote_verbatim": False,
        "source_id": "",
        "source_name": "",
        "page": None,
        "checks": {
            "kind": "fact",
            "entailment": entailment,
            "numeric": None,
            "wording": None,
            "source_quality": {"status": "missing", "basis": ""},
        },
        "flags": [],
    }
    evidence_text = ""

    def _finish(verdict: str, reason: str) -> dict[str, Any]:
        unit["verdict"] = verdict
        unit["reason"] = reason
        if unit["checks"]["wording"] is None:
            unit["checks"]["wording"] = (
                wording if wording is not None else wording_check(content, evidence_text or unit["quote"])
            )
        if unit["checks"]["numeric"] is None:
            unit["checks"]["numeric"] = (
                numeric if numeric is not None else recompute(content, evidence_text or unit["quote"] or "")
            )
        unit["flags"] = list(unit["checks"]["wording"].get("flags") or [])
        return unit

    def _cite(row: dict[str, Any], source: dict[str, Any] | None = None) -> None:
        unit["source_id"] = str(row.get("source_id") or (source or {}).get("id") or (source or {}).get("source_id") or "")
        unit["source_name"] = str(row.get("source_name") or (source or {}).get("filename") or "")
        unit["page"] = page_of(row)

    sub_claims = split_sub_claims(content)

    # Rule 2 (no corpus) before rule 1 so an unanchored unit is still UNSUPPORTED.
    if sources is None:
        if not cited:
            unit["checks"]["source_quality"] = {"status": "missing", "basis": "no citation or anchor on the paragraph"}
            return _finish(UNSUPPORTED, "no source sentence carries this claim")
        _cite(cited[0])
        unit["checks"]["source_quality"] = {
            "status": "missing",
            "basis": "no source documents were supplied to the verifier",
        }
        if entailment == "contradicts":
            return _finish(CONTRADICTED, "the source states otherwise (per the entailment check; source text not supplied)")
        if entailment == "partial":
            return _finish(UNSUPPORTED, "a material qualifier is missing")
        if entailment == "no":
            return _finish(UNSUPPORTED, "the source does not state this claim")
        return _finish(INSUFFICIENT_EVIDENCE, "the cited source was not supplied to the verifier")

    chosen_row: dict[str, Any] | None = None
    chosen_source: dict[str, Any] | None = None
    any_source_found = False
    found_sources: list[dict[str, Any]] = []
    evidence_parts: list[str] = []
    for row in cited:
        source = find_source(row, sources)
        if source is None:
            continue
        any_source_found = True
        if source not in found_sources:
            found_sources.append(source)
        source_text = str(source.get("extracted_text") or "")
        # A field citation is tested on the field's page quote (module docstring,
        # "Form fields"); every other row on the sentence the model cited.
        quote = _field_quote(row) or _row_quote(row)
        if not quote_is_verbatim(quote, source_text):
            continue
        if chosen_row is None:
            chosen_row, chosen_source = row, source
        window = str(row.get("anchor_window") or "").strip()
        part = window if window and quote_is_verbatim(window, source_text) else quote
        if part not in evidence_parts:
            evidence_parts.append(part)
    evidence_text = " ".join(evidence_parts)

    # Rule 1 — nothing cites a source sentence: the lexical fallback, per sentence.
    if not cited or (cited and not any_source_found and not sub_claims):
        if cited and not any_source_found:
            _cite(cited[0])
            unit["checks"]["source_quality"] = {
                "status": "missing",
                "basis": "the cited source is not among the supplied documents",
            }
            return _finish(INSUFFICIENT_EVIDENCE, "the cited source is not among the supplied documents")
        if not sub_claims:
            fallback, source = _lexical_fallback(content, sources)
            if fallback is None or source is None:
                unit["checks"]["source_quality"] = {"status": "missing", "basis": "no citation or anchor on the paragraph"}
                return _finish(UNSUPPORTED, "no source sentence carries this claim")
            unit["checks"]["kind"] = "fact"
            unit["source_id"] = str(source.get("id") or source.get("source_id") or "")
            unit["source_name"] = str(source.get("filename") or "")
            unit["quote"] = fallback.get("evidence")
            unit["quote_verbatim"] = bool(fallback.get("evidence"))
            evidence_text = str(fallback.get("evidence") or "")
            quality = source_quality(source, quote=evidence_text, carry_plan=carry_plan)
            unit["checks"]["source_quality"] = quality
            unit["checks"]["numeric"] = recompute(content, evidence_text, source_text=str(source.get("extracted_text") or ""))
            if fallback["status"] == "unsupported":
                # Nothing found to judge: not in the source, whatever its quality.
                return _finish(UNSUPPORTED, f"not found in the source: {fallback['detail']}")
            if quality["status"] != "ok":
                return _finish(INSUFFICIENT_EVIDENCE, f"source quality is insufficient: {quality['basis']}")
            if fallback["status"] == "verified":
                return _finish(VERIFIED, f"found in the source without a citation: {fallback['detail']}")
            return _finish(CONTRADICTED, "the source states otherwise: " + fallback["detail"])

    # Rule 5 — an enumeration: every fact against the whole cited source.
    if sub_claims:
        unit["checks"]["kind"] = "enumeration"
        candidates = found_sources or list(sources)
        if not candidates:
            unit["checks"]["source_quality"] = {"status": "missing", "basis": "no source documents supplied"}
            return _finish(INSUFFICIENT_EVIDENCE, "no source documents to assess the enumerated facts against")
        best: list[dict[str, Any]] = []
        best_source: dict[str, Any] = candidates[0]
        for source in candidates:
            source_text = str(source.get("extracted_text") or "")
            assessed = [assess_sub_claim(segment, source_text) for segment in sub_claims]
            if not best or sum(a["status"] == "verified" for a in assessed) > sum(a["status"] == "verified" for a in best):
                best, best_source = assessed, source
        unit["checks"]["sub_claims"] = best
        if chosen_row is not None and chosen_source is best_source:
            _cite(chosen_row, chosen_source)
            unit["quote"] = _field_quote(chosen_row) or _row_quote(chosen_row)
            unit["quote_verbatim"] = True
            if _field_quote(chosen_row):
                _mark_field(unit, chosen_row)
        else:
            row = cited[0] if cited else {}
            _cite(row, best_source)
            first_evidence = next((a["evidence"] for a in best if a.get("evidence")), None)
            unit["quote"] = first_evidence
            unit["quote_verbatim"] = bool(first_evidence)
        evidence_text = " ".join(a["evidence"] for a in best if a.get("evidence"))
        quality = source_quality(best_source, quote=unit["quote"] or "", carry_plan=carry_plan)
        if chosen_row is not None and chosen_source is best_source:
            quality = _field_basis(quality, chosen_row)
        unit["checks"]["source_quality"] = quality
        if quality["status"] != "ok":
            return _finish(INSUFFICIENT_EVIDENCE, f"source quality is insufficient: {quality['basis']}")
        unit["checks"]["numeric"] = _empty_numeric("figures checked per sub-claim")
        if entailment == "contradicts":
            return _finish(CONTRADICTED, "the source states otherwise")
        contradicted = [a for a in best if a["status"] == "contradicted"]
        if contradicted:
            return _finish(CONTRADICTED, "the source states otherwise: " + "; ".join(a["detail"] for a in contradicted))
        failing = [a for a in best if a["status"] == "unsupported"]
        checked = [a for a in best if a["status"] in ("verified", "unsupported", "contradicted")]
        if failing:
            return _finish(
                UNSUPPORTED,
                f"{len(failing)} of {len(checked)} enumerated facts not found in the source: "
                + "; ".join(a["detail"] for a in failing),
            )
        if not checked or not unit["quote_verbatim"]:
            return _finish(INSUFFICIENT_EVIDENCE, "no enumerated fact could be checked against the source")
        return _finish(VERIFIED, f"all {len(checked)} enumerated facts are verbatim in the source")

    # Rule 3 — the cited text is not in the source.
    if chosen_row is None or chosen_source is None:
        _cite(cited[0])
        unit["checks"]["source_quality"] = {"status": "ok", "basis": "source supplied; cited text not found in it"}
        return _finish(UNSUPPORTED, "the cited text is not verbatim in the source")

    quote = _field_quote(chosen_row) or _row_quote(chosen_row)
    unit["quote"] = quote
    unit["quote_verbatim"] = True
    _cite(chosen_row, chosen_source)
    if _field_quote(chosen_row):
        _mark_field(unit, chosen_row)
    source_text = str(chosen_source.get("extracted_text") or "")
    unit["checks"]["wording"] = wording if wording is not None else wording_check(content, evidence_text)
    unit["checks"]["numeric"] = (
        numeric if numeric is not None else recompute(content, evidence_text, source_text=source_text)
    )

    # Rule 4 — source quality.
    quality = _field_basis(source_quality(chosen_source, quote=quote, carry_plan=carry_plan), chosen_row)
    unit["checks"]["source_quality"] = quality
    if quality["status"] != "ok":
        return _finish(INSUFFICIENT_EVIDENCE, f"source quality is insufficient: {quality['basis']}")

    # Rule 6 — the verifier did not answer.
    if entailment is None:
        return _finish(INSUFFICIENT_EVIDENCE, "no entailment check ran for this claim")
    if entailment == "unverified":
        return _finish(INSUFFICIENT_EVIDENCE, "verifier did not answer")

    numeric_status = str((unit["checks"]["numeric"] or {}).get("status") or "not_applicable")
    numeric_kind = str((unit["checks"]["numeric"] or {}).get("kind") or "")
    numeric_detail = str((unit["checks"]["numeric"] or {}).get("detail") or "")

    # Rule 7 — the source says otherwise, or the figures do not hold.
    if entailment == "contradicts":
        return _finish(CONTRADICTED, "the source states otherwise")
    if numeric_status == "mismatch" and numeric_kind == "arithmetic":
        return _finish(CONTRADICTED, f"the figures do not hold against the source: {numeric_detail}")
    # Rule 8 — a figure the source does not carry is not found, not contradicted.
    if numeric_status == "mismatch":
        return _finish(UNSUPPORTED, f"a figure in the claim is not in the source: {numeric_detail}")

    # Rule 9 — verified only on yes with the figures accounted for.
    if entailment == "yes":
        if numeric_status in ("recomputed_ok", "not_applicable"):
            return _finish(
                VERIFIED,
                "the quoted source text states the claim"
                + (f"; {numeric_detail}" if numeric_status == "recomputed_ok" else ""),
            )
        return _finish(INSUFFICIENT_EVIDENCE, f"the stated calculation cannot be recomputed: {numeric_detail}")

    # Rules 10–11.
    if entailment == "partial":
        return _finish(UNSUPPORTED, "a material qualifier is missing")
    return _finish(UNSUPPORTED, "the source does not state this claim")


def _sentence_entailment(node: dict[str, Any], index: int, fallback: str | None) -> str | None:
    """The entailment label for sentence ``index`` (``entailment.sentences``),
    else ``fallback`` — the paragraph label — when the record predates sentences."""
    meta = node.get("meta")
    prov = meta.get("provenance") if isinstance(meta, dict) else None
    record = prov.get("entailment") if isinstance(prov, dict) else None
    if not isinstance(record, dict):
        return fallback
    for entry in record.get("sentences") or []:
        if isinstance(entry, dict) and entry.get("index") == index:
            verdict = str(entry.get("verdict") or "").strip().lower()
            return verdict or None
    if record.get("sentences"):
        # Per-sentence records exist and this sentence has none: it was not
        # judged (no citation reached it), which is not the paragraph's answer.
        return None
    return fallback


def _aggregate_units(units: list[dict[str, Any]]) -> tuple[str, str]:
    verdicts = [str(unit.get("verdict") or "") for unit in units]
    verified = sum(v == VERIFIED for v in verdicts)
    failing = [unit for unit in units if unit.get("verdict") != VERIFIED]
    detail = "; ".join(f"'{unit['text'][:60]}' — {unit['reason']}" for unit in failing[:4])
    if any(v == CONTRADICTED for v in verdicts):
        return CONTRADICTED, f"{verified} of {len(units)} sentences verified; a sentence is contradicted: {detail}"
    if all(v == VERIFIED for v in verdicts):
        return VERIFIED, f"all {len(units)} sentences are verified against the source"
    if any(v == UNSUPPORTED for v in verdicts):
        return UNSUPPORTED, f"{verified} of {len(units)} sentences verified: {detail}"
    return INSUFFICIENT_EVIDENCE, f"{verified} of {len(units)} sentences verified: {detail}"


def derive_claim(
    node: dict[str, Any],
    *,
    sources: list[dict[str, Any]] | None,
    carry_plan: dict[str, Any] | None = None,
    entailment: str | None = None,
    numeric: dict[str, Any] | None = None,
    wording: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Derive the ``claim`` block for one paragraph and write it at
    ``node.meta.provenance.claim``. Returns the block.

    The claim unit is the sentence: a paragraph of several sentences is assessed
    sentence by sentence (``checks.kind: sentences``, each in
    ``checks.sub_claims``) and carries the aggregate — VERIFIED only when every
    sentence is, CONTRADICTED when any is, else UNSUPPORTED / INSUFFICIENT. A
    one-sentence paragraph is the unit itself. A statement about the draft or
    the source (``checks.kind: meta``) is not a claim: ``verdict`` is ``null``.

    ``entailment`` defaults to the label at ``meta.provenance.entailment`` (per
    sentence when the record carries ``sentences``); ``numeric`` and ``wording``
    default to ``numeric_recompute.recompute`` and ``wording_check`` over the
    verbatim evidence and apply to a one-unit paragraph only. ``sources`` is the
    list of substrate rows the compile was handed; ``None`` means the caller
    could not supply them — nothing can then be VERIFIED, and the block says so.
    """
    content = str(node.get("content") or "").strip()
    paragraph_label = entailment if entailment is not None else _entailment_label(node)

    def _write(block: dict[str, Any]) -> dict[str, Any]:
        meta = dict(node.get("meta") or {})
        prov = dict(meta.get("provenance") or {})
        prov["claim"] = block
        meta["provenance"] = prov
        node["meta"] = meta
        return block

    # Rule 0 — a statement about the draft or the source, not a document fact.
    if is_meta_statement(content):
        rows = _rows(node)
        first = rows[0] if rows else {}
        return _write(
            {
                "policy": CLAIM_POLICY_ID,
                "verdict": None,
                "reason": "statement about the source, not a document fact",
                "quote": None,
                "quote_verbatim": False,
                "source_id": str(first.get("source_id") or ""),
                "source_name": str(first.get("source_name") or ""),
                "page": page_of(first) if first else None,
                "checks": {
                    "kind": "meta",
                    "entailment": paragraph_label,
                    "numeric": None,
                    "wording": None,
                    "source_quality": {"status": "missing", "basis": "not a statement about the document"},
                },
                "flags": [],
            }
        )

    units = sentence_units(node)
    if len(units) <= 1:
        text = units[0]["text"] if units else content
        rows = units[0]["rows"] if units else _rows(node)
        unit = _assess_unit(
            text,
            rows,
            sources=sources,
            carry_plan=carry_plan,
            entailment=paragraph_label,
            numeric=numeric,
            wording=wording,
        )
        block = {"policy": CLAIM_POLICY_ID, **{k: v for k, v in unit.items() if k != "text"}}
        return _write(block)

    assessed: list[dict[str, Any]] = []
    for unit in units:
        label = _sentence_entailment(node, unit["index"], paragraph_label if entailment is not None else None)
        assessed.append(
            _assess_unit(unit["text"], unit["rows"], sources=sources, carry_plan=carry_plan, entailment=label)
        )
    verdict, reason = _aggregate_units(assessed)
    lead = next((u for u in assessed if u["verdict"] == VERIFIED and u["quote"]), None) or assessed[0]
    flags: list[str] = []
    for u in assessed:
        for flag in u.get("flags") or []:
            if flag not in flags:
                flags.append(flag)
    block = {
        "policy": CLAIM_POLICY_ID,
        "verdict": verdict,
        "reason": reason,
        "quote": lead["quote"],
        "quote_verbatim": bool(lead["quote_verbatim"]),
        "source_id": lead["source_id"],
        "source_name": lead["source_name"],
        "page": lead["page"],
        "checks": {
            "kind": "sentences",
            "entailment": paragraph_label,
            "numeric": _empty_numeric("figures checked per sentence"),
            "wording": {
                "flags": flags,
                "unsupported_terms": sorted({t for u in assessed for t in (u["checks"].get("wording") or {}).get("unsupported_terms") or []}),
            },
            "source_quality": lead["checks"]["source_quality"],
            "sub_claims": assessed,
        },
        "flags": flags,
    }
    return _write(block)


def claim_block(node: dict[str, Any]) -> dict[str, Any] | None:
    """The persisted ``meta.provenance.claim`` block, or ``None``."""
    meta = node.get("meta")
    prov = meta.get("provenance") if isinstance(meta, dict) else None
    block = prov.get("claim") if isinstance(prov, dict) else None
    if not isinstance(block, dict):
        return None
    if block.get("verdict") in VERDICTS:
        return block
    if block.get("verdict") is None and (block.get("checks") or {}).get("kind") == "meta":
        return block
    return None


def is_meta_block(block: dict[str, Any] | None) -> bool:
    return bool(block) and (block.get("checks") or {}).get("kind") == "meta"


def claim_units(block: dict[str, Any]) -> list[dict[str, Any]]:
    """The claim units a block counts as: its sentences for ``kind: sentences``,
    else the block itself. A meta block has none."""
    if is_meta_block(block):
        return []
    if (block.get("checks") or {}).get("kind") == "sentences":
        return [u for u in (block["checks"].get("sub_claims") or []) if isinstance(u, dict)]
    return [block]


def attach_claims_to_tree(
    document: dict[str, Any],
    *,
    sources: list[dict[str, Any]] | None,
    carry_plan: dict[str, Any] | None = None,
    only_missing: bool = False,
) -> dict[str, Any]:
    """Derive a claim block for every claim-eligible paragraph, in place.

    Runs after ``entailment.attach_entailment_to_tree``. With ``only_missing`` the
    blocks a previous derivation wrote are kept (used when a caller has no sources
    to re-derive against and must not downgrade persisted verdicts to "source not
    supplied").
    """
    if not isinstance(document, dict):
        return document
    for section in document.get("body") or []:
        if not isinstance(section, dict):
            continue
        for node in [section, *(section.get("children") or [])]:
            if not isinstance(node, dict) or str(node.get("type") or "") != "paragraph":
                continue
            if not is_claim_eligible(str(node.get("content") or "")):
                continue
            if only_missing and claim_block(node) is not None:
                continue
            derive_claim(node, sources=sources, carry_plan=carry_plan)
    return document


__all__ = [
    "CLAIM_POLICY_ID",
    "CONTRADICTED",
    "HIGH_RISK_TERMS",
    "INSUFFICIENT_EVIDENCE",
    "MIN_ENUMERATION_ITEMS",
    "MIN_SHORT_CLAIM_TOKENS",
    "MIN_SOURCE_CHARS",
    "MIN_SOURCE_CONFIDENCE",
    "UNSUPPORTED",
    "VERDICTS",
    "VERIFIED",
    "assess_sub_claim",
    "attach_claims_to_tree",
    "claim_block",
    "claim_units",
    "derive_claim",
    "find_source",
    "is_claim_eligible",
    "is_meta_block",
    "is_meta_statement",
    "labelled_figures",
    "page_of",
    "quote_is_verbatim",
    "source_quality",
    "sentence_for_offset",
    "sentence_spans",
    "sentence_units",
    "split_sentences",
    "split_sub_claims",
    "strip_label_line",
    "strip_remnants",
    "verbatim_form",
    "wording_check",
]
