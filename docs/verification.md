# Verification — the claim contract (policy `claim-v1`, 2026-09-27)

This is what Assure asserts about a compiled document's claims, how each verdict
is derived, and what is *not* claimed. Code: `prompt_matrix/services/claim_policy.py`
(verdicts), `services/numeric_recompute.py` (figures and arithmetic),
`services/entailment.py` (the model judgement), `services/audit_summary.py`
(counters, gate, summary). Wiring: `routers/draft.py` after
`attach_entailment_to_tree`, and the cache-replay recount
(`_recount_cached_verified`).

## The customer's rule

Every factual claim, legal statement, citation, exclusion, date, percentage,
monetary figure and calculation is assessed. A claim is **VERIFIED** only when it
is directly supported by the supplied source documents; **UNSUPPORTED** when it
cannot be found or proven; **CONTRADICTED** when it conflicts with a source;
**INSUFFICIENT_EVIDENCE** when the source is not good enough to decide. Model
confidence is never evidence. Pages, quotes, citations and facts are never
invented. Calculations are recomputed from the source's figures.

## The claim unit

A paragraph node one level under a section is claim-bearing when it has at
least four content tokens (the lexical matcher's floor,
`models/jdf._MIN_CLAIM_TOKENS`), **or** at least two content tokens and one of: a
figure (money, percent, number, date), an exclusion word
(*exclude/excluded/except/unless*), or a high-risk term. "Flood is excluded." is
a claim. "Coverage summary" is not. (`claim_policy.is_claim_eligible`.)

