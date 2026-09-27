"""Parsure V1 field intelligence: classify, extract, validate, decide.

Everything in this module is a **heuristic over parsed text**. It exists so
the review backend has real, traceable field values to show — never guessed
ones. Spec: ``assure_parsure_v1_icp_spec.md`` §4 (quality-weighted
confidence), §5 (field_state vs routing_action, 3-rule policy), §6 (ugly
materials), §8 item 6 (no hallucination), §9 items 7–16.

Boundaries, so this does not become a second router or verifier (spec §8):

* Classification is a keyword count (``classify_document``). Its confidence is
  the share of a type's keywords matched, capped at 0.9 — a keyword heuristic
  cannot earn more than that, and the cap is stated here so nobody reads 0.9
  as a model probability. Before any type competes, ``document_family``
  reads the page's strong cues (auto / property / real_estate_transaction /
  medical) and only the named family's schemas may be chosen
  (``type_allowed``); the orchestrator applies the same gate to the evidence
  rule and the model suggestion, and a switch needs a *type-specific* field,
  not one every insurance form shares (``SHARED_FIELD_NAMES``). Customer
  case, 2026-09-26: a CMS-1500 medical claim became ``auto_policy`` on the
  keyword "accident" plus policy number / insured name / signature.
* Every field is anchored, found or not. A found field names the node its
  value sits on; an absent one names what was searched and the first layout
  node as ``anchor_node_id`` (``attach_absent_evidence``), so the provenance
  graph has no orphan. ``evidence_state`` (``EVIDENCE_STATES``) says which
  of five things happened; it never mixes with ``field_state`` / routing.
* Extraction is label-anchored regex per page (``extract_fields``). A field
  whose label or value is not found is ``value=None``,
  ``extraction_confidence=0.0``, ``review_required=True``,
  ``reason="field not found"``. Nothing is inferred from context. The
  fields this pass leaves empty may be offered to ``services/llm_extraction``
  (prose documents carry no labels); that pass only ever accepts a value it
  can re-find verbatim in the page text, and records the find through
  ``build_found_field(method="llm_grounded")`` so both passes yield the same
  record shape.
* ``coverage_plausibility`` is six arithmetic rules for auto insurance. It is
  *not* a second Z3: real Z3 hits come from ``verification["z3"]["violations"]``
  (services/verification.py) and are mapped onto fields by
  ``attach_z3_violations``. A failed plausibility rule is recorded as
  ``plausibility_violation`` with ``verification_source="plausibility_rule"``
  and the decision policy treats it like a Z3 violation; it is never reported
  *as* a Z3 violation (docs/anti-claims.md).
* Confidence comes only from the signals the pipeline actually produced:
  parser confidence, page quality, number readability, signature quality, Z3.
  The multiplication is written out in ``confidence_basis`` so a reviewer can
  check it. The quality assessors live in ``services/quality_probe.py``
  (another Phase A module); when it is not importable the conservative
  fallbacks below apply and the basis string says so.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import date
from typing import Any

# --------------------------------------------------------------------------
# Vocabularies (spec §5) — the two never mix.
# --------------------------------------------------------------------------

#: ``not_found`` (2026-09-27): the extractor read the pages and the field is not
#: there. Before this state an absent field was ``unverified`` / ``manual_review``
#: and a customer's review queue listed nine "review items" that were nine
#: absences — nothing for a person to look at. ``field_not_found`` is its routing:
#: recorded, counted, not queued. An absence on an *unreadable* page routes to
#: ``retry_parsure`` instead — a rescan, not a reviewer, is the next step.
FIELD_STATES = ("accepted", "partial", "unverified", "disputed", "rejected", "not_found")
ROUTING_ACTIONS = ("none", "manual_review", "adjudicator_queue", "compliance_review", "retry_parsure", "replay_later", "field_not_found")

#: Page quality under which a value read from the page is never auto-accepted
#: and a value that fails its shape check is dropped to ``found_suspect``
#: (customer run 2026-09-27: page quality 0.28, low DPI + blur, and the fields
#: still carried garbage as accepted values). Same scale as
#: ``quality_probe.page_quality_score``; 0.4 sits between ``UNREADABLE_MAX_QUALITY``
#: (0.3, the page says nothing) and ``READABLE_MIN_QUALITY`` (0.5).
LOW_QUALITY_PAGE = 0.4

#: Decision policy thresholds (spec §5, V1 rules 1–3).
VERIFICATION_THRESHOLD = 0.8
EXTRACTION_THRESHOLD = 0.75
#: Spec §9 item 10: when no verification check applies to a field.
DEFAULT_VERIFICATION_CONFIDENCE = 0.85
#: Spec §4: parser confidence defaults when the parser reported none.
PARSER_CONFIDENCE_DEFAULTS = {"jdf-cli": 0.85, "jdf": 0.85, "textract": 0.80, "jdf-cli+tesseract": 0.80}
OTHER_PARSER_CONFIDENCE_DEFAULT = 0.5
NEUTRAL_PAGE_QUALITY = 0.5
Z3_VIOLATION_PENALTY = 0.9
#: Heuristic classification cannot earn more than this.
CLASSIFICATION_CAP = 0.9

# --------------------------------------------------------------------------
# Document classification
# --------------------------------------------------------------------------

DOCUMENT_TYPES = (
    "auto_policy", "auto_claim", "auto_title",
    "property_policy", "property_claim",
    "deed", "mortgage", "title", "closing",
    "medical_claim",
)

#: The document family each type belongs to. A type may be chosen only when
#: its family agrees with the family the page's cues name (``document_family``)
#: or when no family dominates. Added 2026-09-26 after a customer's CMS-1500
#: medical claim was read as ``auto_policy``: the only keyword hit was
#: "accident", and the evidence rule then switched to the auto schema on the
#: strength of fields every insurance form shares (policy number, insured
#: name, signature).
TYPE_FAMILY: dict[str, str] = {
    "auto_policy": "auto", "auto_claim": "auto", "auto_title": "auto",
    "property_policy": "property", "property_claim": "property",
    "deed": "real_estate_transaction", "mortgage": "real_estate_transaction",
    "title": "real_estate_transaction", "closing": "real_estate_transaction",
    "medical_claim": "medical",
}
DOCUMENT_FAMILIES = ("auto", "property", "real_estate_transaction", "medical")

#: Keyword lists per ICP type. Matched case-insensitively as whole phrases.
#: The first few of each list are the discriminating phrases; the rest are
#: supporting vocabulary that appears across the type's documents.
TYPE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "auto_policy": (
        "auto policy", "automobile", "declarations", "vehicle", "vin", "liability",
        "collision", "comprehensive", "premium", "policy number", "named insured", "policy period",
    ),
    "auto_claim": (
        "claim number", "date of loss", "claimant", "adjuster", "vehicle", "vin",
        "accident", "damage", "loss description", "estimate", "collision", "repair",
    ),
    "auto_title": (
        "certificate of title", "title number", "odometer", "lienholder", "vin",
        "body type", "owner", "make", "model", "year", "title brand", "registered owner",
    ),
    "property_policy": (
        "homeowners", "dwelling", "property policy", "coverage a", "personal property",
        "premium", "deductible", "policy number", "insured location", "wind", "hail", "named insured",
        # Real-estate wording seen on lender-facing declarations (customer file
        # ``real_estate_policy_*.pdf``, 2026-09-26, typed auto_claim on
        # "claim" / "date of loss" / "vehicle" in its exclusions).
        "real estate", "property insurance", "dwelling coverage", "hazard insurance", "mortgagee",
    ),
    "property_claim": (
        "claim number", "date of loss", "property damage", "cause of loss", "dwelling",
        "adjuster", "claimant", "loss location", "roof", "estimate", "water damage", "repair",
    ),
    "deed": (
        "warranty deed", "quitclaim", "grantor", "grantee", "conveys", "legal description",
        "parcel", "recorded", "consideration", "witnesseth", "county", "hereby grants",
    ),
    "mortgage": (
        "mortgage", "borrower", "lender", "promissory note", "principal", "interest rate",
        "maturity date", "loan number", "security instrument", "escrow", "monthly payment", "lien",
    ),
    "title": (
        "title commitment", "title insurance", "schedule a", "schedule b", "exceptions",
        "proposed insured", "title company", "commitment number", "vesting", "requirements", "endorsement", "underwriter",
    ),
    "closing": (
        "closing disclosure", "settlement statement", "closing date", "cash to close", "loan costs",
        "seller", "buyer", "settlement agent", "prorations", "disbursement", "hud-1", "sale price",
    ),
    # CMS-1500 (NUCC 02/12) vocabulary; the same words are the medical
    # family's cues in ``FAMILY_CUES``.
    "medical_claim": (
        "cms-1500", "health insurance claim form", "patient", "insured's id number", "insured's i.d. number",
        "diagnosis", "icd", "cpt", "npi", "place of service", "total charge", "amount paid",
        "rendering provider", "medicare", "medicaid", "group health plan",
    ),
}

#: Strong cues per family — words that name the *kind* of document, not the
#: vocabulary two kinds share ("premium", "deductible", "claim", "accident" are
#: on auto, property and medical forms alike; CMS-1500 box 10b literally asks
#: "Auto Accident?"). Matched as whole phrases, case-insensitive.
FAMILY_CUES: dict[str, tuple[str, ...]] = {
    "auto": ("vin", "vehicle identification", "vehicle", "collision", "automobile", "odometer", "lienholder", "auto policy"),
    "property": ("dwelling", "coverage a", "homeowners", "hazard insurance", "personal property", "property insurance", "insured location"),
    "real_estate_transaction": (
        "deed", "grantor", "grantee", "mortgagee", "borrower", "lender", "promissory note", "closing disclosure",
        "settlement statement", "title commitment", "legal description", "parcel", "escrow",
    ),
    "medical": TYPE_KEYWORDS["medical_claim"],
}
#: A family is named when its cue count reaches this …
FAMILY_MIN_CUES = 2
#: … and leads the runner-up by at least this. A lender-facing property
#: declarations page whose exclusions mention "vehicle", "VIN" and "collision"
#: (customer file, 2026-09-26) scores auto 3 / property 2: no family dominates,
#: the gate stays open and the label pass decides as before.
FAMILY_MARGIN = 2

#: Fewer matched keywords than this and the heuristic will not name a type.
MIN_KEYWORD_MATCHES = 3


def _keyword_hits(text_lower: str, keywords: tuple[str, ...]) -> list[str]:
    hits: list[str] = []
    for kw in keywords:
        pattern = r"(?<![a-z0-9])" + re.escape(kw).replace(r"\ ", r"\s+") + r"(?![a-z0-9])"
        if re.search(pattern, text_lower):
            hits.append(kw)
    return hits


def document_family(text: str) -> dict[str, Any]:
    """``{"family", "cues", "counts", "basis"}`` — which kind of document the
    page's strong cues name, or ``"unknown"`` when none dominates.

    Counts ``FAMILY_CUES`` per family; the leader is named when it has at
    least ``FAMILY_MIN_CUES`` hits and leads the runner-up by
    ``FAMILY_MARGIN``. Anything else is ``unknown`` with the competing counts
    in ``basis``. This is a gate, not a classifier: it never names a type,
    it only says which schemas may be tried (``classify_document``,
    ``v1_orchestrator.reclassify_by_evidence``).
    """
    text_lower = (text or "").lower()
    if not text_lower.strip():
        return {"family": "unknown", "cues": [], "counts": {}, "basis": "no text"}
    hits = {fam: _keyword_hits(text_lower, cues) for fam, cues in FAMILY_CUES.items()}
    counts = {fam: len(h) for fam, h in hits.items()}
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], DOCUMENT_FAMILIES.index(kv[0])))
    (top, top_n), (second, second_n) = ranked[0], ranked[1]
    if top_n >= FAMILY_MIN_CUES and top_n - second_n >= FAMILY_MARGIN:
        others = ", ".join(f"{fam} {n}" for fam, n in ranked[1:] if n)
        return {
            "family": top, "cues": hits[top], "counts": counts,
            "basis": f"{top}: {top_n} cue{'s' if top_n != 1 else ''} ({', '.join(hits[top][:6])})" + (f"; next {others}" if others else "; no competing family"),
        }
    if top_n == 0:
        return {"family": "unknown", "cues": [], "counts": counts, "basis": "no family cue on the page"}
    competing = ", ".join(f"{fam} {n}" for fam, n in ranked if n)
    why = f"below {FAMILY_MIN_CUES} cues" if top_n < FAMILY_MIN_CUES else f"margin over {second} is {top_n - second_n} < {FAMILY_MARGIN}"
    return {"family": "unknown", "cues": hits[top], "counts": counts, "basis": f"no family dominates ({competing}; {why})"}


def type_allowed(document_type: Any, family: Any) -> bool:
    """The family gate: a type may be chosen when its family agrees with the
    detected family, or no family was detected. Types outside the taxonomy
    (``uncertain``, ``mixed_bundle``, ``<family>_unknown``) are never gated."""
    fam = str(family or "unknown")
    type_fam = TYPE_FAMILY.get(str(document_type or ""))
    return type_fam is None or fam == "unknown" or type_fam == fam


#: Two types whose keyword counts differ by at most this are a near-tie; the
#: label pass over both decides (``classify_document``).
NEAR_TIE_MARGIN = 1


def classify_document(text: str) -> dict[str, Any]:
    """Keyword-heuristic document type with an ``uncertain`` fallback.

    ``confidence`` is the share of the winning type's keywords that occur in
    the text, capped at ``CLASSIFICATION_CAP`` (0.9): a keyword count is not a
    calibrated probability and must never present as one. Fewer than
    ``MIN_KEYWORD_MATCHES`` hits is ``"uncertain"`` — the reviewer picks
    (classification override route).

    Near-tie rule (2026-09-26): when the top two types are within
    ``NEAR_TIE_MARGIN`` hits of each other — including an exact tie — the
    label pass (``count_found_fields``) runs for both and the type whose
    fields are actually found wins; the basis records both counts. Only when
    the found counts are equal too does an exact tie stay ``uncertain`` and a
    one-hit lead stand. Keywords alone mistyped a real-estate declarations
    page as ``auto_claim`` because its exclusions mention "claim", "date of
    loss" and "vehicle" (customer report, 2026-09-26); its fields are
    property-policy fields, and that is the evidence that counts.

    Family gate (2026-09-26, CMS-1500 read as ``auto_policy``): when
    ``document_family`` names a family, only that family's types compete; the
    answer carries ``family`` so the caller can validate the final type
    against it. A one-type family (medical) therefore needs its own keywords
    to reach ``MIN_KEYWORD_MATCHES``, or the answer is ``uncertain`` and the
    orchestrator's family fallback decides.
    """
    text_lower = (text or "").lower()
    if not text_lower.strip():
        return {"document_type": "uncertain", "confidence": 0.0, "basis": "no text to classify", "matched_keywords": [],
                "family": {"family": "unknown", "cues": [], "counts": {}, "basis": "no text"}}
    family = document_family(text)
    candidates = [t for t in DOCUMENT_TYPES if type_allowed(t, family["family"])]
    scored: list[tuple[str, list[str]]] = []
    for doc_type in candidates:
        hits = _keyword_hits(text_lower, TYPE_KEYWORDS[doc_type])
        scored.append((doc_type, hits))
    scored.sort(key=lambda item: len(item[1]), reverse=True)
    best_type, best_hits = scored[0]
    second_type, second_hits = scored[1] if len(scored) > 1 else (None, [])
    ratio = len(best_hits) / max(1, len(TYPE_KEYWORDS[best_type]))
    confidence = round(min(CLASSIFICATION_CAP, ratio), 3)
    gate_note = f"; family gate: {family['family']} ({family['basis']})" if family["family"] != "unknown" else ""
    if len(best_hits) < MIN_KEYWORD_MATCHES:
        return {
            "document_type": "uncertain",
            "confidence": confidence,
            "basis": f"only {len(best_hits)} keyword(s) matched for {best_type}; below minimum {MIN_KEYWORD_MATCHES}{gate_note}",
            "matched_keywords": best_hits,
            "family": family,
        }
    if second_type is None:
        return {
            "document_type": best_type,
            "confidence": confidence,
            "basis": f"keyword heuristic: {len(best_hits)}/{len(TYPE_KEYWORDS[best_type])} {best_type} keywords matched (only type of its family){gate_note}; capped at {CLASSIFICATION_CAP}",
            "matched_keywords": best_hits,
            "family": family,
        }
    if second_type is not None and len(best_hits) - len(second_hits) <= NEAR_TIE_MARGIN:
        best_found = count_found_fields(best_type, [text])
        second_found = count_found_fields(second_type, [text])
        if best_found != second_found:
            winner, loser = (best_type, second_type) if best_found > second_found else (second_type, best_type)
            winner_hits = best_hits if winner == best_type else second_hits
            winner_found, loser_found = max(best_found, second_found), min(best_found, second_found)
            return {
                "document_type": winner,
                "confidence": round(min(CLASSIFICATION_CAP, len(winner_hits) / max(1, len(TYPE_KEYWORDS[winner]))), 3),
                "basis": (
                    f"keyword near-tie ({best_type} {len(best_hits)}, {second_type} {len(second_hits)}) decided by the label pass: "
                    f"{winner} {winner_found}/{len(FIELD_TAXONOMY[winner])} fields found vs {loser} {loser_found}/{len(FIELD_TAXONOMY[loser])}; "
                    f"capped at {CLASSIFICATION_CAP}{gate_note}"
                ),
                "matched_keywords": winner_hits,
                "family": family,
            }
    if len(second_hits) == len(best_hits):
        return {
            "document_type": "uncertain",
            "confidence": confidence,
            "basis": f"tie between {best_type} and {scored[1][0]} ({len(best_hits)} keywords each; label pass found the same number of fields for both){gate_note}",
            "matched_keywords": best_hits,
            "family": family,
        }
    return {
        "document_type": best_type,
        "confidence": confidence,
        "basis": (
            f"keyword heuristic: {len(best_hits)}/{len(TYPE_KEYWORDS[best_type])} {best_type} keywords matched "
            f"(next: {scored[1][0]} {len(second_hits)}){gate_note}; capped at {CLASSIFICATION_CAP}"
        ),
        "matched_keywords": best_hits,
        "family": family,
    }


# --------------------------------------------------------------------------
# Field taxonomy
# --------------------------------------------------------------------------

#: ``codes``: a list of ICD-10 / CPT / HCPCS codes as written (CMS-1500 boxes 21 and 24d).
FIELD_TYPES = ("text", "number", "money", "date", "vin", "name", "signature", "codes")


@dataclass(frozen=True)
class FieldSpec:
    """One extractable field: how it is labelled and how strictly it is treated.

    ``anchors`` are label regexes (case-insensitive) that precede the value;
    ``compliance_bound`` fields never auto-accept (spec §5 rule 2).
    """

    name: str
    label: str
    field_type: str
    anchors: tuple[str, ...]
    compliance_bound: bool = False
    aliases: tuple[str, ...] = dc_field(default_factory=tuple)


def _f(name: str, label: str, field_type: str, anchors: tuple[str, ...], compliance_bound: bool = False) -> FieldSpec:
    return FieldSpec(name=name, label=label, field_type=field_type, anchors=anchors, compliance_bound=compliance_bound)


_POLICY_NUMBER = _f("policy_number", "Policy number", "text", (r"policy\s*(?:no\.?|number|num\.?|#)",), True)
_INSURED_NAME = _f("insured_name", "Insured name", "name", (r"(?:named\s+)?insured(?:\s+name)?(?!\s*(?:location|address|property|vehicle|party|signature))", r"insured\s*party"))
_EFFECTIVE = _f("effective_date", "Effective date", "date", (r"effective(?:\s+date)?", r"policy\s+period\s*(?:from)?", r"inception\s+date"))
_EXPIRATION = _f("expiration_date", "Expiration date", "date", (r"expir(?:ation|es|y)(?:\s+date)?", r"(?:policy\s+period\s+)?(?:to|through)"))
_VIN = _f("vin", "VIN", "vin", (r"vin", r"vehicle\s+identification\s+(?:no\.?|number)"), True)
_VEHICLE = _f("vehicle_year_make_model", "Vehicle (year make model)", "text", (r"vehicle(?:\s+description)?", r"year\s*/\s*make\s*/\s*model", r"year,?\s+make,?\s+(?:and\s+)?model"))
_PREMIUM = _f("premium", "Premium", "money", (r"(?:total\s+|annual\s+|policy\s+)?premium",), True)
_AGENT = _f("agent_name", "Agent", "name", (r"agent(?:\s+name)?", r"producer"))
_SIGNATURE = _f("signature", "Signature", "signature", (r"(?:authorized\s+|insured'?s?\s+|applicant'?s?\s+|borrower'?s?\s+|grantor'?s?\s+|buyer'?s?\s+|claimant'?s?\s+|owner'?s?\s+)?signature", r"signed\s+by"), True)
_CLAIM_NUMBER = _f("claim_number", "Claim number", "text", (r"claim\s*(?:no\.?|number|#)",))
_CLAIMANT = _f("claimant_name", "Claimant", "name", (r"claimant(?:\s+name)?",))
_DATE_OF_LOSS = _f("date_of_loss", "Date of loss", "date", (r"date\s+of\s+loss", r"loss\s+date"))
_ADJUSTER = _f("adjuster_name", "Adjuster", "name", (r"adjuster(?:\s+name)?",))
_EST_DAMAGE = _f("estimated_damage", "Estimated damage", "money", (r"estimated\s+(?:damage|repair\s+cost|loss)", r"damage\s+estimate", r"repair\s+estimate"))
_PROPERTY_ADDRESS = _f("property_address", "Property address", "text", (r"property\s+address", r"insured\s+location", r"premises", r"property\s+located\s+at"))
#: CMS-1500 writes "INSURED'S NAME"; the shared insured-name anchor stops at the
#: apostrophe (so "Insured's Signature" is not a name), hence a form-specific spec
#: under the same field name — it stays a *shared* field for the family gate.
_MEDICAL_INSURED_NAME = _f("insured_name", "Insured name", "name", (r"insured'?s?\s+name", r"(?:named\s+)?insured(?:\s+name)?(?!\s*(?:location|address|property|vehicle|party|signature|i\.?d))"))

FIELD_TAXONOMY: dict[str, list[FieldSpec]] = {
    "auto_policy": [
        _POLICY_NUMBER, _INSURED_NAME, _EFFECTIVE, _EXPIRATION, _VIN, _VEHICLE, _PREMIUM,
        _f("liability_limit", "Liability limit", "money", (r"(?:bodily\s+injury\s+)?liability(?:\s+limit|\s+coverage)?",), True),
        _f("collision_deductible", "Collision deductible", "money", (r"collision(?:\s+deductible)?",)),
        _f("comprehensive_deductible", "Comprehensive deductible", "money", (r"comprehensive(?:\s+deductible)?",)),
        _AGENT, _SIGNATURE,
    ],
    "auto_claim": [
        _CLAIM_NUMBER, _POLICY_NUMBER, _CLAIMANT, _DATE_OF_LOSS, _VIN, _VEHICLE,
        _f("loss_description", "Loss description", "text", (r"loss\s+description", r"description\s+of\s+(?:loss|accident)")),
        _EST_DAMAGE, _ADJUSTER, _SIGNATURE,
    ],
    "auto_title": [
        _f("title_number", "Title number", "text", (r"title\s*(?:no\.?|number|#)",)),
        _VIN,
        _f("owner_name", "Owner", "name", (r"(?:registered\s+)?owner(?:\s+name)?",)),
        _VEHICLE,
        _f("odometer", "Odometer", "number", (r"odometer(?:\s+reading)?", r"mileage")),
        _f("lienholder", "Lienholder", "name", (r"lien\s*holder", r"first\s+lien")),
        _f("issue_date", "Issue date", "date", (r"(?:date\s+)?issued", r"issue\s+date")),
        _SIGNATURE,
    ],
    "property_policy": [
        _POLICY_NUMBER, _INSURED_NAME, _PROPERTY_ADDRESS, _EFFECTIVE, _EXPIRATION,
        _f("dwelling_coverage", "Dwelling coverage", "money", (r"(?:coverage\s+a\s*[-–]?\s*)?dwelling(?:\s+coverage|\s+limit)?",), True),
        _f("personal_property_coverage", "Personal property coverage", "money", (r"(?:coverage\s+c\s*[-–]?\s*)?personal\s+property(?:\s+coverage|\s+limit)?",), True),
        _PREMIUM,
        _f("deductible", "Deductible", "money", (r"(?:all\s+peril\s+|wind\s*/\s*hail\s+)?deductible",)),
        _AGENT, _SIGNATURE,
    ],
    "property_claim": [
        _CLAIM_NUMBER, _POLICY_NUMBER, _CLAIMANT, _DATE_OF_LOSS, _PROPERTY_ADDRESS,
        _f("cause_of_loss", "Cause of loss", "text", (r"cause\s+of\s+loss", r"peril")),
        _EST_DAMAGE, _ADJUSTER, _SIGNATURE,
    ],
    "deed": [
        _f("grantor", "Grantor", "name", (r"grantor(?:\(s\))?(?:\s+name)?",)),
        _f("grantee", "Grantee", "name", (r"grantee(?:\(s\))?(?:\s+name)?",)),
        _PROPERTY_ADDRESS,
        _f("legal_description", "Legal description", "text", (r"legal\s+description",)),
        _f("consideration", "Consideration", "money", (r"(?:for\s+(?:the\s+)?)?consideration(?:\s+of)?",)),
        _f("recording_date", "Recording date", "date", (r"record(?:ed|ing)(?:\s+date|\s+on)?",)),
        _f("parcel_number", "Parcel number", "text", (r"parcel\s*(?:id|no\.?|number|#)", r"apn", r"tax\s+id")),
        _f("county", "County", "text", (r"county(?:\s+of|\s*:)",)),
        _SIGNATURE,
    ],
    "mortgage": [
        _f("borrower_name", "Borrower", "name", (r"borrower(?:\(s\))?(?:\s+name)?",)),
        _f("lender_name", "Lender", "name", (r"lender(?:\s+name)?", r"mortgagee")),
        _f("loan_number", "Loan number", "text", (r"loan\s*(?:no\.?|number|#)",)),
        _f("principal_amount", "Principal amount", "money", (r"principal(?:\s+amount|\s+sum)?", r"loan\s+amount")),
        _f("interest_rate", "Interest rate", "number", (r"interest\s+rate", r"note\s+rate")),
        _f("maturity_date", "Maturity date", "date", (r"maturity(?:\s+date)?",)),
        _PROPERTY_ADDRESS,
        _f("execution_date", "Execution date", "date", (r"(?:date\s+of\s+)?execution", r"dated", r"executed\s+on")),
        _SIGNATURE,
    ],
    "title": [
        _f("commitment_number", "Commitment number", "text", (r"commitment\s*(?:no\.?|number|#)", r"file\s*(?:no\.?|number|#)")),
        _f("proposed_insured", "Proposed insured", "name", (r"proposed\s+insured",)),
        _PROPERTY_ADDRESS,
        _f("policy_amount", "Policy amount", "money", (r"(?:proposed\s+)?policy\s+amount", r"amount\s+of\s+insurance"), True),
        _EFFECTIVE,
        _f("title_company", "Title company", "name", (r"title\s+company", r"underwriter", r"issued\s+by")),
        _f("vesting", "Vesting", "text", (r"vesting", r"title\s+(?:is\s+)?vested\s+in")),
        _SIGNATURE,
    ],
    "closing": [
        _f("closing_date", "Closing date", "date", (r"closing\s+date", r"settlement\s+date")),
        _f("buyer_name", "Buyer", "name", (r"buyer(?:\s+name)?", r"purchaser")),
        _f("seller_name", "Seller", "name", (r"seller(?:\s+name)?",)),
        _PROPERTY_ADDRESS,
        _f("sale_price", "Sale price", "money", (r"sale\s+price", r"purchase\s+price", r"contract\s+price")),
        _f("loan_amount", "Loan amount", "money", (r"loan\s+amount",)),
        _f("cash_to_close", "Cash to close", "money", (r"cash\s+to\s+close",)),
        _f("settlement_agent", "Settlement agent", "name", (r"settlement\s+agent", r"closing\s+agent")),
        _SIGNATURE,
    ],
    # CMS-1500 (NUCC 02/12) box numbers in the labels' order: 2, 1a, 3, 4, 21,
    # 24d, 24a, 33/32, 24j/33a, 25, 28, 29, 31.
    "medical_claim": [
        _f("patient_name", "Patient name", "name", (r"patient'?s?\s+name", r"name\s+of\s+patient")),
        _f("insured_id", "Insured's ID number", "text", (r"insured'?s?\s+i\.?d\.?\s*(?:no\.?|number|#)?", r"(?:member|subscriber)\s+i\.?d\.?(?:\s*(?:no\.?|number|#))?", r"medicare\s+(?:no\.?|number|i\.?d\.?)"), True),
        _f("patient_dob", "Patient date of birth", "date", (r"patient'?s?\s+(?:birth\s+date|date\s+of\s+birth|dob)", r"date\s+of\s+birth", r"birth\s+date", r"dob")),
        _MEDICAL_INSURED_NAME,
        _f("diagnosis_codes", "Diagnosis codes (ICD)", "codes", (
            r"diagnosis(?:\s+or\s+nature\s+of\s+illness(?:\s+or\s+injury)?)?(?:\s*\((?:icd|dx)[^)]*\))?(?:\s+codes?)?",
            r"icd(?:-?\s*(?:9|10)(?:-cm)?)?(?:\s+codes?)?", r"dx(?:\s+codes?)?",
        )),
        _f("procedure_codes", "Procedure codes (CPT/HCPCS)", "codes", (
            r"procedures?(?:,\s*services,?\s*(?:or\s+)?supplies)?(?:\s*\((?:cpt|hcpcs)[^)]*\))?(?:\s+codes?)?",
            r"(?:cpt|hcpcs)(?:\s*/\s*hcpcs)?(?:\s+codes?)?",
        )),
        _f("date_of_service", "Date of service", "date", (r"dates?(?:\(s\))?\s+of\s+service(?:\s+from)?", r"service\s+dates?(?:\s+from)?", r"dos")),
        _f("provider_name", "Provider", "name", (
            r"(?:rendering|billing)\s+provider(?:\s+(?:name|info(?:rmation)?))?(?:\s*&\s*ph\.?\s*#?)?", r"provider\s+name",
            r"service\s+facility(?:\s+(?:name|location(?:\s+information)?))?", r"physician(?:\s+name)?",
        )),
        _f("provider_npi", "Provider NPI", "text", (r"(?:rendering\s+|billing\s+)?(?:provider\s+)?npi(?:\s*(?:no\.?|number|#))?",), True),
        _f("federal_tax_id", "Federal tax ID", "text", (r"federal\s+tax\s+i\.?d\.?(?:\s*(?:no\.?|number|#))?", r"tax\s+i\.?d\.?(?:\s*(?:no\.?|number|#))?", r"(?:ein|tin)(?:\s*(?:no\.?|number|#))?"), True),
        _f("total_charge", "Total charge", "money", (r"total\s+charges?",), True),
        _f("amount_paid", "Amount paid", "money", (r"amount\s+paid",)),
        _SIGNATURE,
    ],
}

#: Fields whose presence says nothing about the document's kind: every
#: insurance form carries a policy number, an insured, dates and a signature.
#: The evidence rule and the model confirmation require at least one field
#: *outside* this set before a type may be chosen — the customer's CMS-1500
#: (2026-09-26) was switched to ``auto_policy`` by exactly these.
SHARED_FIELD_NAMES = frozenset({"policy_number", "insured_name", "signature", "effective_date", "expiration_date"})

# --------------------------------------------------------------------------
# Extraction evidence — cheap label-pass counts used to check a classification
# --------------------------------------------------------------------------

def count_found_fields(document_type: str, page_texts_in: list[str]) -> int:
    """How many of ``document_type``'s non-signature fields the label pass finds
    in ``page_texts_in`` — the regex pass only, no confidence, no model.

    This is the evidence ``classify_document`` (near-tie rule) and
    ``v1_orchestrator.reclassify_by_evidence`` weigh against the keyword count:
    a type whose fields are on the page is a better answer than a type whose
    vocabulary merely appears in it. Signature is excluded because its
    presence is a quality-probe question, not a label match. Costs one
    ``_find_field`` per field per page (about 10 × pages regex searches per
    type); on a 12-page policy all nine types take under 50 ms.
    """
    specs = [s for s in (FIELD_TAXONOMY.get(document_type) or []) if s.field_type != "signature"]
    if not specs:
        return 0
    texts = [t or "" for t in (page_texts_in or [])]
    found = 0
    for spec in specs:
        if any(_find_field(spec, text) for text in texts if text):
            found += 1
    return found


def found_field_names(document_type: str, page_texts_in: list[str]) -> list[str]:
    """The names ``count_found_fields`` counts, in taxonomy order (signature excluded)."""
    specs = [s for s in (FIELD_TAXONOMY.get(document_type) or []) if s.field_type != "signature"]
    texts = [t or "" for t in (page_texts_in or [])]
    return [spec.name for spec in specs if any(_find_field(spec, text) for text in texts if text)]


def type_specific_found(document_type: str, page_texts_in: list[str]) -> list[str]:
    """Found fields of ``document_type`` that are not in ``SHARED_FIELD_NAMES`` —
    the evidence that this schema, and not any insurance form, is on the page."""
    return [n for n in found_field_names(document_type, page_texts_in) if n not in SHARED_FIELD_NAMES]


def found_field_counts(page_texts_in: list[str]) -> dict[str, int]:
    """``count_found_fields`` for every type in ``FIELD_TAXONOMY``, in taxonomy order."""
    return {doc_type: count_found_fields(doc_type, page_texts_in) for doc_type in FIELD_TAXONOMY}


def field_needs_review(field: dict[str, Any]) -> bool:
    """The one rule for "this field still asks for a person": its routing is
    not ``none``, or its state is ``disputed`` / ``rejected``.

    Every count a reviewer sees — the review queue and its ``counts``, the
    "Needs attention" header, ``review_summary.fields_review`` (hence the
    analytics column), the Extracted-data count line, the record page's "Need
    review" and the card's status line — goes through this function so the
    same document yields the same number everywhere. Added 2026-09-26 after a
    customer read "Need attention: 3" next to "62 items" as a broken count:
    one figure counted documents, the other fields, by two different rules.
    """
    if field.get("evidence_state") == "schema_mismatch":
        # The document is wrong for these fields, not the fields for the
        # document: the report asks for a person once (``classification.
        # schema_mismatch``), never thirteen times.
        return False
    routing = str(field.get("routing_action") or "none").lower()
    # ``field_not_found`` (2026-09-27): an absence is recorded, not queued — a
    # reviewer cannot act on a value that is not there. An absence on an
    # unreadable page routes ``retry_parsure`` and does count: the next step
    # (a rescan) is a person's decision.
    return routing not in ("none", "field_not_found") or field.get("field_state") in ("disputed", "rejected")


# --------------------------------------------------------------------------
# Value parsing
# --------------------------------------------------------------------------

_SEP = r"[ \t]*[:#\-–—]?[ \t]*\n?[ \t]*"
_MONEY_RE = r"(?:USD\s*|\$\s*)?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?"
_NUMBER_RE = r"([-+]?\d[\d,]*(?:\.\d+)?)\s*(%|miles|mi\.?)?"
_DATE_RE = (
    r"(\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}"
    r"|\d{4}-\d{2}-\d{2}"
    r"|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}"
    r"|\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?,?\s+\d{4})"
)
_VIN_RE = r"([A-Za-z0-9]{17})"
_TEXT_RE = r"([^\n]{1,160})"
#: One ICD-10-CM code (letter, two digits, optional .1–4 alphanumerics: S13.4XXA)
#: or one CPT/HCPCS code (five digits, optional -modifier: 99213-25), each
#: optionally led by the CMS-1500 pointer letter ("A. S13.4XXA").
_CODE_ITEM = r"(?:[A-L]\.\s*)?(?:[A-Z]\d{2}(?:\.[0-9A-Z]{1,4})?|\d{5}(?:-[0-9A-Z]{2})?)"
_CODES_RE = r"(" + _CODE_ITEM + r"(?:[ \t,;]+" + _CODE_ITEM + r")*)"
_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}


def _parse_date(raw: str) -> str | None:
    """ISO ``YYYY-MM-DD`` from the common US/ISO/long forms; None when ambiguous."""
    s = raw.strip().rstrip(".,")
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        y, mo, d = (int(x) for x in m.groups())
    else:
        m = re.fullmatch(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})", s)
        if m:
            mo, d, y = (int(x) for x in m.groups())
            if y < 100:
                y += 2000 if y < 70 else 1900
        else:
            m = re.fullmatch(r"([a-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})", s, re.I)
            if m:
                mon, d, y = m.group(1)[:3].lower(), int(m.group(2)), int(m.group(3))
                mo = _MONTHS.get(mon, 0)
            else:
                m = re.fullmatch(r"(\d{1,2})\s+([a-z]+)\.?,?\s+(\d{4})", s, re.I)
                if not m:
                    return None
                d, mon, y = int(m.group(1)), m.group(2)[:3].lower(), int(m.group(3))
                mo = _MONTHS.get(mon, 0)
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


def _parse_money(raw: str) -> float | None:
    m = re.search(_MONEY_RE, raw)
    if not m:
        return None
    whole = m.group(1).replace(",", "")
    cents = m.group(2) or "0"
    try:
        return float(f"{whole}.{cents}")
    except ValueError:
        return None


def _parse_number(raw: str) -> float | None:
    m = re.search(r"[-+]?\d[\d,]*(?:\.\d+)?", raw)
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _parse_codes(raw: str) -> list[str] | None:
    """The code tokens of a ``codes`` capture, pointer letters dropped, upper-cased."""
    codes = [re.sub(r"^[A-La-l]\.\s*", "", tok).upper() for tok in re.split(r"[ \t,;]+", raw.strip()) if tok.strip()]
    codes = [c for c in codes if re.fullmatch(r"[A-Z]\d{2}(?:\.[0-9A-Z]{1,4})?|\d{5}(?:-[0-9A-Z]{2})?", c)]
    return codes or None


def _clean_text_value(raw: str) -> str:
    """Trim a rest-of-line capture at the next inline label (two+ spaces, a tab,
    or a pipe). A sentence-final period is dropped when the last word is four
    or more letters ("County of Plymouth." → "Plymouth"; "Inc." and "Jr."
    keep theirs) — prose golden set, 2026-09-25."""
    value = re.split(r"\s{2,}|\t|\s\|\s", raw.strip(), maxsplit=1)[0]
    value = re.sub(r"\s+", " ", value).strip(" :;,-–—")
    value = re.sub(r"(?<=[A-Za-z]{4})\.$", "", value)
    return value


# --------------------------------------------------------------------------
# VIN (ISO 3779)
# --------------------------------------------------------------------------

_VIN_TRANSLITERATION = {
    **{str(d): d for d in range(10)},
    "A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "G": 7, "H": 8,
    "J": 1, "K": 2, "L": 3, "M": 4, "N": 5, "P": 7, "R": 9,
    "S": 2, "T": 3, "U": 4, "V": 5, "W": 6, "X": 7, "Y": 8, "Z": 9,
}
_VIN_WEIGHTS = (8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2)
_VIN_YEAR_CODES = "ABCDEFGHJKLMNPRSTVWXY123456789"


def validate_vin(vin: str | None) -> dict[str, Any]:
    """ISO 3779 / 49 CFR 565 check: 17 chars, no I/O/Q, position-9 check digit.

    The check digit is weighted-sum mod 11 over the transliterated characters
    ('X' for 10). A VIN failing it is *rejected*, never "corrected" — a guessed
    VIN is exactly the hallucination spec §8.6 forbids.
    """
    if not vin:
        return {"valid": False, "reason": "VIN missing"}
    v = str(vin).strip().upper()
    if len(v) != 17:
        return {"valid": False, "reason": f"VIN must be 17 characters (got {len(v)})"}
    if re.search(r"[IOQ]", v):
        return {"valid": False, "reason": "VIN contains I, O or Q (not allowed by ISO 3779)"}
    if not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", v):
        return {"valid": False, "reason": "VIN contains characters outside A-Z/0-9"}
    total = sum(_VIN_TRANSLITERATION[ch] * w for ch, w in zip(v, _VIN_WEIGHTS))
    remainder = total % 11
    expected = "X" if remainder == 10 else str(remainder)
    if v[8] != expected:
        return {"valid": False, "reason": f"VIN check digit mismatch (position 9 is {v[8]}, computed {expected})"}
    return {"valid": True, "reason": "17 characters, allowed alphabet, check digit verified"}


def vin_model_years(vin: str) -> list[int]:
    """Model years the 10th VIN character can encode (the code repeats every 30 years)."""
    v = str(vin).strip().upper()
    if len(v) != 17 or v[9] not in _VIN_YEAR_CODES:
        return []
    idx = _VIN_YEAR_CODES.index(v[9])
    return [1980 + idx, 2010 + idx, 2040 + idx]


# --------------------------------------------------------------------------
# Quality assessors — real module when present, conservative fallbacks otherwise
# --------------------------------------------------------------------------

#: Spec §6 "Unreadable Numbers" penalties; overridden by quality_probe when present.
NUMBER_PENALTIES = {
    "clear": 1.0, "printed_good": 1.0, "handwritten": 0.7, "faded": 0.6, "typewritten_low_quality": 0.8,
    "unreadable": 0.0, "unknown": 1.0, "invalid_format": 0.0,
}
#: Spec §6 "Faint Signatures": text-only signals cannot see ink, so every
#: quality other than a clear one carries a penalty and forces review.
#: Vocabulary since 2026-09-27 (customer: "questionable" told the reviewer
#: nothing): ``present_clear`` (an explicit e-signature marker), ``present_ambiguous``
#: (a mark exists, ink cannot confirm a handwritten signature), ``missing``,
#: ``stamp`` (stamp/seal wording — not a signature, not collapsed into one),
#: ``printed_name`` (a typed name on the signature line), ``unreadable`` (the
#: page cannot be judged). The older words stay in the table so reports saved
#: before the change still score.
SIGNATURE_PENALTIES = {
    "clear": 1.0, "present_clear": 1.0, "faint": 0.8, "incomplete": 0.7, "stamped": 0.7, "stamp": 0.7,
    "questionable": 0.6, "present_ambiguous": 0.6, "printed_name": 0.6, "missing": 0.5, "unreadable": 0.4, "unknown": 1.0,
}
SIGNATURE_QUALITIES = ("present_clear", "present_ambiguous", "missing", "stamp", "printed_name", "unreadable", "unknown")
#: What the reviewer looks at next, per verdict — the verdict alone was "too
#: vague" (customer, 2026-09-27); every ``signature_quality`` carries one.
SIGNATURE_NEXT_CHECK = {
    "present_clear": "No check needed: an explicit electronic-signature marker is on the page.",
    "present_ambiguous": "Open the page image and confirm the mark is a handwritten signature, not a stray mark or print.",
    "missing": "Confirm the signature line is blank on the original; if so, request a signed copy.",
    "stamp": "A stamp or seal sits at the signature line; confirm whether it is acceptable in place of a handwritten signature.",
    "printed_name": "A typed name sits on the signature line; confirm whether a handwritten signature is required.",
    "unreadable": "The page is not readable enough to judge the signature; view the original at full resolution.",
    "unknown": "No signature label was found; check whether this document requires a signature at all.",
    # legacy verdicts from reports saved before 2026-09-27
    "clear": "No check needed.", "faint": "Open the page image and confirm the faint mark is a signature.",
    "incomplete": "Open the page image; the mark looks incomplete.", "stamped": "Confirm whether a stamp is acceptable here.",
    "questionable": "Open the page image and confirm the mark is a handwritten signature.",
}


def with_next_check(sig: dict[str, Any]) -> dict[str, Any]:
    """Attach ``next_check`` to a signature verdict (quality_probe's or the fallback's)."""
    if isinstance(sig, dict) and not sig.get("next_check"):
        sig["next_check"] = SIGNATURE_NEXT_CHECK.get(str(sig.get("quality") or "unknown"), SIGNATURE_NEXT_CHECK["unknown"])
    return sig


def _quality_probe():
    try:
        from . import quality_probe  # type: ignore
        return quality_probe
    except ImportError:
        try:
            import quality_probe  # type: ignore
            return quality_probe
        except ImportError:
            return None


def assess_number(value_text: str, *, ocr_confidence: float | None, page_quality: float | None, handwritten: bool = False) -> dict[str, Any]:
    """Number readability from the signals at hand (spec §6 "Unreadable Numbers").

    Delegates to ``quality_probe.assess_number`` when that module exists. The
    fallback reads only OCR confidence and page quality: a text-layer parse
    (no OCR) with no page-quality signal is ``printed_good`` because the digits
    came from the PDF's own text, not from recognition.
    """
    qp = _quality_probe()
    if qp is not None and hasattr(qp, "assess_number"):
        try:
            return dict(qp.assess_number(value_text, ocr_confidence=ocr_confidence, page_quality=page_quality, handwritten=handwritten))
        except Exception:
            pass
    if handwritten:
        quality, basis = "handwritten", "handwriting detected"
    elif ocr_confidence is not None and ocr_confidence < 0.6:
        quality, basis = "faded", f"ocr_confidence {ocr_confidence:.2f} < 0.60"
    elif page_quality is not None and page_quality < 0.5:
        quality, basis = "typewritten_low_quality", f"page_quality {page_quality:.2f} < 0.50"
    elif ocr_confidence is None:
        quality, basis = "printed_good", "text-layer digits (no OCR pass)"
    else:
        quality, basis = "clear", f"ocr_confidence {ocr_confidence:.2f}"
    penalty = NUMBER_PENALTIES[quality]
    return {"quality": quality, "penalty": penalty, "review_required": penalty < 1.0, "basis": basis}


_SIG_LABEL_RE = re.compile(
    r"(?:authorized\s+|insured'?s?\s+|applicant'?s?\s+|borrower'?s?\s+|grantor'?s?\s+|buyer'?s?\s+|claimant'?s?\s+|owner'?s?\s+)?"
    r"signature|signed\s+by|/s/",
    re.I,
)


def assess_signature(page_text: str, *, visual: dict | None = None, ocr_lines: list | None = None, page: int | None = None) -> dict[str, Any]:
    """Signature presence and quality for one page (spec §6 "Faint Signatures").

    Delegates to ``quality_probe.assess_signature`` when present. The fallback
    is text-only: it can tell a label followed by an e-signature marker
    (``/s/``, "electronically signed"), a stamp, or an empty signature line —
    it cannot see ink density, so a signature it cannot prove is
    ``questionable`` with ``review_required=True``, never ``clear``.
    """
    qp = _quality_probe()
    if qp is not None and hasattr(qp, "assess_signature"):
        try:
            return with_next_check(dict(qp.assess_signature(page_text, visual=visual, ocr_lines=ocr_lines, page=page)))
        except Exception:
            pass
    text = page_text or ""
    m = _SIG_LABEL_RE.search(text)
    if not m:
        return with_next_check({"present": False, "quality": "missing", "review_required": True, "basis": "no signature label on page", "page": page})
    tail = text[m.end(): m.end() + 80]
    tail_line = tail.split("\n", 1)[0]
    after = re.sub(r"^[\s:\-–—]+", "", tail_line)
    lower_tail = tail.lower()
    if re.search(r"stamp|seal", lower_tail):
        return with_next_check({"present": True, "quality": "stamp", "review_required": True, "basis": "stamp/seal wording after signature label", "page": page})
    if re.search(r"/s/|electronically\s+signed|e-?signed|digitally\s+signed|docusign", text[max(0, m.start() - 40): m.end() + 80], re.I):
        return with_next_check({"present": True, "quality": "present_clear", "review_required": False, "basis": "explicit e-signature marker (/s/ or electronically signed)", "page": page})
    if not after or re.fullmatch(r"[_\s.]*", after):
        return with_next_check({"present": False, "quality": "missing", "review_required": True, "basis": "signature line is blank", "page": page})
    if re.fullmatch(r"[A-Za-z.'\- ]{2,}", after.strip()) and len(after.strip().split()) >= 2:
        return with_next_check({"present": True, "quality": "printed_name", "review_required": True, "basis": "typed name after the signature label; no ink measurement from text", "page": page})
    return with_next_check({"present": True, "quality": "present_ambiguous", "review_required": True, "basis": "text after signature label; ink quality not assessable from text", "page": page})


def _penalties():
    qp = _quality_probe()
    number = getattr(qp, "NUMBER_PENALTIES", None) if qp else None
    signature = getattr(qp, "SIGNATURE_PENALTIES", None) if qp else None
    return (number if isinstance(number, dict) else NUMBER_PENALTIES), (signature if isinstance(signature, dict) else SIGNATURE_PENALTIES)


# --------------------------------------------------------------------------
# Quality-weighted confidence (spec §4)
# --------------------------------------------------------------------------

def parser_confidence_default(parser_name: str | None) -> float:
    return PARSER_CONFIDENCE_DEFAULTS.get(str(parser_name or "").lower(), OTHER_PARSER_CONFIDENCE_DEFAULT)


def quality_weighted_confidence(
    *,
    parser_confidence: float | None,
    parser_name: str | None,
    page_quality: float | None,
    number_quality: str | None = None,
    z3_violation: bool = False,
    signature_quality: str | None = None,
    quality_label: str = "page_quality",
) -> tuple[float, str]:
    """``extraction_confidence`` and the ``confidence_basis`` that spells it out.

    Delegates to ``quality_probe.quality_weighted_confidence`` when present;
    otherwise the spec §4 product with the spec's defaults. When nothing but
    defaults would enter the product, the answer is the flat 0.50 with the
    "no_signal_available" basis — a default multiplied by a default is not a
    measurement. ``quality_label`` names the quality factor in the basis:
    ``page_quality`` (the page-global score) or ``local_ocr`` (the OCR
    confidence of the line the value sits on — spec §4 formula unchanged,
    the factor is just the nearer measurement; added 2026-09-26).
    """
    qp = _quality_probe()
    if qp is not None and hasattr(qp, "quality_weighted_confidence"):
        try:
            value, basis = qp.quality_weighted_confidence(
                parser_confidence=parser_confidence, parser_name=parser_name, page_quality=page_quality,
                number_quality=number_quality, z3_violation=z3_violation, signature_quality=signature_quality,
                quality_label=quality_label,
            )
            return float(value), str(basis)
        except Exception:
            pass
    number_penalties, signature_penalties = _penalties()
    known_parser = str(parser_name or "").lower() in PARSER_CONFIDENCE_DEFAULTS
    has_signal = (
        parser_confidence is not None or page_quality is not None or known_parser
        or (number_quality and number_penalties.get(number_quality, 1.0) < 1.0)
        or z3_violation or (signature_quality and signature_penalties.get(signature_quality, 1.0) < 1.0)
    )
    if not has_signal:
        return 0.5, "no_signal_available — conservative default"
    parts: list[str] = []
    pc = float(parser_confidence) if parser_confidence is not None else parser_confidence_default(parser_name)
    parts.append(f"parser_confidence ({pc:.2f}{'' if parser_confidence is not None else ', default'})")
    pq = float(page_quality) if page_quality is not None else NEUTRAL_PAGE_QUALITY
    parts.append(f"{quality_label} ({pq:.2f}{'' if page_quality is not None else ', neutral default'})")
    value = pc * pq
    if number_quality and number_quality in number_penalties and number_penalties[number_quality] < 1.0:
        pen = number_penalties[number_quality]
        value *= pen
        parts.append(f"{number_quality}_penalty ({pen:.2f})")
    if z3_violation:
        value *= Z3_VIOLATION_PENALTY
        parts.append(f"z3_penalty ({Z3_VIOLATION_PENALTY:.2f})")
    if signature_quality and signature_quality in signature_penalties and signature_penalties[signature_quality] < 1.0:
        pen = signature_penalties[signature_quality]
        value *= pen
        parts.append(f"signature_{signature_quality}_penalty ({pen:.2f})")
    value = round(max(0.0, min(1.0, value)), 4)
    return value, " × ".join(parts) + f" = {value:.2f}"


# --------------------------------------------------------------------------
# Page text / layout helpers — jdf-cli shape and Assure tree shape
# --------------------------------------------------------------------------

def _element_text(el: dict) -> str:
    text = el.get("text") or el.get("content")
    if isinstance(text, str) and text.strip():
        return text
    ocr = el.get("ocr")
    if isinstance(ocr, dict):
        blocks = [str(b.get("text") or "") for b in ocr.get("blocks") or [] if isinstance(b, dict)]
        joined = "\n".join(b for b in blocks if b.strip())
        if joined:
            return joined
    return ""


def _element_ocr_confidence(el: dict) -> float | None:
    """Mean of jdf-cli's ``ocr.blocks[].confidence`` inside one element, or None
    when the element carries no OCR (text-layer elements)."""
    ocr = el.get("ocr")
    if not isinstance(ocr, dict):
        return None
    confs = [float(b["confidence"]) for b in ocr.get("blocks") or [] if isinstance(b, dict) and isinstance(b.get("confidence"), (int, float))]
    return round(sum(confs) / len(confs), 4) if confs else None


def _element_bbox(el: dict, page: dict) -> list[float] | None:
    """Relative ``[x0, y0, x1, y1]`` from jdf-cli's ``position``/``width``/``height`` (mm)
    against the page's ``pageSize``; None when either side is missing.

    jdf-cli 0.2.3 emits ``position {x, y}`` and ``width`` for text elements but
    no ``height`` (measured on a one-line PDF, 2026-09-25); the fallback height
    is the font size in points converted to mm × 1.2 line height, so the box
    covers the line rather than being zero-tall.
    """
    size = page.get("pageSize") or page.get("size") or {}
    pw, ph = size.get("width"), size.get("height")
    pos = el.get("position") or {}
    if not (isinstance(pw, (int, float)) and isinstance(ph, (int, float)) and pw > 0 and ph > 0):
        return None
    if not (isinstance(pos, dict) and isinstance(pos.get("x"), (int, float)) and isinstance(pos.get("y"), (int, float))):
        bbox = el.get("bbox")
        if isinstance(bbox, (list, tuple)) and len(bbox) == 4 and all(isinstance(v, (int, float)) for v in bbox):
            x0, y0, x1, y1 = bbox
            return [round(x0 / pw, 4), round(y0 / ph, 4), round(x1 / pw, 4), round(y1 / ph, 4)]
        return None
    x, y = float(pos["x"]), float(pos["y"])
    w = el.get("width")
    h = el.get("height")
    if not isinstance(w, (int, float)):
        w = pw - x
    if not isinstance(h, (int, float)):
        font = (el.get("style") or {}).get("fontSize") if isinstance(el.get("style"), dict) else None
        h = (float(font) if isinstance(font, (int, float)) else 11.0) * 0.3528 * 1.2
    return [
        round(max(0.0, min(1.0, x / pw)), 4), round(max(0.0, min(1.0, y / ph)), 4),
        round(max(0.0, min(1.0, (x + float(w)) / pw)), 4), round(max(0.0, min(1.0, (y + float(h)) / ph)), 4),
    ]


def _walk_elements(elements: Any):
    for el in elements or []:
        if not isinstance(el, dict):
            continue
        yield el
        for key in ("elements", "children"):
            if isinstance(el.get(key), list):
                yield from _walk_elements(el[key])


# --------------------------------------------------------------------------
# Stable element identity (policy ``eid-v1``, 2026-09-26)
# --------------------------------------------------------------------------

#: Version of the element-id derivation. jdf-cli 0.2.3 elements carry no id
#: and the tree paragraph id is ``new_node_id`` (random per run), so a replay
#: could never name the same node twice (customer benchmark P0 "missing stable
#: element IDs"). An element id is derived from what the document itself
#: fixes — the chunk it belongs to, where it sits, what it says — so the same
#: bytes give the same id in every process. **Any change to the derivation
#: bumps this string** (``eid-v2`` …) and is recorded in docs/parsure.md;
#: ids of different policies are never compared.
NODE_ID_POLICY = "eid-v1"
#: The derivation in words, carried on every report next to the policy.
NODE_ID_DERIVATION = "chunk_id + bbox(3dp) + text sha1[:12]"


def derive_element_id(chunk_id: str | None, page: int | None, bbox: list | tuple | None, text: str) -> str:
    """``"<chunk_id or p<page>>:<sha1(bbox 3dp | collapsed text)[:12]>"`` — the
    ``eid-v1`` identity of one layout element.

    Deterministic across runs and processes: no uuid, no clock, no counter.
    The bbox is rounded to three decimals (relative page coordinates, so
    0.001 ≈ 0.3 mm on A4 — below jdf-cli's own placement noise) and the text
    is whitespace-collapsed, so a re-parse that shifts a box by a hair or
    re-wraps a line still names the same element; a different box or a
    different word is a different element. Two elements with identical text
    at identical positions are the same element.
    """
    prefix = str(chunk_id).strip() if chunk_id else f"p{int(page) if page is not None else 0}"
    bbox_part = ""
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4 and all(isinstance(v, (int, float)) for v in bbox):
        bbox_part = ",".join(f"{round(float(v), 3):.3f}" for v in bbox)
    text_part = " ".join(str(text or "").split())
    digest = hashlib.sha1(f"{bbox_part}|{text_part}".encode("utf-8")).hexdigest()[:12]
    return f"{prefix}:{digest}"


def _stamp_element_ids(layouts: list[list[dict]]) -> None:
    """``element_id`` on every layout segment (``eid-v1``), from the chunk the
    segment belongs to (``chunk_id``; the page number when there is none), its
    bbox and its text. Called once per ``page_layout`` shape, after chunk ids
    are attached, so the id is the same whether the layout came from jdf-cli
    pages or the saved tree that kept those chunk ids."""
    for idx, segs in enumerate(layouts):
        for seg in segs:
            if seg.get("element_id"):
                continue
            seg["element_id"] = derive_element_id(seg.get("chunk_id"), idx + 1, seg.get("bbox"), seg.get("text") or "")


def _element_at(seg: dict | None, rel_start: int) -> dict | None:
    """The ``meta.elements`` entry of a paragraph segment whose character range
    covers offset ``rel_start`` (relative to the segment's text); None when the
    segment carries no element list."""
    if not seg:
        return None
    for el in seg.get("elements") or []:
        if not isinstance(el, dict):
            continue
        s, e = el.get("start_char"), el.get("end_char")
        if isinstance(s, int) and isinstance(e, int) and s <= rel_start < max(e, s + 1):
            return el
    return None


def _attach_chunk_ids(layouts: list[list[dict]], chunks: Any) -> None:
    """Give element segments the id of the jdf-cli chunk that contains them.

    jdf-cli 0.2.3 ``pages[].elements[]`` carry no ``id`` (checked 2026-09-26:
    keys are content/position/style/type/width), so every field's
    ``field_source_node_id`` was ``None`` — a customer read that as "Assure
    does not address JDF nodes". The ``jdf chunk`` output does carry ids
    (``p1e0`` = page 1, element group 0) and a page, and the Assure document
    tree keeps that id on each paragraph (``meta.chunk_id``), so the chunk id
    is the address that survives from parse to tree to review. The chunk id
    always lands in ``chunk_id`` (the ``eid-v1`` prefix); ``node_id`` takes it
    only when the element had no id of its own.
    """
    if not isinstance(chunks, list) or not chunks:
        return
    by_page: dict[int, list[tuple[str, str]]] = {}
    for c in chunks:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("id") or "").strip()
        text = " ".join(str(c.get("text") or c.get("content") or "").split())
        if not cid or not text:
            continue
        try:
            page_no = int(c.get("page") or 0)
        except (TypeError, ValueError):
            page_no = 0
        by_page.setdefault(page_no, []).append((cid, text))
    for idx, segs in enumerate(layouts):
        candidates = by_page.get(idx + 1) or [c for cs in by_page.values() for c in cs]
        for seg in segs:
            if seg.get("chunk_id"):
                continue
            needle = " ".join(str(seg.get("text") or "").split())
            if not needle:
                continue
            for cid, text in candidates:
                if needle in text or (len(needle) > 40 and needle[:40] in text):
                    seg["chunk_id"] = cid
                    if not seg.get("node_id"):
                        seg["node_id"] = cid
                    break


def page_layout(bundle: dict) -> list[list[dict]]:
    """Per page, the text segments in reading order with their provenance.

    Each segment: ``{"start", "end", "text", "node_id", "chunk_id", "bbox",
    "ocr_confidence", "local_quality", "element_id", "elements"}`` where
    ``start``/``end`` are offsets into that page's text as ``page_texts``
    returns it (segments joined by ``"\\n"``). ``ocr_confidence`` is the mean
    jdf-cli block confidence inside that element (None on the text layer) and
    ``local_quality`` is the quality factor a field on that segment should
    use — the OCR confidence when there is one, else None so the caller falls
    back to the page score (the visual probe's factors are page-global).
    ``chunk_id`` is the jdf-cli chunk the segment belongs to (from the chunk
    list, or the tree paragraph's ``meta.chunk_id``), ``element_id`` its
    ``eid-v1`` identity (``derive_element_id``), and ``elements`` — tree
    paragraphs only — the paragraph's ``meta.elements`` so a value's span
    resolves to the element inside it. Handles the three shapes the ingest
    produces: jdf-cli ``pages[].elements[]`` (``content``/``text`` +
    ``position``; OCR ``ocr.blocks``), the Assure tree ``body[].children[]``
    (paragraph ``content`` with node ids, page from ``meta.source_page``),
    and, failing both, the bundle's ``chunks`` grouped by ``page`` or the
    flat ``text`` split on form feeds.
    """
    jdf = bundle.get("jdf") if isinstance(bundle, dict) else None
    layouts: list[list[dict]] = []

    def _append(segments: list[dict]) -> None:
        layouts.append(segments)

    def _segments_from(items: list[tuple]) -> list[dict]:
        segs: list[dict] = []
        cursor = 0
        for item in items:
            text, node_id, bbox = item[0], item[1], item[2]
            ocr_conf = item[3] if len(item) > 3 else None
            chunk_id = item[4] if len(item) > 4 else None
            elements = item[5] if len(item) > 5 else None
            text = text.rstrip("\n")
            if not text.strip():
                continue
            segs.append({"start": cursor, "end": cursor + len(text), "text": text, "node_id": node_id, "chunk_id": chunk_id, "bbox": bbox,
                         "ocr_confidence": ocr_conf, "local_quality": ocr_conf, "element_id": None,
                         "elements": list(elements) if isinstance(elements, list) else None})
            cursor += len(text) + 1
        return segs

    if isinstance(jdf, dict) and isinstance(jdf.get("pages"), list) and not isinstance(jdf.get("body"), list):
        for page in jdf["pages"]:
            if not isinstance(page, dict):
                _append([])
                continue
            items = []
            for el in _walk_elements(page.get("elements")):
                text = _element_text(el)
                if not text.strip():
                    continue
                node_id = el.get("id")
                items.append((text, str(node_id) if node_id else None, _element_bbox(el, page), _element_ocr_confidence(el)))
            _append(_segments_from(items))
        if any(layouts):
            _attach_chunk_ids(layouts, bundle.get("chunks"))
            _stamp_element_ids(layouts)
            return layouts
        layouts = []

    if isinstance(jdf, dict) and isinstance(jdf.get("body"), list):
        for section in jdf["body"]:
            if not isinstance(section, dict):
                continue
            items = []
            stack = list(section.get("children") or [])
            while stack:
                node = stack.pop(0)
                if not isinstance(node, dict):
                    continue
                if node.get("type") == "table":
                    # The caption is jdf-cli's own ``Header: cell | …`` text of the
                    # chunk (jdf_converter), and it is what the page text carried
                    # before tree table nodes gained their grid (2026-09-27); it
                    # stays the page text so anchors and replay ids do not move.
                    # Table *values* are read by services/table_extraction, not
                    # from this text. The grid is flattened only for a node
                    # without a caption (hand-built trees).
                    cells = [str(c) for row in node.get("rows") or [] for c in row]
                    caption = str(node.get("caption") or "")
                    text = caption if caption.strip() else ("\n".join([" | ".join(str(h) for h in node.get("headers") or [])] + [" | ".join(str(c) for c in row) for row in node.get("rows") or []]) if cells else "")
                else:
                    text = str(node.get("content") or node.get("title") or node.get("alt") or "")
                if text.strip():
                    nmeta = node.get("meta") if isinstance(node.get("meta"), dict) else {}
                    chunk_id = str(nmeta.get("chunk_id")) if nmeta.get("chunk_id") else None
                    elements = nmeta.get("elements") if isinstance(nmeta.get("elements"), list) else None
                    items.append((text, str(node.get("id")) if node.get("id") else None, None, None, chunk_id, elements))
                stack = list(node.get("children") or []) + stack
            _append(_segments_from(items))
        if layouts:
            _stamp_element_ids(layouts)
            return layouts

    chunks = bundle.get("chunks") if isinstance(bundle, dict) else None
    if isinstance(chunks, list) and chunks:
        by_page: dict[int, list] = {}
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            try:
                page_no = int(chunk.get("page") or 1)
            except (TypeError, ValueError):
                page_no = 1
            cid = str(chunk.get("id")) if chunk.get("id") else None
            by_page.setdefault(page_no, []).append((str(chunk.get("text") or chunk.get("content") or ""), cid, None, None, cid))
        page_count = max(int(bundle.get("page_count") or 1), max(by_page) if by_page else 1)
        for page_no in range(1, page_count + 1):
            _append(_segments_from(by_page.get(page_no, [])))
        _stamp_element_ids(layouts)
        return layouts

    text = str(bundle.get("text") or "") if isinstance(bundle, dict) else ""
    pages = split_pages(text)
    for page_text in pages:
        _append(_segments_from([(page_text, None, None)]))
    _stamp_element_ids(layouts)
    return layouts


#: Page separators a flat text carries: the form feed PyMuPDF/pdftotext emit,
#: or a ``=== PAGE ===`` / ``=== PAGE 2 ===`` line — the readable form the
#: golden fixtures use (a literal \f is invisible in a diff).
_PAGE_BREAK_RE = re.compile(r"\f|\n?^[ \t]*=== PAGE(?: \d+)? ===[ \t]*$\n?", re.M)


def split_pages(text: str) -> list[str]:
    """A flat text into per-page strings on ``\f`` or a ``=== PAGE ===`` line."""
    if not _PAGE_BREAK_RE.search(text or ""):
        return [text or ""]
    return _PAGE_BREAK_RE.split(text)


def page_texts(bundle: dict) -> list[str]:
    """One string per page (1-indexed pages → index 0), any bundle shape."""
    return ["\n".join(seg["text"] for seg in segs) for segs in page_layout(bundle)]


def _segment_at(segments: list[dict], start: int) -> dict | None:
    for seg in segments:
        if seg["start"] <= start <= seg["end"]:
            return seg
    return None


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------

def _value_pattern(field_type: str) -> str:
    return {
        "money": _MONEY_RE, "number": _NUMBER_RE, "date": _DATE_RE, "vin": _VIN_RE, "codes": _CODES_RE,
    }.get(field_type, _TEXT_RE)


_LABEL_LIKE = re.compile(r"^[A-Za-z][A-Za-z /]{1,30}:")


_HEADER_WORDS = frozenset({
    "address", "information", "section", "declarations", "declaration", "coverage", "coverages", "page", "description",
    "location", "mailing", "schedule", "summary", "statement", "details", "form", "notice", "certificate", "total",
    "amount", "date", "number", "signature", "name", "policy", "insured", "applicant", "premium", "vehicle", "property",
})
#: A street address has a house number before a street word, or a unit/box
#: token — a bare "Dr." or "St." is a title or a saint, not a street
#: (tests/golden deeds and mortgages carry "Dr. …" names, 2026-09-27).
_ADDRESS_RE = re.compile(
    r"(?:^|\s)\d{1,6}\s+[A-Za-z0-9.'\- ]{1,40}?\b(?:st|street|ave|avenue|rd|road|blvd|boulevard|dr|drive|ln|lane|ct|court|hwy|highway|way|pl|place)\b\.?"
    r"|\b(?:suite|ste|apt|unit)\b\.?\s*#?\s*\d|\bp\.?o\.?\s*box\b",
    re.I,
)
#: Legal names run long ("Eleanor M. Bradford, of Plymouth, Plymouth County,
#: Massachusetts"; "Samuel T. Okafor and Grace A. Okafor" — tests/golden):
#: the cap is on nonsense, not on parties.
NAME_MAX_WORDS = 12
_ID_SHAPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-/. ]{2,29}$")
_ID_FIELDS = frozenset({"policy_number", "claim_number"})
VALUE_QUALITIES = ("valid", "invalid_format", "garbage", "header_or_label", "address_fragment")
#: ``provenance_confidence`` by value shape (plan Part 1.1, 2026-09-27): the
#: span of a header or of OCR debris is a real span, but "the right place for
#: this value" is exactly what a header under the label disproves — so the
#: figure drops with the shape instead of reading 1.0 beside garbage. Value
#: quality itself stays a separate signal (``value_quality``).
PROVENANCE_BY_SHAPE = {"valid": 1.0, "invalid_format": 0.7, "address_fragment": 0.6, "garbage": 0.3, "header_or_label": 0.3}
GROUNDING_QUOTE_MAX = 240


def line_quote(page_text: str | None, start: int, end: int) -> str | None:
    """The verbatim line(s) of ``page_text`` holding ``[start, end)`` — the
    label-pass grounding quote (secondary grounding, plan Part 2.9)."""
    if not page_text or start is None or end is None or start < 0 or end > len(page_text) or start >= end:
        return None
    a = page_text.rfind("\n", 0, start) + 1
    b = page_text.find("\n", end)
    b = len(page_text) if b < 0 else b
    quote = page_text[a:b].strip()
    return quote[:GROUNDING_QUOTE_MAX] if quote else None


def attach_grounding(field: dict[str, Any], *, page_text: str | None, page_index: int, start: int, end: int, source: str, model: str | None) -> None:
    """``grounding_quote`` / ``grounding_span`` / ``grounding_model`` /
    ``grounding_source`` for a located value. The span repeats the provenance
    (page, offsets, element, node) so an export can carry the grounding alone."""
    span = field.get("source_span") if isinstance(field.get("source_span"), dict) else {}
    field["grounding_quote"] = line_quote(page_text, start, end)
    field["grounding_span"] = {"page": page_index + 1, "start_char": start, "end_char": end,
                               "element_id": span.get("element_id") or field.get("element_id"), "node_id": field.get("field_source_node_id")}
    field["grounding_model"] = model
    field["grounding_source"] = source


def value_shape(spec: FieldSpec, raw: str | None, parsed: Any = ...) -> dict[str, Any]:
    """``value_quality`` — does the text under the label have the *shape* of this
    field's value? Provenance says where a value came from; this says whether
    it is one. Added 2026-09-27 after a customer run in which ``insured_name``
    held a section header ("MAILING ADDRESS") and ``policy_number`` held OCR
    debris, both with provenance confidence 1.0 and no flag anywhere.

    Names: words only, 1–6 of them, no digits, no street token, no header
    word. Policy/claim numbers: 3–30 letters/digits/dashes with at least one
    digit. Typed fields (money, number, date, VIN, codes): the parse is the
    shape — ``parsed`` None is ``invalid_format``. Any value under 50 %
    alphanumeric is ``garbage``. ``basis`` says which rule spoke.
    """
    text = (raw or "").strip()
    if not text:
        return {"quality": "invalid_format", "basis": "empty value"}
    alnum = sum(ch.isalnum() for ch in text)
    ratio = alnum / len(text)
    if ratio < 0.5:
        return {"quality": "garbage", "basis": f"{ratio:.0%} of characters are letters or digits"}
    if spec.field_type in ("money", "number", "date", "vin", "codes"):
        if parsed is None:
            return {"quality": "invalid_format", "basis": f"'{text[:40]}' is not a {spec.field_type} value"}
        return {"quality": "valid", "basis": f"reads as a {spec.field_type} value"}
    words = re.findall(r"[A-Za-z][A-Za-z.'\-]*", text)
    lowered = {w.lower().strip(".") for w in words}
    if spec.field_type == "name":
        if _ADDRESS_RE.search(text) or re.match(r"^\d", text):
            return {"quality": "address_fragment", "basis": "street address shape in a name field"}
        if re.search(r"\d", text):
            return {"quality": "invalid_format", "basis": "digits in a name"}
        if not 1 <= len(words) <= NAME_MAX_WORDS:
            return {"quality": "invalid_format", "basis": f"{len(words)} words is not a name"}
        hdr = lowered & _HEADER_WORDS
        # Header when header words carry the value ("MAILING ADDRESS", "INSURED
        # INFORMATION"), not when one sits inside a longer party name.
        if hdr and (2 * len(hdr) >= len(words) or (text.isupper() and len(words) <= 4)):
            return {"quality": "header_or_label", "basis": f"header/label words in a name field: {', '.join(sorted(hdr))}"}
        return {"quality": "valid", "basis": f"{len(words)} name word{'s' if len(words) != 1 else ''}, no digits"}
    if spec.name in _ID_FIELDS:
        if not _ID_SHAPE_RE.match(text) or alnum < 3 or not re.search(r"\d", text):
            return {"quality": "invalid_format", "basis": "expected 3–30 letters/digits with at least one digit"}
        return {"quality": "valid", "basis": "letters/digits identifier shape"}
    if words and lowered and lowered <= _HEADER_WORDS and len(words) <= 3:
        return {"quality": "header_or_label", "basis": f"only header/label words: {text[:40]}"}
    return {"quality": "valid", "basis": "text value"}


def _find_candidates(spec: FieldSpec, text: str):
    """Yield every (raw, start, end) the anchors of ``spec`` locate in ``text``, in
    anchor order then position order — ``_find_field`` takes the first one whose
    shape is valid, ``_find_suspect`` the first one that is not."""
    for anchor in spec.anchors:
        pattern = re.compile(
            r"(?<![a-z])" + anchor + r"(?!'|[a-z])" + r"(?P<sep>" + _SEP + r")(?P<val>" + _value_pattern(spec.field_type) + r")",
            re.I,
        )
        for m in pattern.finditer(text):
            raw = m.group("val")
            start, end = m.span("val")
            if spec.field_type == "vin":
                if re.match(r"[A-Za-z0-9]", text[end:end + 1]):
                    continue
                raw = raw.upper()
            elif spec.field_type in ("text", "name", "signature"):
                stripped = raw.lstrip()
                start += len(raw) - len(stripped)
                raw = _clean_text_value(stripped)
                if not raw or re.fullmatch(r"[_\s.]*", raw) or _LABEL_LIKE.match(raw):
                    continue
                if spec.field_type in ("text", "name") and not re.search(r"[:#\-–—\n]", m.group("sep")) and raw[:1].islower():
                    continue
                end = start + len(raw)
            else:
                raw = raw.strip()
            if spec.field_type == "name" and (len(raw) > 80 or re.search(r"\d{3,}", raw)):
                continue
            yield raw, start, end


def _find_suspect(spec: FieldSpec, text: str) -> tuple[str, int, int, dict[str, Any]] | None:
    """The first anchor hit whose shape is *not* valid — what ``_find_field``
    passed over. Surfaced as ``found_suspect`` when no valid hit exists, so a
    label followed by debris is reported as debris, not as absence."""
    for raw, start, end in _find_candidates(spec, text):
        shape = value_shape(spec, raw) if spec.field_type in ("text", "name") else {"quality": "valid"}
        if shape["quality"] != "valid":
            return raw, start, end, shape
    return None


def _find_field(spec: FieldSpec, text: str) -> tuple[str, int, int] | None:
    """(raw value, start, end) of the first anchor+value hit in ``text`` whose
    shape is valid (``value_shape``), or None.

    The anchor must end at a word boundary (``(?!'|[a-z])`` — "Buyer's
    Signature" is not the buyer's name) and the separator may cross at most
    one line break, so a label whose value sits on the next line is read but a
    label followed by *another* label is not.

    A text/name capture with **no separator** after the label (only spaces)
    that starts with a lowercase letter is prose running on from the word,
    not a value: "the covered vehicle is a 2003 Honda…" is not
    ``vehicle = "is a 2003 Honda…"``. Measured on tests/golden/prose
    (2026-09-25): 12 of the 15 residual misses after the LLM pass were such
    captures, and because the field then held a (wrong) value it was never
    offered to the grounded pass. Form values start with a capital or a digit
    ("Agent Mary Agent" with an OCR-dropped colon still reads), so labeled
    documents are unaffected (tests/golden stays 52/52).
    """
    for raw, start, end in _find_candidates(spec, text):
        if spec.field_type in ("text", "name") and value_shape(spec, raw)["quality"] != "valid":
            # A section header or address fragment under a name label is not
            # the name; keep looking (the label may repeat with the real value).
            continue
        return raw, start, end
    return None


def _empty_field(spec: FieldSpec, reason: str = "field not found") -> dict[str, Any]:
    return {
        "name": spec.name,
        "label": spec.label,
        "field_type": spec.field_type,
        "value": None,
        "raw": None,
        "extraction_confidence": 0.0,
        "confidence_basis": reason,
        # No value → nothing was verified: ``None``, never the 0.85 default
        # (customer, 2026-09-27: "verification_confidence 0.85 on a field that
        # was not found"). ``attach_verification_confidence`` fills found fields.
        "verification_confidence": None,
        "verification_basis": "nothing to verify: no value",
        "provenance_confidence": 0.0,
        "value_quality": None,
        # Grounding (plan Part 2.9 / 11, 2026-09-27): the verbatim text that
        # supports the value, where it sits, and who located it — None until
        # something did.
        "grounding_quote": None,
        "grounding_span": None,
        "grounding_model": None,
        "grounding_source": None,
        "field_uid": None,
        "signature_quality": None,
        "number_quality": None,
        "source_span": None,
        "field_source_node_id": None,
        "element_id": None,
        "field_state": "not_found",
        "routing_action": "field_not_found",
        "review_required": False,
        "reason": reason,
        "z3_violation": False,
        "plausibility_violation": False,
        "verification_source": None,
        "compliance_bound": spec.compliance_bound,
        "corrected": False,
        "extraction_method": None,
        "evidence": None,
        "evidence_state": None,
        "quality_source": None,
    }


#: The five outcomes a field can have, kept apart from ``field_state`` (what the
#: policy decided) and ``routing_action`` (who acts) — spec §5 vocabularies never
#: mix, and this one answers a different question: *what did the extractor see?*
#: ``found_verified`` / ``found_unverified``: a value, accepted or not;
#: ``not_on_document``: no value on pages that were readable;
#: ``unreadable``: no value and no readable page to say it is absent;
#: ``schema_mismatch``: the type is wrong for the page — the fields are not
#: applicable, and the document, not the fields, asks for a person;
#: ``found_suspect`` (2026-09-27): a label was found and text sits under it,
#: but the text fails the field's shape check (``value_shape``) — debris, a
#: section header, an address fragment — so no value is taken and the reviewer
#: is pointed at the span.
EVIDENCE_STATES = ("found_verified", "found_unverified", "not_on_document", "unreadable", "schema_mismatch", "found_suspect")
EVIDENCE_REASONS = {
    "not_on_document": "Not on this document type",
    "unreadable": "Page could not be read",
    "schema_mismatch": "Wrong document type — fields not applicable",
    "found_suspect": "Text under the label is not a valid value",
}
#: A page is *readable* (its silence about a field means absence) at this many
#: characters and this page quality; *unreadable* below the second pair.
#: Between the two the page is low but not blind, and an absent field still
#: reads ``not_on_document`` with the quality named in the basis.
READABLE_MIN_CHARS = 200
READABLE_MIN_QUALITY = 0.5
UNREADABLE_MAX_QUALITY = 0.3
#: How many layout node ids an absent field lists as searched.
ABSENT_NODE_LIST_MAX = 50


def page_readability(texts: list[str], page_quality: list[float | None] | None) -> list[str]:
    """``readable`` / ``low`` / ``unreadable`` per page from the two measured
    facts at hand: characters of text and the page quality score (None →
    judged on text alone)."""
    out: list[str] = []
    pq = list(page_quality or [])
    for i, text in enumerate(texts or []):
        chars = len((text or "").strip())
        q = pq[i] if i < len(pq) else None
        if chars < READABLE_MIN_CHARS or (q is not None and q < UNREADABLE_MAX_QUALITY):
            out.append("unreadable")
        elif q is None or q >= READABLE_MIN_QUALITY:
            out.append("readable")
        else:
            out.append("low")
    return out


def attach_absent_evidence(field: dict[str, Any], texts: list[str], layout: list[list[dict]], page_quality: list[float | None] | None) -> dict[str, Any]:
    """Anchor a field that was not found so the provenance graph has no orphan.

    ``evidence`` records what was searched — the pages, the first
    ``ABSENT_NODE_LIST_MAX`` layout node ids, the character count — and the
    ``anchor_node_id``: the first layout node id (page 1 first, else any page).
    ``field_source_node_id`` is that anchor and ``source_span`` is
    ``{"span_type": "absent", "pages": [...]}``; ``v1_orchestrator.
    attach_tree_node_ids`` maps the anchor onto the saved tree (or the tree
    root) like any found field. ``evidence_state`` is ``not_on_document`` when
    at least one searched page was readable, ``unreadable`` when none was
    (``page_readability``); ``reason`` follows unless a more specific one
    (signature missing, unparsable value) is already there. Added 2026-09-26:
    a customer's report had ``field_source_node_id: null`` on nine of twelve
    fields and read it as "Assure lost the link"."""
    texts = list(texts or [])
    pages = [i + 1 for i, t in enumerate(texts) if (t or "").strip()] or list(range(1, len(texts) + 1))
    node_ids: list[str] = []
    for segs in layout or []:
        for seg in segs:
            nid = seg.get("node_id")
            if nid and nid not in node_ids:
                node_ids.append(str(nid))
    anchor = node_ids[0] if node_ids else None
    readability = page_readability(texts, page_quality)
    searched = [readability[p - 1] for p in pages if p - 1 < len(readability)]
    if "readable" in searched:
        state = "not_on_document"
    elif searched and all(r == "unreadable" for r in searched):
        state = "unreadable"
    elif searched:
        state = "not_on_document"
    else:
        state = "unreadable"
    chars = sum(len((t or "").strip()) for t in texts)
    field["evidence"] = {
        "kind": "absent",
        "searched_pages": pages,
        "searched_node_ids": node_ids[:ABSENT_NODE_LIST_MAX],
        "searched_chars": chars,
        "anchor_node_id": anchor,
        "anchor_kind": "layout_node" if anchor else None,
        "readability": searched,
    }
    field["evidence_state"] = state
    field["field_source_node_id"] = anchor
    field["source_span"] = {"span_type": "absent", "pages": pages}
    if field.get("field_type") != "signature":
        # Pre-policy routing of an absence (2026-09-27): recorded when the pages
        # were readable, a rescan when none was — the policy (rule 0) repeats
        # this, so a field read before the policy runs says the same.
        field["field_state"] = "not_found"
        field["routing_action"] = "retry_parsure" if state == "unreadable" else "field_not_found"
        field["review_required"] = state == "unreadable"
    low = [str(p) for p, r in zip(pages, searched) if r == "low"]
    field["confidence_basis"] = (
        f"not found on {len(pages)} page{'s' if len(pages) != 1 else ''} ({chars} characters searched"
        + (f"; page quality low on p.{', '.join(low)}" if low and state == "not_on_document" else "")
        + ("; no readable page" if state == "unreadable" else "") + ")"
    )
    if field.get("reason") in (None, "field not found"):
        field["reason"] = EVIDENCE_REASONS[state]
    return field


def mark_schema_mismatch(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every field of a schema the page does not carry: ``evidence_state``
    ``schema_mismatch``, confidence ``None`` (not computed — a number here
    would describe a value that has no business on this document), routing
    ``none`` so the queue does not list them; the document asks once."""
    for f in fields:
        f["evidence_state"] = "schema_mismatch"
        f["extraction_confidence"] = None
        f["confidence_basis"] = "not computed: schema mismatch"
        f["field_state"] = "unverified"
        f["routing_action"] = "none"
        f["review_required"] = False
        f["reason"] = EVIDENCE_REASONS["schema_mismatch"]
    return fields


#: How a value was located. ``label_anchor``: the regex pass in this module.
#: ``llm_grounded``: ``services/llm_extraction`` asked a model for a verbatim
#: quote and the quote (and the value inside it) was re-found in the page text
#: before the value was taken (spec §8 item 6 — the model never supplies the
#: value, only the place to look).
#: ``table`` (2026-09-27, plan Part 3): the value is a cell of a jdf-cli table
#: element matched by header/row label (``services/table_extraction``); the
#: label pass never sees table cells because jdf-cli 0.2.3 emits a table as
#: one ``type: "table"`` element with ``headers``/``rows`` and no text
#: (measured on bench/cases repair_estimate.pdf and coverage_schedule.pdf).
EXTRACTION_METHODS = ("label_anchor", "llm_grounded", "table")
#: A grounded LLM find is a real, located value, but the *locating* step was a
#: model's reading rather than a label match, and the model can quote the
#: neighbouring sentence (qwen2.5:1.5b returned the property-damage figure
#: for ``liability_limit`` in 3 of 5 prose runs, 2026-09-25 — a value that is
#: on the page but not this field's). The factor is stated in the basis and,
#: at default parser confidence, keeps a grounded field below the 0.75
#: auto-accept line (0.85 × 1.0 × 0.85 = 0.72 → manual review).
LLM_GROUNDED_FACTOR = 0.85


def _locate(field: dict[str, Any], spec: FieldSpec, *, page_index: int, start: int, end: int, layout: list[list[dict]], method: str) -> dict | None:
    """Provenance for a value (or suspect) at ``[start, end)`` on a page:
    ``source_span`` (bbox when the layout has one, text range otherwise),
    ``field_source_node_id``, ``element_id``, ``evidence``, provenance 1.0.
    Returns the layout segment. Element identity (``eid-v1``, 2026-09-26): the
    segment's own id, or — when the segment is a tree paragraph carrying
    ``meta.elements`` — the element whose character range holds the value,
    with that element's bbox and the value's offsets inside the paragraph
    (``node_offsets``), so a one-paragraph page still resolves to the line."""
    segments = layout[page_index] if page_index < len(layout) else []
    seg = _segment_at(segments, start)
    span: dict[str, Any] = {"page": page_index + 1, "span_type": "text_range", "start_char": start, "end_char": end}
    if seg and seg.get("bbox"):
        span = {"page": page_index + 1, "span_type": "bbox_relative", "bbox": seg["bbox"], "start_char": start, "end_char": end}
    element_id = seg.get("element_id") if seg else None
    if seg:
        rel_start, rel_end = start - int(seg.get("start") or 0), end - int(seg.get("start") or 0)
        el = _element_at(seg, rel_start)
        if el:
            element_id = el.get("element_id") or element_id
            span["node_offsets"] = {"start_char": rel_start, "end_char": rel_end}
            if isinstance(el.get("bbox"), list) and len(el["bbox"]) == 4:
                span = {**span, "span_type": "bbox_relative", "bbox": el["bbox"]}
    span["element_id"] = element_id
    field["source_span"] = span
    field["field_source_node_id"] = seg.get("node_id") if seg else None
    field["element_id"] = element_id
    field["provenance_confidence"] = 1.0
    field["evidence"] = {"kind": "found", "page": page_index + 1, "node_id": field["field_source_node_id"], "element_id": element_id, "method": method}
    return seg


def build_found_field(
    spec: FieldSpec,
    *,
    page_index: int,
    raw: str,
    start: int,
    end: int,
    parser_name: str | None,
    parse_confidence: float | None,
    ocr_confidence: float | None,
    page_quality: list[float | None],
    visual_pages: list[dict],
    layout: list[list[dict]],
    method: str = "label_anchor",
    page_text: str | None = None,
) -> dict[str, Any]:
    """The contract record for a value located at ``[start, end)`` on a page.

    Shared by the label pass here and the grounded LLM pass so both produce
    the same shape: typed value (money/number/date parsed, VIN upper-cased and
    check-digit validated), ``source_span`` (bbox when the layout has one,
    text range otherwise), ``field_source_node_id``, ``number_quality`` and the
    quality-weighted confidence with its basis. A value that does not parse
    for its type stays ``None`` with the reason saying what was read.
    ``method="llm_grounded"`` multiplies the confidence by
    ``LLM_GROUNDED_FACTOR`` and extends the basis.
    """
    field = _empty_field(spec)
    field["raw"] = raw
    # Something was read under the label: until the policy runs this is a
    # review item, not an absence (``_empty_field`` is the absence record).
    field["field_state"], field["routing_action"], field["review_required"] = "unverified", "manual_review", True
    field["reason"] = None
    value: Any = raw
    if spec.field_type == "money":
        value = _parse_money(raw)
    elif spec.field_type == "number":
        value = _parse_number(raw)
    elif spec.field_type == "date":
        value = _parse_date(raw)
    elif spec.field_type == "vin":
        value = raw.upper()
    elif spec.field_type == "codes":
        value = _parse_codes(raw)
    shape = value_shape(spec, raw, value)
    field["value_quality"] = shape
    pq = page_quality[page_index] if page_index < len(page_quality) else None
    if shape["quality"] != "valid":
        value = None
    if value is None:
        # ``found_suspect``: located, not usable. Provenance stays (the span is
        # real) so the reviewer is taken to the debris; the value is not.
        _locate(field, spec, page_index=page_index, start=start, end=end, layout=layout, method=method)
        field["provenance_confidence"] = PROVENANCE_BY_SHAPE.get(shape["quality"], 0.5)
        attach_grounding(field, page_text=page_text, page_index=page_index, start=start, end=end,
                         source="llm" if method == "llm_grounded" else "label_anchor", model=None if method == "llm_grounded" else "label_anchor")
        if spec.field_type in ("money", "number", "vin") or spec.name in _ID_FIELDS:
            field["number_quality"] = {"quality": "invalid_format", "penalty": NUMBER_PENALTIES["invalid_format"], "review_required": True, "basis": shape["basis"]}
        field["evidence_state"] = "found_suspect"
        field["reason"] = f"{shape['quality'].replace('_', ' ')} under the {spec.label} label: '{raw[:40]}' — {shape['basis']}"
        field["confidence_basis"] = f"not computed: {shape['quality']} ({shape['basis']})"
        field["extraction_method"] = method
        return field
    field["value"] = value
    field["extraction_method"] = method
    visual = visual_pages[page_index] if page_index < len(visual_pages) else None
    handwritten = bool(visual and "handwritten" in (visual.get("flags") or []))
    seg = _locate(field, spec, page_index=page_index, start=start, end=end, layout=layout, method=method)
    field["provenance_confidence"] = PROVENANCE_BY_SHAPE["valid"]
    attach_grounding(field, page_text=page_text, page_index=page_index, start=start, end=end,
                     source="llm" if method == "llm_grounded" else "label_anchor", model=None if method == "llm_grounded" else "label_anchor")
    # Field-level quality (2026-09-26): the OCR confidence of the line the
    # value sits on is a nearer measurement than the page-global score, so it
    # takes the quality factor's place when the segment carries one; the basis
    # names which was used. The page score stays the fallback on a text layer.
    local = seg.get("local_quality") if seg else None
    quality, quality_label = (float(local), "local_ocr") if isinstance(local, (int, float)) else (pq, "page_quality")
    field["quality_source"] = quality_label
    field["local_quality"] = float(local) if isinstance(local, (int, float)) else None
    number_quality = None
    if spec.field_type in ("money", "number", "vin"):
        nq = assess_number(raw, ocr_confidence=float(local) if isinstance(local, (int, float)) else ocr_confidence, page_quality=pq, handwritten=handwritten)
        field["number_quality"] = nq
        number_quality = nq.get("quality")
    conf, basis = quality_weighted_confidence(
        parser_confidence=parse_confidence, parser_name=parser_name, page_quality=quality, number_quality=number_quality,
        quality_label=quality_label,
    )
    if method == "llm_grounded":
        conf = round(conf * LLM_GROUNDED_FACTOR, 4)
        basis = f"{basis.rsplit(' = ', 1)[0]} × llm_grounded ({LLM_GROUNDED_FACTOR:.2f}) = {conf:.2f}"
    field["extraction_confidence"] = conf
    field["confidence_basis"] = basis
    field["reason"] = None
    if spec.field_type == "vin":
        check = validate_vin(value)
        field["vin_check"] = check
        if not check["valid"]:
            field["plausibility_violation"] = True
            field["verification_source"] = "vin_check"
            field["verification_confidence"] = 0.0
            field["verification_basis"] = f"VIN check digit: {check['reason']}"
            field["value_quality"] = {"quality": "invalid_format", "basis": f"VIN check failed: {check['reason']}"}
            field["reason"] = f"VIN rejected: {check['reason']}"
    if field["number_quality"] and field["number_quality"].get("review_required"):
        field["reason"] = field["reason"] or f"number_quality {field['number_quality']['quality']}: {field['number_quality'].get('basis', '')}"
    return field


def extract_fields(
    document_type: str,
    page_texts_in: list[str],
    *,
    jdf: dict | None = None,
    parser_name: str | None,
    parse_confidence: float | None,
    ocr_confidence: float | None,
    page_quality: list[float | None] | None,
    visual_pages: list[dict] | None = None,
    layout: list[list[dict]] | None = None,
) -> list[dict[str, Any]]:
    """Label-anchored extraction of the taxonomy fields for ``document_type``.

    Each returned field carries the spec §4 contract keys. A field not found
    on any page is the explicit ``None / 0.0 / review_required`` record — the
    extractor never fills a value it did not read. ``layout`` (from
    ``page_layout``) supplies bbox + node ids; when absent (or ``jdf`` given)
    it is derived from ``jdf``. Numbers/money/VIN get ``number_quality`` from
    ``assess_number``; the signature field gets ``signature_quality`` from
    ``assess_signature`` on the page where the label was found (or the last
    page when no label was found — spec §9 item 5, bottom-of-document fallback).

    Parser confidence: ``parse_confidence`` when the parser emitted one;
    otherwise the measured OCR confidence (jdf-cli's per-line tesseract mean,
    or Textract's) *is* the parser's confidence for an OCR parse, so it is
    used before any default. Measured 2026-09-25: a phone photo read at OCR
    0.93 was scored from the 0.50 unknown-parser default because the OCR
    figure was only being used for number quality.
    """
    if parse_confidence is None and ocr_confidence is not None:
        parse_confidence = float(ocr_confidence)
    specs = FIELD_TAXONOMY.get(document_type) or []
    texts = list(page_texts_in or [])
    if layout is None and jdf is not None:
        layout = page_layout({"jdf": jdf})
    layout = layout or []
    page_quality = list(page_quality or [])
    visual_pages = list(visual_pages or [])
    results: list[dict[str, Any]] = []
    for spec in specs:
        hit: tuple[int, str, int, int] | None = None
        for page_index, text in enumerate(texts):
            found = _find_field(spec, text or "")
            if found:
                hit = (page_index, *found)
                break
        if spec.field_type == "signature":
            results.append(_signature_field(spec, texts, hit, parser_name=parser_name, parse_confidence=parse_confidence, page_quality=page_quality, visual_pages=visual_pages, layout=layout))
            continue
        if hit is None:
            suspect = None
            for page_index, text in enumerate(texts):
                suspect = _find_suspect(spec, text or "")
                if suspect:
                    hit = (page_index, *suspect[:3])
                    break
            if hit is None:
                results.append(_empty_field(spec))
                continue
        page_index, raw, start, end = hit
        field = build_found_field(
            spec, page_index=page_index, raw=raw, start=start, end=end, parser_name=parser_name,
            parse_confidence=parse_confidence, ocr_confidence=ocr_confidence, page_quality=page_quality,
            visual_pages=visual_pages, layout=layout, page_text=texts[page_index] if page_index < len(texts) else None,
        )
        mark_low_quality_page(field, page_quality)
        results.append(field)
    for f in results:
        if f.get("value") is None and f.get("evidence_state") != "found_suspect":
            attach_absent_evidence(f, texts, layout, page_quality)
        elif f.get("evidence_state") == "found_suspect" and not f.get("field_source_node_id"):
            # Flat text (no layout segment under the span): anchor the suspect
            # like an absence so the graph has no orphan.
            anchor = next((str(seg["node_id"]) for segs in layout or [] for seg in segs if seg.get("node_id")), None)
            f["field_source_node_id"] = anchor
            f["evidence"] = {**(f.get("evidence") or {}), "anchor_node_id": anchor, "anchor_kind": "layout_node" if anchor else None}
    return results


def mark_low_quality_page(field: dict[str, Any], page_quality: list[float | None]) -> dict[str, Any]:
    """Quality gate (2026-09-27): a value read from a page scored under
    ``LOW_QUALITY_PAGE`` is never auto-accepted (``low_quality_page`` → policy
    rule 2) and its reason names the score, so a reviewer confirms it against
    the image instead of trusting a poor scan's text."""
    page = ((field.get("source_span") or {}).get("page") or 0) - 1
    pq = page_quality[page] if 0 <= page < len(page_quality) else None
    if field.get("value") is None or pq is None or float(pq) >= LOW_QUALITY_PAGE:
        field["low_quality_page"] = False
        return field
    field["low_quality_page"] = True
    field["reason"] = field.get("reason") or f"page quality {float(pq):.2f} < {LOW_QUALITY_PAGE:.2f} — value read from a poor scan; confirm against the image"
    return field


def _signature_field(spec: FieldSpec, texts: list[str], hit, *, parser_name, parse_confidence, page_quality, visual_pages, layout) -> dict[str, Any]:
    field = _empty_field(spec)
    if hit is not None:
        page_index = hit[0]
    elif texts:
        page_index = len(texts) - 1
    else:
        field["reason"] = "no pages to assess for a signature"
        field["signature_quality"] = {"present": False, "quality": "missing", "review_required": True, "basis": "no pages", "page": None}
        return field
    visual = visual_pages[page_index] if page_index < len(visual_pages) else None
    sig = assess_signature(texts[page_index] if page_index < len(texts) else "", visual=visual, page=page_index + 1)
    field["signature_quality"] = sig
    # A signature field is never ``not_found``: absent or unverifiable, it is a
    # compliance question for a person (spec §6), so the policy sees ``unverified``.
    field["field_state"], field["routing_action"], field["review_required"] = "unverified", "manual_review", True
    if sig.get("present") is None:
        # quality_probe could not measure ink (no visual sample): the label was
        # seen but presence is not a fact this code may state. Not found ≠
        # missing; the reason says which.
        field["reason"] = f"signature presence not verifiable — {sig.get('basis') or 'no visual sample'}"
        field["confidence_basis"] = "signature presence not verifiable (no visual sample)"
        return field
    if not sig.get("present"):
        field["reason"] = "signature missing — manual review required"
        field["confidence_basis"] = f"signature {sig.get('quality')}: {sig.get('basis')}"
        return field
    if sig.get("quality") == "printed_name":
        # A typed name on the signature line is not a signature: no value, a
        # compliance question (2026-09-27 taxonomy — stamps and typed names
        # are not collapsed into "present").
        field["reason"] = f"signature line holds a typed name — {sig.get('next_check') or 'confirm whether a handwritten signature is required'}"
        field["confidence_basis"] = f"signature printed_name: {sig.get('basis')}"
        return field
    quality = str(sig.get("quality") or "present_ambiguous")
    field["value"] = "present"
    field["extraction_method"] = "label_anchor"
    field["raw"] = hit[1] if hit is not None else None
    pq = page_quality[page_index] if page_index < len(page_quality) else None
    if hit is not None:
        _, _, start, end = hit
        # Same provenance path as every other found field (``_locate``): the
        # element inside the paragraph, not the paragraph — so the worker run
        # and a replay name the same element (2026-09-27).
        seg = _locate(field, spec, page_index=page_index, start=start, end=end, layout=layout, method="label_anchor")
        attach_grounding(field, page_text=texts[page_index] if page_index < len(texts) else None, page_index=page_index, start=start, end=end,
                         source="label_anchor", model="label_anchor")
        local = seg.get("local_quality") if seg else None
        if isinstance(local, (int, float)):
            pq, field["quality_source"], field["local_quality"] = float(local), "local_ocr", float(local)
    else:
        # The probe saw a signature marker the label pass did not anchor (its
        # label regex is wider): the page is the provenance, its first layout
        # node the anchor — never an orphan (graph_integrity, 2026-09-27).
        segments = layout[page_index] if page_index < len(layout) else []
        anchor = next((str(seg["node_id"]) for seg in segments if seg.get("node_id")), None)
        field["source_span"] = {"page": page_index + 1, "span_type": "page", "element_id": None}
        field["field_source_node_id"] = anchor
        field["provenance_confidence"] = 0.5
        field["evidence"] = {"kind": "found", "page": page_index + 1, "node_id": anchor, "anchor_node_id": anchor,
                             "anchor_kind": "layout_node" if anchor else None, "method": "visual_probe"}
    conf, basis = quality_weighted_confidence(parser_confidence=parse_confidence, parser_name=parser_name, page_quality=pq, signature_quality=quality,
                                              quality_label=field.get("quality_source") or "page_quality")
    field["extraction_confidence"] = conf
    field["confidence_basis"] = basis
    field["reason"] = None if not sig.get("review_required") else f"signature_quality {quality}: {sig.get('basis', '')}"
    return field


# --------------------------------------------------------------------------
# Plausibility (auto insurance, 6 rules) — consumes fields, not a verifier
# --------------------------------------------------------------------------

def _num(fields_by_name: dict[str, dict], name: str) -> float | None:
    f = fields_by_name.get(name)
    v = f.get("value") if f else None
    return float(v) if isinstance(v, (int, float)) else None


def _date_val(fields_by_name: dict[str, dict], name: str) -> date | None:
    f = fields_by_name.get(name)
    v = f.get("value") if f else None
    try:
        return date.fromisoformat(v) if isinstance(v, str) else None
    except ValueError:
        return None


def coverage_plausibility(fields: list[dict]) -> list[dict[str, Any]]:
    """Six arithmetic checks over extracted auto-insurance fields (spec §9 item 15).

    Each result is ``{"rule", "passed", "message", "fields"}``; ``passed`` is
    None when the inputs are missing (a rule that cannot run did not fail).
    These are plausibility rules, not Z3: they never appear as Z3 violations.
    """
    by_name = {f["name"]: f for f in fields}
    premium = _num(by_name, "premium")
    liability = _num(by_name, "liability_limit")
    collision = _num(by_name, "collision_deductible")
    comprehensive = _num(by_name, "comprehensive_deductible")
    eff = _date_val(by_name, "effective_date")
    exp = _date_val(by_name, "expiration_date")
    vin = (by_name.get("vin") or {}).get("value")
    vehicle = (by_name.get("vehicle_year_make_model") or {}).get("value")
    rules: list[dict[str, Any]] = []

    def add(rule: str, passed: bool | None, message: str, names: list[str]) -> None:
        rules.append({"rule": rule, "passed": passed, "message": message, "fields": names})

    if premium is None:
        add("premium_positive", None, "premium not extracted", ["premium"])
    else:
        add("premium_positive", premium > 0, f"premium {premium:,.2f} {'>' if premium > 0 else '<='} 0", ["premium"])

    if premium is None or liability is None:
        add("premium_within_liability", None, "premium or liability limit not extracted", ["premium", "liability_limit"])
    elif liability <= 0:
        add("premium_within_liability", False, f"liability limit {liability:,.2f} is not positive", ["premium", "liability_limit"])
    else:
        ok = premium <= 0.2 * liability
        add("premium_within_liability", ok, f"premium {premium:,.2f} is {premium / liability:.1%} of liability limit {liability:,.2f} (max 20%)", ["premium", "liability_limit"])

    ded_names = [n for n, v in (("collision_deductible", collision), ("comprehensive_deductible", comprehensive)) if v is not None]
    if not ded_names:
        add("deductibles_in_range", None, "no deductible extracted", ["collision_deductible", "comprehensive_deductible"])
    else:
        bad = [n for n in ded_names if not (0 <= _num(by_name, n) <= 5000)]
        add("deductibles_in_range", not bad, ("deductibles within 0–5000" if not bad else f"out of range: {', '.join(bad)}"), ded_names)

    if eff is None or exp is None:
        add("effective_before_expiration", None, "effective or expiration date not extracted", ["effective_date", "expiration_date"])
        add("term_at_most_12_months", None, "effective or expiration date not extracted", ["effective_date", "expiration_date"])
    else:
        add("effective_before_expiration", eff < exp, f"effective {eff.isoformat()} {'<' if eff < exp else '>='} expiration {exp.isoformat()}", ["effective_date", "expiration_date"])
        days = (exp - eff).days
        add("term_at_most_12_months", 0 < days <= 366 + 7, f"policy term {days} days (max 12 months + 7-day tolerance)", ["effective_date", "expiration_date"])

    year_match = re.search(r"\b(19[89]\d|20[0-4]\d)\b", str(vehicle or ""))
    if not (isinstance(vin, str) and year_match):
        add("vin_year_matches_vehicle", None, "VIN or vehicle year not extracted", ["vin", "vehicle_year_make_model"])
    else:
        years = vin_model_years(vin)
        vehicle_year = int(year_match.group(1))
        ok = vehicle_year in years
        add("vin_year_matches_vehicle", ok, f"VIN year code '{vin[9]}' allows {years}; vehicle year {vehicle_year}", ["vin", "vehicle_year_make_model"])
    return rules


def attach_plausibility(fields: list[dict], rules: list[dict]) -> list[dict]:
    """Mark fields named by a failed rule as ``plausibility_violation``; a passed
    rule that names a field raises its ``verification_confidence`` to 1.0 (a
    check applied and held); a failed one drops it to 0.0."""
    by_name = {f["name"]: f for f in fields}
    for rule in rules:
        if rule.get("passed") is None:
            continue
        for name in rule.get("fields") or []:
            f = by_name.get(name)
            if not f or f.get("value") is None:
                continue
            if rule["passed"]:
                if not f.get("plausibility_violation") and not f.get("z3_violation"):
                    f["verification_confidence"] = 1.0
                    f["verification_source"] = f.get("verification_source") or "plausibility_rule"
            else:
                f["plausibility_violation"] = True
                f["verification_source"] = "plausibility_rule"
                f["verification_confidence"] = 0.0
                f["reason"] = f"plausibility rule '{rule['rule']}' failed: {rule.get('message')}"
    return fields


def attach_z3_violations(fields: list[dict], violations: list[dict] | None) -> list[dict]:
    """Map real Z3 violations (``{severity, category, description, node_id}``
    from services/verification) onto fields: by source node id, or by the
    field's raw value appearing in the description. V1 field_node_mapping is
    one primary node per field (spec §4). Unmatched violations stay
    document-level; nothing is invented to make them stick."""
    for f in fields:
        if f.get("value") is None:
            # An absent field's ``field_source_node_id`` is the anchor it was
            # searched from (attach_absent_evidence), not a node it came from;
            # a violation on that node is not a violation of this field.
            continue
        hits = []
        for v in violations or []:
            if not isinstance(v, dict):
                continue
            node_id = str(v.get("node_id") or "")
            desc = str(v.get("description") or v.get("message") or "")
            if node_id and f.get("field_source_node_id") and node_id == f["field_source_node_id"]:
                hits.append(v)
            elif f.get("raw") and len(str(f["raw"])) >= 3 and str(f["raw"]) in desc:
                hits.append(v)
        if hits:
            f["z3_violation"] = True
            f["verification_source"] = "z3"
            f["verification_confidence"] = 0.0
            f["z3_violations"] = hits
            f["reason"] = f"Z3 violation: {str(hits[0].get('description') or '')[:160]}"
            if f.get("value") is not None:
                conf, basis = quality_weighted_confidence(
                    parser_confidence=None, parser_name=None, page_quality=None, z3_violation=True,
                )
                # Re-derive with the penalty applied to the already-computed product
                # so the basis string keeps the original factors.
                f["extraction_confidence"] = round(float(f["extraction_confidence"]) * Z3_VIOLATION_PENALTY, 4)
                f["confidence_basis"] = f"{str(f['confidence_basis']).rsplit(' = ', 1)[0]} × z3_penalty ({Z3_VIOLATION_PENALTY:.2f}) = {f['extraction_confidence']:.2f}"
    return fields


# --------------------------------------------------------------------------
# Decision policy (spec §5)
# --------------------------------------------------------------------------

def attach_verification_confidence(fields: list[dict], verification: dict | None) -> list[dict]:
    """``verification_confidence`` only from evidence (customer, 2026-09-27:
    "a fixed-looking verification score conceals uncertainty").

    A field a rule or Z3 already spoke about (``verification_source`` set)
    keeps that answer. Otherwise: a value on a document whose Z3 pass ran
    (``z3_status`` PASS or VIOLATION) and attached nothing to this field gets
    the spec's V1 document-level default (0.85) with a basis that says so;
    a value with no verification run at all gets ``None`` — and rule 1 cannot
    accept it; a field with no value gets ``None`` (nothing to verify).
    """
    status = ""
    if isinstance(verification, dict):
        z3 = verification.get("z3") if isinstance(verification.get("z3"), dict) else {}
        status = str(verification.get("z3_status") or z3.get("z3_status") or "").upper()
    ran = status in ("PASS", "VIOLATION")
    for f in fields:
        if f.get("evidence_state") == "schema_mismatch":
            continue
        if f.get("value") is None:
            f["verification_confidence"] = None
            f["verification_basis"] = "nothing to verify: no value"
            continue
        if f.get("verification_source"):
            f.setdefault("verification_basis", f"{f['verification_source']} result")
            continue
        if ran:
            f["verification_confidence"] = DEFAULT_VERIFICATION_CONFIDENCE
            f["verification_basis"] = f"document-level Z3 {status}: no violation attached to this field (V1 default {DEFAULT_VERIFICATION_CONFIDENCE})"
        else:
            f["verification_confidence"] = None
            f["verification_basis"] = f"no verification ran on this document (z3_status {status or 'none'})"
    return fields


def apply_decision_policy(field: dict) -> dict:
    """The V1 rules, in order, on one field dict (mutated and returned).

    0. no value: ``not_found`` / ``field_not_found`` (recorded, not queued) —
       or ``retry_parsure`` when no searched page was readable; a
       ``found_suspect`` (debris under the label) is ``unverified`` /
       ``manual_review``; a signature field is always a review item.
    1. verification ≥ 0.8 and extraction ≥ 0.75, not compliance-bound, no
       violation, page quality not low → ``accepted`` / ``none``.
    2. otherwise → ``unverified`` / ``manual_review`` (the reason names the
       failing factor; ``verification_confidence`` None reads "not verified").
    3. a Z3 violation (or its plausibility/VIN equivalent) → ``rejected`` /
       ``compliance_review`` — checked first because it overrides both.

    A ``disputed`` field is left alone: the dispute workflow owns it until
    resolution. ``partial`` is in the vocabulary but no V1 rule produces it.
    Rule 0 and the None handling date from 2026-09-27 (customer handoff:
    not-found is not review; no default verification confidence).
    """
    if field.get("field_state") == "disputed":
        return field
    if field.get("evidence_state") == "schema_mismatch":
        # mark_schema_mismatch settled these: no confidence, no routing.
        return field
    violation = bool(field.get("z3_violation") or field.get("plausibility_violation"))
    raw_vc = field.get("verification_confidence")
    vc = float(raw_vc) if raw_vc is not None else None
    ec = float(field.get("extraction_confidence") or 0.0)
    compliance = bool(field.get("compliance_bound"))
    if field.get("value") is None and field.get("field_type") != "signature":
        field["verification_confidence"] = None
        field["verification_basis"] = "nothing to verify: no value"
        if field.get("evidence_state") == "found_suspect":
            field["field_state"], field["routing_action"] = "unverified", "manual_review"
            field["review_required"], field["policy_rule"] = True, 2
        elif field.get("evidence_state") == "unreadable":
            field["field_state"], field["routing_action"] = "not_found", "retry_parsure"
            field["review_required"], field["policy_rule"] = True, 0
            field["reason"] = field.get("reason") or EVIDENCE_REASONS["unreadable"]
        else:
            field["field_state"], field["routing_action"] = "not_found", "field_not_found"
            field["review_required"], field["policy_rule"] = False, 0
            if field.get("reason") in (None, "field not found"):
                field["reason"] = EVIDENCE_REASONS["not_on_document"]
        assert field["field_state"] in FIELD_STATES and field["routing_action"] in ROUTING_ACTIONS
        return field
    low_page = bool(field.get("low_quality_page"))
    if violation:
        field["field_state"], field["routing_action"] = "rejected", "compliance_review"
        field["review_required"] = True
        field["policy_rule"] = 3
    elif field.get("value") is not None and vc is not None and vc >= VERIFICATION_THRESHOLD and ec >= EXTRACTION_THRESHOLD and not compliance and not low_page:
        field["field_state"], field["routing_action"] = "accepted", "none"
        field["review_required"] = False
        field["policy_rule"] = 1
    else:
        field["field_state"], field["routing_action"] = "unverified", "manual_review"
        field["review_required"] = True
        field["policy_rule"] = 2
        if not field.get("reason"):
            if field.get("value") is None:
                field["reason"] = "signature missing — manual review required"
            elif compliance:
                field["reason"] = "compliance-bound field — human confirmation required"
            elif low_page:
                field["reason"] = f"page quality below {LOW_QUALITY_PAGE:.2f} — confirm against the image"
            elif vc is None:
                field["reason"] = "not verified — no verification ran on this document"
            elif ec < EXTRACTION_THRESHOLD:
                field["reason"] = f"extraction_confidence {ec:.2f} < {EXTRACTION_THRESHOLD}"
            else:
                field["reason"] = f"verification_confidence {vc:.2f} < {VERIFICATION_THRESHOLD}"
    quality_flag = (field.get("number_quality") or {}).get("review_required") or (field.get("signature_quality") or {}).get("review_required")
    if quality_flag and field["field_state"] == "accepted":
        # Spec §6: a flagged number/signature always needs eyes, even when the
        # arithmetic would accept it.
        field["field_state"], field["routing_action"] = "unverified", "manual_review"
        field["review_required"] = True
        field["policy_rule"] = 2
    if field.get("value") is not None:
        field["evidence_state"] = "found_verified" if field["field_state"] == "accepted" else "found_unverified"
    assert field["field_state"] in FIELD_STATES and field["routing_action"] in ROUTING_ACTIONS
    assert field.get("evidence_state") in (None, *EVIDENCE_STATES)
    return field


# --------------------------------------------------------------------------
# Cross-document conflicts (spec §9 item 16)
# --------------------------------------------------------------------------

SHARED_FIELDS = ("policy_number", "insured_name", "vin")
#: One party-name key across document types (2026-09-27, Red-Hat conflict
#: recall): a policy names the *insured*, a claim names the *claimant*; the
#: customer's policy + claim pair disagreed on the person and no conflict was
#: raised because ``insured_name`` was compared only with ``insured_name``.
PARTY_NAME_FIELDS = ("insured_name", "claimant_name")
#: The keys compared across a project's reports; each is the tuple of field
#: names that carry the same fact. The conflict entry's ``field`` is the one
#: name when every value came from the same field, else the key's first name,
#: and ``fields`` lists every name that contributed.
CONFLICT_KEYS: tuple[tuple[str, ...], ...] = (("policy_number",), PARTY_NAME_FIELDS, ("vin",))


def _norm(value: Any) -> str:
    return re.sub(r"[\s\-]+", "", str(value)).upper()


def cross_document_conflicts(reports: list[dict]) -> list[dict[str, Any]]:
    """Differing non-null values of a shared key across a project's reports.

    Whitespace/hyphen/case differences are not conflicts. A conflict is
    reported for the reviewer; it never auto-disputes any field (spec §9 16).
    ``insured_name`` and ``claimant_name`` are one key (``PARTY_NAME_FIELDS``):
    the entry carries ``fields`` (every field name that contributed) and
    ``report_ids`` (every report that contributed) beside ``values``.
    """
    conflicts: list[dict[str, Any]] = []
    for names in CONFLICT_KEYS:
        seen: list[dict[str, Any]] = []
        for report in reports:
            for f in report.get("fields") or []:
                if f.get("name") in names and f.get("value") not in (None, "") and f.get("evidence_state") != "schema_mismatch":
                    seen.append({"report_id": report.get("report_id"), "document_id": report.get("document_id"),
                                 "field": f.get("name"), "value": f["value"]})
        distinct = {_norm(s["value"]) for s in seen}
        if len(distinct) > 1:
            fields_seen = sorted({str(s["field"]) for s in seen})
            conflicts.append({
                "field": fields_seen[0] if len(fields_seen) == 1 else names[0],
                "fields": fields_seen,
                "kind": "cross_document",
                "report_ids": sorted({str(s["report_id"]) for s in seen if s.get("report_id") is not None}),
                "values": seen,
            })
    return conflicts


# --------------------------------------------------------------------------
# Schema registry bootstrap (2026-09-27, plan Part 4.1)
# --------------------------------------------------------------------------

#: Snapshots of the static ``_f()`` taxonomy above, taken before
#: ``schema_registry.reload`` rebinds the public names. The registry reads the
#: built-ins from these, so a ``reload()`` after a runtime schema was dropped
#: into ``ASSURE_SCHEMA_DIR`` (or removed) always starts from the same base.
_BUILTIN_DOCUMENT_TYPES: tuple[str, ...] = tuple(DOCUMENT_TYPES)
_BUILTIN_TYPE_FAMILY: dict[str, str] = dict(TYPE_FAMILY)
_BUILTIN_TYPE_KEYWORDS: dict[str, tuple[str, ...]] = {t: tuple(k) for t, k in TYPE_KEYWORDS.items()}
_BUILTIN_FIELD_TAXONOMY: dict[str, list[FieldSpec]] = {t: list(specs) for t, specs in FIELD_TAXONOMY.items()}


def _bootstrap_schema_registry() -> None:
    """Merge the runtime schemas (``prompt_matrix/schemas/*.json``,
    ``ASSURE_SCHEMA_DIR``) into ``DOCUMENT_TYPES`` / ``TYPE_FAMILY`` /
    ``TYPE_KEYWORDS`` / ``FIELD_TAXONOMY`` at import. The registry never
    raises on a bad schema file (it logs and skips), and this guard makes
    sure a bug in the registry itself leaves the static taxonomy bound
    rather than failing the import of the extractor."""
    try:
        try:
            from . import schema_registry
        except ImportError:  # pragma: no cover - flat-import fallback
            import schema_registry  # type: ignore
        schema_registry.reload()
    except Exception:  # noqa: BLE001 — the static taxonomy is the fallback
        import logging

        logging.getLogger(__name__).exception("schema registry bootstrap failed; static taxonomy in effect")


_bootstrap_schema_registry()
