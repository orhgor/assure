"""Zero-click Z3 lock candidate extraction from substrate text."""

from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

try:
    from ..keys import key_present, load_keys
    from ..litellm_runner import call_model
    from ..upload_limits import pdf_has_visual_content
except ImportError:
    from keys import key_present, load_keys
    from litellm_runner import call_model
    from upload_limits import pdf_has_visual_content

MIN_CONFIDENCE = 0.7
TEXT_MODEL = "deepseek/deepseek-chat"
VISION_MODEL = "gemini/gemini-3.6-flash"

_log = logging.getLogger(__name__)

_SYSTEM = (
    "You extract numerical and factual assertions for a truth ledger. "
    'Respond with JSON only: {"candidates": [...]}.'
)

#: Both templates ask for one line of JSON. A memo of coverage limits states ~30
#: figures, and the same extraction pretty-printed costs ~250 characters per
#: candidate — more than the model is allowed to write, so the answer ends
#: mid-object and no candidate can be read back at all (measured on the renewal
#: memo: 6,709 characters cut at the output limit, 0 candidates parsed). Compact
#: JSON fits the same 30 candidates in ~4.9k characters.
_USER_TEMPLATE = """Extract all numerical and factual assertions from the text below.
Return one line of JSON with no indentation and no newlines between tokens: {{"candidates": [{{"entity": str, "metric": str, "period": str, "scenario": str, "value": number, "unit": str, "confidence": float}}]}}
Use confidence 0.0–1.0. Omit vague claims (e.g. "approx. 50%") or set confidence below 0.5.

Text:
{text}
"""

_VISION_USER_TEMPLATE = """Extract all numerical and factual assertions from this document, including values shown in charts, tables, and diagrams.
Return one line of JSON with no indentation and no newlines between tokens: {{"candidates": [{{"entity": str, "metric": str, "period": str, "scenario": str, "value": number, "unit": str, "confidence": float}}]}}
Use confidence 0.0–1.0. Omit vague claims or set confidence below 0.5.

Additional extracted text (may be incomplete):
{text}
"""


@dataclass(frozen=True)
class LockInferenceResult:
    candidates: list[dict[str, Any]]
    model: str
    has_visual_content: bool


def resolve_lock_inference_model(has_visual_content: bool) -> str:
    return VISION_MODEL if has_visual_content else TEXT_MODEL


def _canonical_key(candidate: dict[str, Any]) -> str:
    parts = [
        str(candidate.get("entity") or "Entity").strip(),
        str(candidate.get("metric") or "Metric").strip(),
        str(candidate.get("period") or "").strip(),
    ]
    slug = "_".join(p for p in parts if p)
    slug = re.sub(r"[^\w]+", "_", slug).strip("_")
    return slug or "metric"


