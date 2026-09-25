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
  as a model probability.
* Extraction is label-anchored regex per page (``extract_fields``). A field
  whose label or value is not found is ``value=None``,
  ``extraction_confidence=0.0``, ``review_required=True``,
  ``reason="field not found"``. Nothing is inferred from context.
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

import re
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import date
from typing import Any

# --------------------------------------------------------------------------
# Vocabularies (spec §5) — the two never mix.
# --------------------------------------------------------------------------

FIELD_STATES = ("accepted", "partial", "unverified", "disputed", "rejected")
ROUTING_ACTIONS = ("none", "manual_review", "adjudicator_queue", "compliance_review", "retry_parsure", "replay_later")

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
)

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
}

#: Fewer matched keywords than this and the heuristic will not name a type.
MIN_KEYWORD_MATCHES = 3


def _keyword_hits(text_lower: str, keywords: tuple[str, ...]) -> list[str]:
    hits: list[str] = []
    for kw in keywords:
        pattern = r"(?<![a-z0-9])" + re.escape(kw).replace(r"\ ", r"\s+") + r"(?![a-z0-9])"
        if re.search(pattern, text_lower):
            hits.append(kw)
    return hits


def classify_document(text: str) -> dict[str, Any]:
    """Keyword-heuristic document type with an ``uncertain`` fallback.

    ``confidence`` is the share of the winning type's keywords that occur in
    the text, capped at ``CLASSIFICATION_CAP`` (0.9): a keyword count is not a
    calibrated probability and must never present as one. Fewer than
    ``MIN_KEYWORD_MATCHES`` hits, or a tie between two types, is
    ``"uncertain"`` — the reviewer picks (classification override route).
    """
    text_lower = (text or "").lower()
    if not text_lower.strip():
        return {"document_type": "uncertain", "confidence": 0.0, "basis": "no text to classify", "matched_keywords": []}
    scored: list[tuple[str, list[str]]] = []
    for doc_type in DOCUMENT_TYPES:
        hits = _keyword_hits(text_lower, TYPE_KEYWORDS[doc_type])
        scored.append((doc_type, hits))
    scored.sort(key=lambda item: len(item[1]), reverse=True)
    best_type, best_hits = scored[0]
    second_hits = scored[1][1] if len(scored) > 1 else []
    ratio = len(best_hits) / max(1, len(TYPE_KEYWORDS[best_type]))
    confidence = round(min(CLASSIFICATION_CAP, ratio), 3)
    if len(best_hits) < MIN_KEYWORD_MATCHES:
        return {
            "document_type": "uncertain",
            "confidence": confidence,
            "basis": f"only {len(best_hits)} keyword(s) matched for {best_type}; below minimum {MIN_KEYWORD_MATCHES}",
            "matched_keywords": best_hits,
        }
    if len(second_hits) == len(best_hits):
        return {
            "document_type": "uncertain",
            "confidence": confidence,
            "basis": f"tie between {best_type} and {scored[1][0]} ({len(best_hits)} keywords each)",
            "matched_keywords": best_hits,
        }
    return {
        "document_type": best_type,
        "confidence": confidence,
        "basis": (
            f"keyword heuristic: {len(best_hits)}/{len(TYPE_KEYWORDS[best_type])} {best_type} keywords matched "
            f"(next: {scored[1][0]} {len(second_hits)}); capped at {CLASSIFICATION_CAP}"
        ),
        "matched_keywords": best_hits,
    }


# --------------------------------------------------------------------------
# Field taxonomy
# --------------------------------------------------------------------------

FIELD_TYPES = ("text", "number", "money", "date", "vin", "name", "signature")


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
}

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


