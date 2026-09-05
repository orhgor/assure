"""Local adversarial checks. No extra model call.

Looks for injection phrasing, PII-shaped spans, and citation risk already
handled by the citation critic. This is a detector, not an attack kit.
"""

from __future__ import annotations

import re

try:
    from .critique import run_critique
    from .rule_critic import rule_based_critique
except ImportError:
    from critique import run_critique
    from rule_critic import rule_based_critique

_INJECTION = (
    re.compile(r"ignore (all )?(previous|prior|above) instructions", re.I),
    re.compile(r"disregard (the )?(system|developer) prompt", re.I),
    re.compile(r"you are now (jailbroken|unrestricted|dan)\b", re.I),
    re.compile(r"reveal (your )?(system prompt|hidden instructions)", re.I),
)

_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_PHONE = re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?){2}\d{4}\b")
_KEYISH = re.compile(r"\b(?:sk|pk|api)[-_]?[A-Za-z0-9]{16,}\b")
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")


def scan_text(text: str, *, context: str = "") -> dict:
    blob = text or ""
    errors: list[str] = []
    warnings: list[str] = []
    for pat in _INJECTION:
        if pat.search(blob):
            errors.append("Prompt-injection phrasing found.")
            break
    if _EMAIL.search(blob):
        warnings.append("Email-shaped text found.")
    if _PHONE.search(blob) and len(re.findall(r"\d", blob)) >= 10:
        warnings.append("Phone-shaped number found.")
    if _KEYISH.search(blob):
        errors.append("API-key-shaped token found.")
    if _SSN.search(blob):
        errors.append("SSN-shaped number found.")
    structural = rule_based_critique(blob)
    errors.extend(structural.get("errors") or [])
    warnings.extend(structural.get("warnings") or [])
    critique = run_critique(blob, context)
    flagged = list(critique.get("flagged_lines") or [])
    if flagged:
        warnings.append(f"{len(flagged)} citation-like line(s) not in the attached files.")
    ok = not errors
    return {
        "ok": ok,
        "errors": errors,
        "warnings": warnings,
        "flagged_count": len(flagged),
        "injection": any(pat.search(blob) for pat in _INJECTION),
        "pii": bool(_EMAIL.search(blob) or _SSN.search(blob) or _KEYISH.search(blob)),
    }


def scan_prompt_and_reply(prompt: str, reply: str | None = None, *, context: str = "") -> dict:
    prompt_scan = scan_text(prompt, context=context)
    reply_scan = (
        scan_text(reply or "", context=context)
        if reply
        else {
            "ok": True,
            "errors": [],
            "warnings": [],
            "flagged_count": 0,
            "injection": False,
            "pii": False,
        }
    )
    errors = list(prompt_scan["errors"]) + [f"reply: {item}" for item in reply_scan["errors"]]
    warnings = list(prompt_scan["warnings"]) + [f"reply: {item}" for item in reply_scan["warnings"]]
    return {
        "ok": not errors,
        "prompt": prompt_scan,
        "reply": reply_scan,
        "errors": errors,
        "warnings": warnings,
    }