def _coerce_candidates(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        raw = payload
    elif isinstance(payload, dict):
        raw = payload.get("candidates") or payload.get("metrics") or []
    else:
        return []

    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            confidence = float(item.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence < MIN_CONFIDENCE:
            continue
        try:
            value = float(item.get("value"))
        except (TypeError, ValueError):
            continue
        candidate = {
            "entity": str(item.get("entity") or "").strip(),
            "metric": str(item.get("metric") or "").strip(),
            "period": str(item.get("period") or "").strip(),
            "scenario": str(item.get("scenario") or "Actual").strip(),
            "value": value,
            "unit": str(item.get("unit") or "").strip(),
            "confidence": round(confidence, 3),
            "canonical_key": _canonical_key(item),
        }
        if candidate["entity"] or candidate["metric"]:
            out.append(candidate)
    return out


def _escape_newlines_in_strings(json_text: str) -> str:
    """Best-effort: escape raw newlines (inside JSON string values only)."""
    out: list[str] = []
    in_str = False
    esc = False
    for ch in json_text:
        if in_str:
            if esc:
                out.append(ch)
                esc = False
            elif ch == "\\":
                out.append(ch)
                esc = True
            elif ch == '"':
                out.append(ch)
                in_str = False
            elif ch == "\n":
                out.append("\\n")
            elif ch == "\r":
                out.append("\\r")
            else:
                out.append(ch)
        else:
            if ch == '"':
                in_str = True
            out.append(ch)
    return "".join(out)


def _extract_json_block(text: str) -> str | None:
    """Return the first balanced {…}/[…], stripping ```json fences.

    Walks char-by-char tracking quote/escape state and brace/bracket depth,
    so raw newlines inside string values (which json.JSONDecodeError would
    otherwise reject) do not break the scan. Returns None if no balanced
    block is found.
    """
    s = re.sub(r"^```(?:json)?\s*$", "", str(text or ""), flags=re.MULTILINE).strip()
    if not s:
        return None
    stack: list[str] = []
    open_idx = -1
    in_str = False
    esc = False
    i = 0
    while i < len(s):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch in "{[(":
                if not stack:
                    open_idx = i
                stack.append(ch)
            elif ch in "}])":
                if stack:
                    stack.pop()
                    if not stack:
                        return s[open_idx : i + 1]
        i += 1
    return None


def _log_parse_failure(content: str) -> None:
    _log.error(
        "[LOCK_INFERENCE] JSON parse failed; raw model output (%d chars):\n%s",
        len(content or ""),
        (content or "")[:500],
    )


def _recover_complete_candidates(text: str) -> list[dict[str, Any]]:
    """The candidates an unreadable answer still carries, taken object by object.

    ``_extract_json_block`` needs the answer's braces to balance, so a reply the
    model's output limit cut short yields nothing and the draft reads as having no
    figures at all. The complete objects in the array are still real extractions,
    so they are read back one at a time and pass the same acceptance rules as a
    clean answer (``_coerce_candidates``: confidence floor, numeric value, a named
    metric). Nothing is inferred from the truncated object itself.
    """
    start = text.find('"candidates"')
    if start == -1:
        return []
    # Scan the array that follows the key. A '{' at array depth 0 opens a
    # candidate; the matching '}' closes it. The scan ends at the array's ']' or
    # at the end of the text, whichever comes first — the truncated tail simply
    # never closes its object.
    index = text.find("[", start)
    if index == -1:
        return []
    items: list[Any] = []
    depth = 0
    opened_at = -1
    in_string = False
    escaped = False
    while index < len(text):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                opened_at = index
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and opened_at != -1:
                try:
                    items.append(json.loads(text[opened_at : index + 1]))
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass
                opened_at = -1
        elif char == "]" and depth == 0:
            break
        index += 1
    return _coerce_candidates(items)


def _parse_model_json(content: str) -> list[dict[str, Any]]:
    text = (content or "").strip()
    if not text:
        return []
    if text.startswith("ERROR:"):
        raise RuntimeError(text)
    candidates: list[str] = [text]
    block = _extract_json_block(text)
    if block:
        candidates.append(block)
    for candidate in candidates:
        if not candidate:
            continue
        try:
            parsed = json.loads(_escape_newlines_in_strings(candidate))
            return _coerce_candidates(parsed)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue

    recovered = _recover_complete_candidates(text)
    if recovered:
        _log.warning(
            "[LOCK_INFERENCE] answer is not one JSON document (raw %d chars); "
            "recovered %d complete candidate(s) from it",
            len(text),
            len(recovered),
        )
        return recovered
    _log_parse_failure(text)
    return []


def _ensure_provider_key(model: str) -> None:
    if model.startswith("gemini/"):
        if not key_present("gemini"):
            raise RuntimeError("Gemini API key not configured for visual lock inference.")
    elif model.startswith("deepseek/"):
        if not key_present("deepseek"):
            raise RuntimeError("DeepSeek API key not configured for lock inference.")
    else:
        raise RuntimeError(f"Unsupported lock inference model: {model}")


def _call_text_model(model: str, prompt: str) -> str:
    return call_model(
        model,
        [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": prompt},
        ],
        # No explicit ``max_tokens``: the runner asks for the intent's budget
        # (``min(PEM_MAX_TOKENS, cap_output_tokens("analysis", model))``, 2048 for
        # deepseek-chat). The 1024 that used to sit here was under the cap and cut
        # a figures-dense memo's extraction off mid-object — the 30 candidates of
        # the renewal memo need ~1.5k tokens. A truncated answer is not a partial
        # ledger: it is no ledger, and the draft's figures went unchecked.
        temperature=0.1,
        response_format={"type": "json_object"},
        intent="analysis",
    )


def _call_vision_model(model: str, prompt: str, pdf_bytes: bytes) -> str:
    b64 = base64.standard_b64encode(pdf_bytes).decode("ascii")
    return call_model(
        model,
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:application/pdf;base64,{b64}"},
                    },
                ],
            }
        ],
        temperature=0.1,
        response_format={"type": "json_object"},
        intent="analysis",
    )


def infer_lock_candidates(
    text: str,
    *,
    pdf_bytes: bytes | None = None,
    has_visual_content: bool | None = None,
    model: str | None = None,
) -> LockInferenceResult:
    """Extract high-confidence lock candidates; route to Gemini when PDF has charts/images."""
    substrate = (text or "").strip()
    visual = (
        bool(has_visual_content)
        if has_visual_content is not None
        else pdf_has_visual_content(pdf_bytes or b"")
    )
    chosen = model or resolve_lock_inference_model(visual)

    if not pdf_bytes and len(substrate) < 20:
        return LockInferenceResult([], chosen, visual)

    load_keys()
    _ensure_provider_key(chosen)

    if visual and pdf_bytes:
        prompt = _VISION_USER_TEMPLATE.format(
            text=substrate[:8000] if substrate else "(see attached PDF)"
        )
        raw = _call_vision_model(chosen, prompt, pdf_bytes)
    else:
        if len(substrate) < 20:
            return LockInferenceResult([], chosen, visual)
        prompt = _USER_TEMPLATE.format(text=substrate[:12000])
        raw = _call_text_model(chosen, prompt)

    return LockInferenceResult(_parse_model_json(raw), chosen, visual)


def document_substrate_text(doc: dict[str, Any]) -> str:
    """Flatten JDF body text when no explicit substrate is provided."""
    lines: list[str] = []
    for section in doc.get("body") or []:
        if not isinstance(section, dict):
            continue
        title = str(section.get("title") or "").strip()
        if title:
            lines.append(title)
        for child in section.get("children") or []:
            if not isinstance(child, dict):
                continue
            content = str(child.get("content") or child.get("text") or "").strip()
            if content:
                lines.append(content)
    meta_text = str((doc.get("meta") or {}).get("substrate_text") or "").strip()
    if meta_text:
        lines.insert(0, meta_text)
    return "\n\n".join(lines)