def _clean_text_value(raw: str) -> str:
    """Trim a rest-of-line capture at the next inline label (two+ spaces, a tab, or a pipe)."""
    value = re.split(r"\s{2,}|\t|\s\|\s", raw.strip(), maxsplit=1)[0]
    value = re.sub(r"\s+", " ", value).strip(" :;,-–—")
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
    "unreadable": 0.0, "unknown": 1.0,
}
#: Spec §6 "Faint Signatures": text-only signals cannot see ink, so every
#: quality other than ``clear`` carries a penalty and forces review.
SIGNATURE_PENALTIES = {
    "clear": 1.0, "faint": 0.8, "incomplete": 0.7, "stamped": 0.7, "questionable": 0.6, "missing": 0.5, "unknown": 1.0,
}


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
            return dict(qp.assess_signature(page_text, visual=visual, ocr_lines=ocr_lines, page=page))
        except Exception:
            pass
    text = page_text or ""
    m = _SIG_LABEL_RE.search(text)
    if not m:
        return {"present": False, "quality": "missing", "review_required": True, "basis": "no signature label on page", "page": page}
    tail = text[m.end(): m.end() + 80]
    tail_line = tail.split("\n", 1)[0]
    after = re.sub(r"^[\s:\-–—]+", "", tail_line)
    lower_tail = tail.lower()
    if re.search(r"stamp", lower_tail):
        return {"present": True, "quality": "stamped", "review_required": True, "basis": "stamp wording after signature label", "page": page}
    if re.search(r"/s/|electronically\s+signed|e-?signed|digitally\s+signed", text[max(0, m.start() - 40): m.end() + 80], re.I):
        return {"present": True, "quality": "clear", "review_required": False, "basis": "explicit e-signature marker (/s/ or electronically signed)", "page": page}
    if not after or re.fullmatch(r"[_\s.]*", after):
        return {"present": False, "quality": "missing", "review_required": True, "basis": "signature line is blank", "page": page}
    if re.fullmatch(r"[A-Za-z.'\- ]{2,}", after.strip()) and len(after.strip().split()) >= 2:
        return {"present": True, "quality": "questionable", "review_required": True, "basis": "name text after signature label; ink quality not assessable from text", "page": page}
    return {"present": True, "quality": "questionable", "review_required": True, "basis": "text after signature label; ink quality not assessable from text", "page": page}


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
) -> tuple[float, str]:
    """``extraction_confidence`` and the ``confidence_basis`` that spells it out.

    Delegates to ``quality_probe.quality_weighted_confidence`` when present;
    otherwise the spec §4 product with the spec's defaults. When nothing but
    defaults would enter the product, the answer is the flat 0.50 with the
    "no_signal_available" basis — a default multiplied by a default is not a
    measurement.
    """
    qp = _quality_probe()
    if qp is not None and hasattr(qp, "quality_weighted_confidence"):
        try:
            value, basis = qp.quality_weighted_confidence(
                parser_confidence=parser_confidence, parser_name=parser_name, page_quality=page_quality,
                number_quality=number_quality, z3_violation=z3_violation, signature_quality=signature_quality,
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
    parts.append(f"page_quality ({pq:.2f}{'' if page_quality is not None else ', neutral default'})")
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


def page_layout(bundle: dict) -> list[list[dict]]:
    """Per page, the text segments in reading order with their provenance.

    Each segment: ``{"start", "end", "text", "node_id", "bbox"}`` where
    ``start``/``end`` are offsets into that page's text as ``page_texts``
    returns it (segments joined by ``"\\n"``). Handles the three shapes the
    ingest produces: jdf-cli ``pages[].elements[]`` (``content``/``text`` +
    ``position``; OCR ``ocr.blocks``), the Assure tree ``body[].children[]``
    (paragraph ``content`` with node ids, page from ``meta.source_page``),
    and, failing both, the bundle's ``chunks`` grouped by ``page`` or the
    flat ``text`` split on form feeds.
    """
    jdf = bundle.get("jdf") if isinstance(bundle, dict) else None
    layouts: list[list[dict]] = []

    def _append(segments: list[dict]) -> None:
        layouts.append(segments)

    def _segments_from(items: list[tuple[str, str | None, list[float] | None]]) -> list[dict]:
        segs: list[dict] = []
        cursor = 0
        for text, node_id, bbox in items:
            text = text.rstrip("\n")
            if not text.strip():
                continue
            segs.append({"start": cursor, "end": cursor + len(text), "text": text, "node_id": node_id, "bbox": bbox})
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
                items.append((text, str(node_id) if node_id else None, _element_bbox(el, page)))
            _append(_segments_from(items))
        if any(layouts):
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
                    cells = [str(c) for row in node.get("rows") or [] for c in row]
                    text = "\n".join([" | ".join(str(h) for h in node.get("headers") or [])] + [" | ".join(str(c) for c in row) for row in node.get("rows") or []]) if cells else str(node.get("caption") or "")
                else:
                    text = str(node.get("content") or node.get("title") or node.get("alt") or "")
                if text.strip():
                    items.append((text, str(node.get("id")) if node.get("id") else None, None))
                stack = list(node.get("children") or []) + stack
            _append(_segments_from(items))
        if layouts:
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
            by_page.setdefault(page_no, []).append((str(chunk.get("text") or chunk.get("content") or ""), str(chunk.get("id")) if chunk.get("id") else None, None))
        page_count = max(int(bundle.get("page_count") or 1), max(by_page) if by_page else 1)
        for page_no in range(1, page_count + 1):
            _append(_segments_from(by_page.get(page_no, [])))
        return layouts

    text = str(bundle.get("text") or "") if isinstance(bundle, dict) else ""
    pages = text.split("\f") if "\f" in text else [text]
    for page_text in pages:
        _append(_segments_from([(page_text, None, None)]))
    return layouts


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
        "money": _MONEY_RE, "number": _NUMBER_RE, "date": _DATE_RE, "vin": _VIN_RE,
    }.get(field_type, _TEXT_RE)


