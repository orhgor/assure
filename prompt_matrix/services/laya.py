"""Laya — rules-v1 triage behind the intake router (spec §2 "Laya").

Laya is a rule-based, calibrated triage layer: fixed thresholds on the
quality probe's per-page flags (``services/quality_probe`` constants), nothing
learned and nothing statistical — "rules-v1, thresholds from quality_probe
constants" is the whole model. It answers the router's three questions from
the spec: which backend the visual evidence suggests, whether to escalate,
and whether a human should look before extraction is trusted.

Laya never overrides ``parser_router.select_parser``. It *annotates* the
router's decision with a suggestion, an escalation bit and the reasons, so a
consumer (Parsure, the review shell) can gate human review. It is called from
``parser_router.route_intake`` and nowhere else (spec §8 rule 8: Laya is
behind routing, not the router; spec §8 rule 1: one router).

Rules (V1):

- text material → ``text_wrap`` (no probe applies).
- no renderable page → escalate + human review (quality is unknowable).
- camera photo → the scan backend (spec §6 "Low-Resolution Photos": photos
  are images, not digital PDFs, whatever their resolution).
- any page ``blurry`` / ``low_res`` / ``low_contrast`` → the scan backend
  (spec §6 "Bad Scans").
- ≥ ``ESCALATE_FLAGGED_FRACTION`` of probed pages flagged → escalate +
  human review, suggested route ``human_review``.
"""

from __future__ import annotations

from typing import Any

MODEL = "rules-v1"
POLICY_VERSION = "v1"

#: Share of probed pages that must carry a quality flag before Laya escalates
#: to human review. Half: one bad page in a clean bundle is a page problem,
#: half or more is a document problem. Initial calibration value.
ESCALATE_FLAGGED_FRACTION = 0.5

_ROUTES = ("jdf", "jdf-ocr", "textract", "text_wrap", "human_review")


def _scan_backend() -> str:
    # Function-level import: parser_router imports this module.
    try:
        from .parser_router import scan_backend
    except ImportError:
        from services.parser_router import scan_backend
    return scan_backend()


def triage(
    *,
    material_type: str | None,
    modality: str | None,
    visual_pages: list[dict[str, Any]] | None,
    parser: str,
) -> dict[str, Any]:
    """Rule-based triage of one intake; see the module docstring for the rules.

    ``parser`` is the router's decision and the default suggestion: Laya only
    moves away from it when a rule fires, and every move is listed in
    ``reasons``.
    """
    pages = [p for p in (visual_pages or []) if isinstance(p, dict)]
    flagged = [p for p in pages if p.get("flags")]
    probed = len(pages)
    reasons: list[str] = []
    suggested = parser if parser in _ROUTES else "jdf"
    escalate = False
    human_review = False

    if material_type == "text_file" or modality == "text":
        reasons.append("text material: wrapped directly as a document, no visual probe applies")
        suggested = "text_wrap"
        return _result(suggested, escalate, human_review, reasons, probed, 0)

    if probed == 0:
        reasons.append("no renderable page: visual quality cannot be measured, so routing is not trusted")
        return _result("human_review", True, True, reasons, probed, 0)

    if material_type == "photo" or modality == "phone_photo":
        suggested = _scan_backend()
        reasons.append(f"camera photo: images have no text layer, scan backend ({suggested}) regardless of resolution")

    if flagged:
        counts: dict[str, int] = {}
        for page in flagged:
            for flag in page.get("flags") or []:
                counts[flag] = counts.get(flag, 0) + 1
        summary = ", ".join(f"{flag} x{n}" for flag, n in sorted(counts.items()))
        suggested = _scan_backend()
        reasons.append(f"{len(flagged)}/{probed} probed pages flagged ({summary}): scan backend ({suggested})")
        if len(flagged) / probed >= ESCALATE_FLAGGED_FRACTION:
            escalate = True
            human_review = True
            suggested = "human_review"
            reasons.append(
                f"{len(flagged)}/{probed} ≥ {ESCALATE_FLAGGED_FRACTION:.0%} of pages flagged: human review before extraction is trusted"
            )
    elif parser == "jdf":
        reasons.append("no page carries a quality flag; router decision jdf stands")
    else:
        reasons.append(f"no page carries a quality flag; router decision {parser} stands")

    return _result(suggested, escalate, human_review, reasons, probed, len(flagged))


def _result(
    suggested: str, escalate: bool, human_review: bool, reasons: list[str], probed: int, flagged: int
) -> dict[str, Any]:
    return {
        "model": MODEL,
        "policy_version": POLICY_VERSION,
        "suggested_route": suggested,
        "escalate": escalate,
        "human_review": human_review,
        "reasons": reasons,
        "probed_pages": probed,
        "flagged_pages": flagged,
    }