**The unit is the sentence.** A paragraph of several sentences (the memo shape
writes six sections of several facts each) is split (`claim_policy.split_sentences`
— figures and initials protected, the draft's label line dropped) and each
sentence is assessed on its own: a `[S<n>]` citation belongs to the sentence it
ends (`attach_citations_to_tree` records `sentence_index` on the row), a lexical
anchor row goes to the sentence its quote overlaps, and a sentence with no row
is searched in the cited sources (the sentence verbatim, or its values under
their labels). The entailment model is asked per sentence against that
sentence's windows (`entailment.sentences[]`). The paragraph block has
`checks.kind: "sentences"`, `checks.sub_claims[]` = one unit per sentence
(`text, verdict, reason, quote, quote_verbatim, source_id, source_name, page,
checks, flags`) and the aggregate verdict: VERIFIED only when every sentence
is; CONTRADICTED when any is; else UNSUPPORTED (any sentence unsupported) or
INSUFFICIENT_EVIDENCE. A one-sentence paragraph is the unit itself.

**Meta paragraphs are not claims.** "The source does not provide…", "The
information extracted is…", or anything under the template's `missing items` /
`confidence` labels gets `checks.kind: "meta"`, `verdict: null`, reason
"statement about the source, not a document fact". They are counted in
`claim_summary.meta`, excluded from `total`/`unsupported`, and never hold the
gate — a memo template must not make every document unreachable for `pass`.

A paragraph that enumerates two or more `label: value` facts ("policy number
AP-2025-0001, total premium $1,250, liability limit $100,000, …") and states no
calculation is an **enumeration**: it is assessed per sub-claim
(`checks.sub_claims`) because the customer's unit is the fact, not the paragraph,
and no three-sentence anchor window carries eight facts — nor does a one-line
window carry both halves of "the named insured is X and the policy period is Y".
A sentence that states arithmetic ("rose from $1,000 to $1,200, an increase of
20%") is one claim, kept whole and recomputed.

## The block

Written at `node.meta.provenance.claim`:

```
{"policy": "claim-v1",
 "verdict": "VERIFIED" | "UNSUPPORTED" | "CONTRADICTED" | "INSUFFICIENT_EVIDENCE",
 "reason": "<one sentence>",
 "quote": "<verbatim source text>" | null,
 "quote_verbatim": true | false,
 "source_id": "<substrate row id>", "source_name": "<file name>",
 "page": <int> | null,
 "checks": {
   "kind": "fact" | "meta" | "enumeration",
   "entailment": "yes" | "partial" | "no" | "contradicts" | "unverified" | null,
   "numeric": {"status": "recomputed_ok" | "mismatch" | "not_applicable" | "insufficient",
               "kind": "arithmetic" | "missing_figure" | null,
               "detail": "...", "expected": ..., "stated": ..., "missing": [...], "checks": [...]},
   "wording": {"flags": ["high_risk_wording:<term>", ...], "unsupported_terms": [...]},
   "source_quality": {"status": "ok" | "low" | "missing", "basis": "<what was measured>"},
   "sub_claims": [{"text", "status": "verified"|"unsupported"|"contradicted"|"skipped",
                   "detail", "evidence"}]          # enumerations only
 },
 "flags": ["high_risk_wording:<term>", "inconsistent_figure:<label>", ...]}
```

`page` is read from the provenance row (`page` / `page_number`) and is `null`
when the row has none. It is never defaulted; `models/jdf.py` no longer
substitutes the document's page count for an unknown page.

## Rules, in order (first match decides)

0. A statement about the draft or the source itself ("The source does not
   provide…", "The source material provides the following…", "The information
   provided is…", "The confidence in the extracted information is…") →
   **UNSUPPORTED**, `kind: meta`, reason "statement about the source, not a
   document fact". Matched at the start of any line, because the compiled
   draft opens each paragraph with a short label line.
1. No citation and no anchor (and not an enumeration) → **UNSUPPORTED**
   ("no source sentence carries this claim").
2. The cited source is not among the supplied documents →
   **INSUFFICIENT_EVIDENCE** (`source_quality.status: missing`). When *no*
   sources were supplied at all, the entailment label still speaks
   (`contradicts` → CONTRADICTED, `no`/`partial` → UNSUPPORTED) but nothing can be
   VERIFIED: `yes` is INSUFFICIENT_EVIDENCE.
3. The cited text is not verbatim in the source's `extracted_text` →
   **UNSUPPORTED** ("the cited text is not verbatim in the source"). Verbatim
   means: whitespace collapsed and the period after a single-letter initial
   ignored ("John Q. Sample" == "John Q Sample", which the compile's sentence map
   drops); casing, words and figures must match exactly
   (`claim_policy.verbatim_form`). No path below can verify without passing this.
4. Source quality insufficient → **INSUFFICIENT_EVIDENCE** with the basis:
   `parse_confidence` under 0.5; under 200 characters of text; the source was
   dropped by the carry plan (`services/source_carry`); or it was truncated and
   the cited text lies beyond the carried characters. An unknown confidence is
   not "low" — nothing is invented in either direction.
5. An enumeration: every sub-claim is checked against the whole cited source
   (or every supplied source when nothing was cited). A sub-claim with figures
   is `verified` when each figure appears in the source and, where the segment
   names a label, the label's head noun occurs in the sentence carrying the
   figure; a same-label figure the source states differently is `contradicted`.
   A sub-claim without figures is `verified` when its value (identifier, name,
   or the text after `label:`) is verbatim in the source. The paragraph is
   **VERIFIED** only when every sub-claim is; any `contradicted` →
   **CONTRADICTED**; otherwise **UNSUPPORTED** naming the failing facts. The
   block's `quote` is the source sentence carrying the first sub-claim.
6. No entailment answer — the check never ran, or the model failed
   (`unverified`) → **INSUFFICIENT_EVIDENCE** ("verifier did not answer").
7. Entailment `contradicts`, or an arithmetic disagreement / same-label figure
   conflict from the numeric check → **CONTRADICTED**.
8. A figure the claim states that is in neither the evidence window nor the
   cited source's text → **UNSUPPORTED** ("a figure in the claim is not in the
   source"). A figure the source lacks does not *conflict*; it is not found.
9. Entailment `yes` and numeric `recomputed_ok` / `not_applicable` →
   **VERIFIED**. Numeric `insufficient` (a calculation is stated but its
   operands are not in the source) → **INSUFFICIENT_EVIDENCE**.
10. Entailment `partial` → **UNSUPPORTED** ("a material qualifier is missing").
    Partial is never verified.
11. Entailment `no` → **UNSUPPORTED**.

### Entailment (four labels, prompt version 3)

`services/entailment.py` asks the SEMANTIC_VALIDATION model one question per
(claim, anchor window): `VERDICT: yes|partial|no|contradicts`, one `REASON`
sentence, and an `EVIDENCE` line. `no` means the source does not state the
claim (absent figure, unrelated window). `contradicts` means the source states a
*different* value, party, date or negation *for the same item* — and it stands
only when the model's `EVIDENCE` text is found verbatim in the window
(`enforce_contradiction_evidence`, via `llm_extraction.find_verbatim`) **and**
its own `REASON` does not describe an absence ("does not mention", "does not
contain") without naming a conflict, **and** — when the reason claims a numeric
difference — the evidence's values (years aside) do not all reappear in the
claim ("Comprehensive Deductible: $250" cannot contradict a claim that states
the comprehensive deductible as $250); otherwise it is downgraded to `no` and
the record carries `downgraded_from: contradicts`. A fact merely absent from a
one-sentence window is never a contradiction (this fired on faithful claims in
the live run of 2026-09-27 before the rule — including once with the whole
one-line window quoted as "evidence").

Per node the citations aggregate: any `contradicts` → `contradicts`; else all
`yes` → `yes`; else `yes` beside only `no` → `yes` (a window that does not
mention the claim does not take away from one that states it); else any
`yes`/`partial` → `partial`; else any `no` → `no`; else `unverified`.
`contradicted: true` is set iff the verdict is `contradicts`.

`ENTAILMENT_PROMPT_VERSION` is part of the entailment cache key and of the
compile cache key (`routers/draft._prompt_key_material`, with the policy id), so
a warm compile is re-verified under the current rule rather than replaying
verdicts made under the old one.

### High-risk wording

Terms in `claim_policy.HIGH_RISK_TERMS` (*guaranteed, covered, coverage applies,
compliant, approved, entitled, liable, obligated, must pay, warranted,
certified, in full, all, always, never, violates, breaches, …*) found in the
claim whose stem does not occur in the verbatim evidence are flagged
`high_risk_wording:<term>`. A flag never changes the verdict; it routes the
document to review.

### Within-document consistency

The same *qualified* label — "annual premium", "collision deductible",
"liability limit" (the head noun with up to two qualifying words before it) —
stated with two different values in two different claims flags both
`inconsistent_figure:<label>` and lists them in `claim_summary.inconsistencies`.
Only identical qualified labels compare: "collision deductible $500" and
"comprehensive deductible $250" are two items. It is a flag, not a verdict
change: the source decides which value is right, and each claim's own verdict
already says whether the source carries it.

## Numeric recompute — what is covered

`services/numeric_recompute.recompute(claim, evidence, source_text)` is
deterministic; no model.

Figures recognised: money (`$1,250.00`, `USD 1,250`, `$5 million`), percentages
(`12%`, `12 %`, `12 percent`), plain numbers with separators, dates
(`MM/DD/YYYY` read as US month-first, `YYYY-MM-DD`, `January 15, 2025`,
`15 January 2025`). Normalised by value: `$1,250.00` == `1250`; `12%` == `12 %`.
Not figures: cross-references (`Section 4`, `Page 12`, `§ 3`), hyphenated or
alphanumeric identifiers (`POL-2025-00123`, a VIN), list markers. Word numbers
("twelve percent") are not recognised.

Presence: every figure in the claim must appear in the evidence window **or**
anywhere in the cited source's text. Otherwise `mismatch` / `missing_figure`.

Arithmetic recomputed, only when the operands are in the source:

| shape | example | check |
|---|---|---|
| sum | "X and Y total Z", "Z in total (X + Y)", "= Z" | operands + explicit operator in the **same sentence**; `total premium of $1,250` is a label, not an operator; listed amounts are never summed without one |
| share | "X of Y (Z%)", "Z% of Y is X" | Z within ±0.5 point of X/Y |
| difference | "increased by Z from X to Y", "from X to Y, an increase of Z" | Z absolute (exact) or percent (±0.5 pt); the stated direction must hold |
| date span | "from 01/15/2025 to 01/15/2026, 12 months" | days exact; months/years rounded to the nearest whole unit |
| labelled figure | claim "collision deductible $600", source "Collision Deductible: $500" | same qualified label, different value → `arithmetic` conflict |

A stated result that recomputes correctly from source operands is accounted
for even when the source does not print it. Tolerances: percentages ±0.5 point,
money exact to the cent.

## Counters, gate, summary

`provenance_stats` (`audit_summary._provenance_counts`) reports `eligible`,
`anchored`, `unanchored`, `verified`, `unsupported`, `contradicted`,
`insufficient`, `flagged`, plus `supported` (== `verified`, kept for older
readers), `partial` and `unverified` (the entailment layer's own detail).

`claim_summary = {total, verified, unsupported, contradicted, insufficient,
flagged, paragraphs, meta, policy, inconsistencies}` is written on
`document.meta`, in the `verified` SSE frame, and in
`projects.last_compiled_json.gate`. `total` and the verdict counts are per
sentence (claim units); `paragraphs` is the claim-bearing paragraphs behind
them; `meta` the paragraphs that are not claims. `provenance_stats` stays per
paragraph (plus `meta`).

Gate: Z3 `VIOLATION` → `blocked`; any contradicted / unsupported / insufficient
/ flagged claim, or an open Red-Hat finding → `review` (the reason names the
counts, contradicted first: "N claims contradicted by their source"); nothing
verified → `review`; otherwise `pass`. `ok` iff Z3 is not VIOLATION and every
eligible claim is VERIFIED with no flag (and there is at least one).

A cache replay (`_recount_cached_verified`) re-derives every claim block against
the ask's sources and recounts; without sources the persisted blocks are kept
and a node with none reads "source not supplied", never verified.

## What is not claimed

- Nothing is VERIFIED without a quote that is verbatim in a supplied source.
- `partial` is not verified. A missing qualifier is UNSUPPORTED.
- Model confidence appears nowhere in a claim block or a verdict.
- A page number is never defaulted; `null` means the source did not say.
- Calculations are recomputed from the source's figures. A total that checks
  against the draft's own figures but whose operands are not in the source is
  not recomputed — its figures are simply missing.
- Only the arithmetic shapes in the table are recognised. A calculation written
  another way is `not_applicable` and its figures are still required to be in
  the source; the recompute does not claim to catch it.
- The sub-claim check of an enumeration is presence-under-label, not
  entailment: it says each value is in the source next to its label, not that
  the paragraph's prose is a faithful reading of it. The paragraph's
  entailment verdict (`checks.entailment`) is reported beside it.
- The lexical matcher (`models/jdf.py`) still needs four shared tokens to anchor
  a paragraph; a short claim like "Flood is excluded." is anchored by the
  model's `[S<n>]` citation or not at all, and an unanchored claim is
  UNSUPPORTED.
- A source under 200 characters, or under 0.5 parse confidence, cannot verify
  anything — INSUFFICIENT_EVIDENCE names the measurement.
- Word numbers, ranges ("$500–$1,000"), and dates in `DD.MM.YYYY` are not
  recognised as figures.