_LABEL_LIKE = re.compile(r"^[A-Za-z][A-Za-z /]{1,30}:")


def _find_field(spec: FieldSpec, text: str) -> tuple[str, int, int] | None:
    """(raw value, start, end) of the first anchor+value hit in ``text``, or None.

    The anchor must end at a word boundary (``(?!'|[a-z])`` — "Buyer's
    Signature" is not the buyer's name) and the separator may cross at most
    one line break, so a label whose value sits on the next line is read but a
    label followed by *another* label is not.
    """
    for anchor in spec.anchors:
        pattern = re.compile(
            r"(?<![a-z])" + anchor + r"(?!'|[a-z])" + _SEP + r"(?P<val>" + _value_pattern(spec.field_type) + r")",
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
                end = start + len(raw)
            else:
                raw = raw.strip()
            if spec.field_type == "name" and (len(raw) > 80 or re.search(r"\d{3,}", raw)):
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
        "verification_confidence": DEFAULT_VERIFICATION_CONFIDENCE,
        "provenance_confidence": 0.0,
        "signature_quality": None,
        "number_quality": None,
        "source_span": None,
        "field_source_node_id": None,
        "field_state": "unverified",
        "routing_action": "manual_review",
        "review_required": True,
        "reason": reason,
        "z3_violation": False,
        "plausibility_violation": False,
        "verification_source": None,
        "compliance_bound": spec.compliance_bound,
        "corrected": False,
    }


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
            results.append(_empty_field(spec))
            continue
        page_index, raw, start, end = hit
        field = _empty_field(spec)
        field["raw"] = raw
        value: Any = raw
        if spec.field_type == "money":
            value = _parse_money(raw)
        elif spec.field_type == "number":
            value = _parse_number(raw)
        elif spec.field_type == "date":
            value = _parse_date(raw)
        elif spec.field_type == "vin":
            value = raw.upper()
        if value is None:
            field["reason"] = f"{spec.field_type} value could not be parsed from '{raw[:40]}' — manual review required"
            field["confidence_basis"] = field["reason"]
            results.append(field)
            continue
        field["value"] = value
        pq = page_quality[page_index] if page_index < len(page_quality) else None
        visual = visual_pages[page_index] if page_index < len(visual_pages) else None
        handwritten = bool(visual and "handwritten" in (visual.get("flags") or []))
        segments = layout[page_index] if page_index < len(layout) else []
        seg = _segment_at(segments, start)
        span: dict[str, Any] = {"page": page_index + 1, "span_type": "text_range", "start_char": start, "end_char": end}
        if seg and seg.get("bbox"):
            span = {"page": page_index + 1, "span_type": "bbox_relative", "bbox": seg["bbox"], "start_char": start, "end_char": end}
        field["source_span"] = span
        field["field_source_node_id"] = seg.get("node_id") if seg else None
        field["provenance_confidence"] = 1.0
        number_quality = None
        if spec.field_type in ("money", "number", "vin"):
            nq = assess_number(raw, ocr_confidence=ocr_confidence, page_quality=pq, handwritten=handwritten)
            field["number_quality"] = nq
            number_quality = nq.get("quality")
        conf, basis = quality_weighted_confidence(
            parser_confidence=parse_confidence, parser_name=parser_name, page_quality=pq, number_quality=number_quality,
        )
        field["extraction_confidence"] = conf
        field["confidence_basis"] = basis
        field["reason"] = None
        if spec.field_type == "vin":
            check = validate_vin(value)
            field["vin_check"] = check
            if not check["valid"]:
                field["plausibility_violation"] = True
                field["verification_source"] = "vin_check"
                field["reason"] = f"VIN rejected: {check['reason']}"
        if field["number_quality"] and field["number_quality"].get("review_required"):
            field["reason"] = field["reason"] or f"number_quality {field['number_quality']['quality']}: {field['number_quality'].get('basis', '')}"
        results.append(field)
    return results


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
    quality = str(sig.get("quality") or "questionable")
    field["value"] = "present"
    field["raw"] = hit[1] if hit is not None else None
    pq = page_quality[page_index] if page_index < len(page_quality) else None
    if hit is not None:
        _, _, start, end = hit
        segments = layout[page_index] if page_index < len(layout) else []
        seg = _segment_at(segments, start)
        span: dict[str, Any] = {"page": page_index + 1, "span_type": "text_range", "start_char": start, "end_char": end}
        if seg and seg.get("bbox"):
            span = {"page": page_index + 1, "span_type": "bbox_relative", "bbox": seg["bbox"], "start_char": start, "end_char": end}
        field["source_span"] = span
        field["field_source_node_id"] = seg.get("node_id") if seg else None
        field["provenance_confidence"] = 1.0
    conf, basis = quality_weighted_confidence(parser_confidence=parse_confidence, parser_name=parser_name, page_quality=pq, signature_quality=quality)
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

def apply_decision_policy(field: dict) -> dict:
    """The three V1 rules, in order, on one field dict (mutated and returned).

    1. verification ≥ 0.8 and extraction ≥ 0.75 and not compliance-bound and
       no violation → ``accepted`` / ``none``.
    2. otherwise → ``unverified`` / ``manual_review``.
    3. a Z3 violation (or its plausibility/VIN equivalent) → ``rejected`` /
       ``compliance_review`` — checked first because it overrides both.

    A ``disputed`` field is left alone: the dispute workflow owns it until
    resolution. ``partial`` is in the vocabulary but no V1 rule produces it.
    """
    if field.get("field_state") == "disputed":
        return field
    violation = bool(field.get("z3_violation") or field.get("plausibility_violation"))
    vc = float(field.get("verification_confidence") if field.get("verification_confidence") is not None else DEFAULT_VERIFICATION_CONFIDENCE)
    ec = float(field.get("extraction_confidence") or 0.0)
    compliance = bool(field.get("compliance_bound"))
    if violation:
        field["field_state"], field["routing_action"] = "rejected", "compliance_review"
        field["review_required"] = True
        field["policy_rule"] = 3
    elif field.get("value") is not None and vc >= VERIFICATION_THRESHOLD and ec >= EXTRACTION_THRESHOLD and not compliance:
        field["field_state"], field["routing_action"] = "accepted", "none"
        field["review_required"] = False
        field["policy_rule"] = 1
    else:
        field["field_state"], field["routing_action"] = "unverified", "manual_review"
        field["review_required"] = True
        field["policy_rule"] = 2
        if not field.get("reason"):
            if field.get("value") is None:
                field["reason"] = "field not found"
            elif compliance:
                field["reason"] = "compliance-bound field — human confirmation required"
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
    assert field["field_state"] in FIELD_STATES and field["routing_action"] in ROUTING_ACTIONS
    return field


# --------------------------------------------------------------------------
# Cross-document conflicts (spec §9 item 16)
# --------------------------------------------------------------------------

SHARED_FIELDS = ("policy_number", "insured_name", "vin")


def _norm(value: Any) -> str:
    return re.sub(r"[\s\-]+", "", str(value)).upper()


def cross_document_conflicts(reports: list[dict]) -> list[dict[str, Any]]:
    """Differing non-null values of a shared field across a project's reports.

    Whitespace/hyphen/case differences are not conflicts. A conflict is
    reported for the reviewer; it never auto-disputes any field (spec §9 16).
    """
    conflicts: list[dict[str, Any]] = []
    for name in SHARED_FIELDS:
        seen: list[dict[str, Any]] = []
        for report in reports:
            for f in report.get("fields") or []:
                if f.get("name") == name and f.get("value") not in (None, ""):
                    seen.append({"report_id": report.get("report_id"), "document_id": report.get("document_id"), "value": f["value"]})
        distinct = {_norm(s["value"]) for s in seen}
        if len(distinct) > 1:
            conflicts.append({"field": name, "kind": "cross_document", "values": seen})
    return conflicts
