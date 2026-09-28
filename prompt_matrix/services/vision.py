"""Visual facts for the picture pages of an intake (Parsure Phase 5).

A phone photo of a dented bumper, a screenshot, a picture of an odometer:
the OCR pipeline reads their text (if any) and the taxonomy fields stay
honestly empty. This module asks a multimodal model what the picture
*shows* and records the answer beside the report — as **facts**, never as
fields. Customer plan ``todos/fable_execution_plan.md`` Part 5 (2026-09-27).

Rules this module keeps (``docs/anti-claims.md`` "Vision (2026-09-27)"):

* A fact exists only when the model answered with a ``name``, a ``value`` and
  an ``evidence`` sentence naming what in the image supports it. An answer
  with no facts is an empty list; a fact without evidence is dropped and
  counted. Nothing here fills a default.
* Facts never enter ``report["fields"]``, ``review_summary`` or any count.
  The customer's earlier complaint was inconsistent counts across surfaces;
  a visual observation is not a taxonomy field and must not move them.
* The picture's quality is measured first (``quality_probe``'s own blur /
  contrast / pixel-size numbers). A ``poor`` picture is not sent to the
  model; the page entry says why.
* Bounded: at most ``PARSURE_VISION_MAX_PAGES`` pictures per document (4),
  ``PARSURE_VISION_TIMEOUT_S`` seconds per model call (45), one JSON retry
  per page. Every failure is a status with the exception class, never an
  exception out of ``attach_vision`` — the ingest already has its revision.

Model access is the app's own path: ``cost_governance.llm_backend()`` picks
the backend, ``ASSURE_<BACKEND>_MODEL_VISION`` the multimodal model, and
litellm carries the PNG as an ``image_url`` data URI (the one content shape
OpenRouter, Bedrock Converse and Ollama's OpenAI-compatible endpoint all
accept through litellm). Tests inject ``completion(prompt, png_bytes)`` and
never touch the network, the same pattern ``llm_extraction`` uses.

Integration point (the orchestrator owner calls it once, after the report
is built and before ``parsure_repository.save_report`` stamps the
snapshot): ``vision.attach_vision(report, file_bytes=..., intake=...)``.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import threading
import time
from typing import Any, Callable

try:
    from . import model_calls as _mc
except ImportError:  # pragma: no cover - flat-import fallback
    import model_calls as _mc  # type: ignore

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

#: Vision model per backend. Ollama: ``qwen2.5vl:3b`` (the smallest Qwen2.5-VL
#: tag Ollama serves, ~3.2 GB; NOT pulled by ``ollama-pull``, so on the local
#: backend vision is off until the variable is set — see ``vision_enabled``).
#: OpenRouter: ``amazon/nova-lite-v1`` (multimodal, the same id the parse
#: stage already uses). Bedrock: the drafting Sonnet id (multimodal).
OLLAMA_VISION_ENV = "ASSURE_OLLAMA_MODEL_VISION"
OLLAMA_VISION_DEFAULT = "qwen2.5vl:3b"
OPENROUTER_VISION_ENV = "ASSURE_OPENROUTER_MODEL_VISION"
OPENROUTER_VISION_DEFAULT = "amazon/nova-lite-v1"
BEDROCK_VISION_ENV = "ASSURE_BEDROCK_MODEL_VISION"

#: Hard wall-clock bound for one model call on one picture (seconds). Same
#: figure as ``llm_extraction.CLASSIFY_TIMEOUT_S``; a hosted multimodal call
#: is expected well under it and a CPU-bound local tag gets room. Not yet
#: measured against a live vision model (2026-09-27: no key and no vision
#: tag on this machine) — ``PARSURE_VISION_TIMEOUT_S`` overrides.
DEFAULT_TIMEOUT_S = 45.0
#: Pictures analysed per document at most; the rest are listed as skipped.
DEFAULT_MAX_PAGES = 4
#: Longest side of the PNG sent to the model. 1568 px is the largest edge
#: Anthropic documents as fully used; above it every provider downsamples
#: anyway and the base64 payload only grows (a 4000 px phone photo is ~3 MB).
RENDER_LONG_SIDE_PX = 1568
#: Below this long side there is nothing a model can be asked to read.
MIN_PICTURE_LONG_SIDE_PX = 480
#: Re-ask once when the answer is not JSON — the same two-passes bound as
#: ``cost_governance.MAX_RETRIES`` and ``llm_extraction.JSON_RETRIES``.
JSON_RETRIES = 1
MAX_OUTPUT_TOKENS = 1200
#: Longest ``value`` / ``evidence`` string kept; a model that writes an essay
#: into ``value`` is truncated, not trusted more.
MAX_TEXT_CHARS = 600

ANALYSIS_TYPES = ("accident_scene", "property_damage", "document_photo", "generic")

#: Fact names (plan Part 5.4, ``*_visual``) the model may use per analysis
#: type. A fact with another name is dropped and counted: the report's
#: vocabulary is the customer's, not the model's.
FACT_NAMES: dict[str, tuple[str, ...]] = {
    "accident_scene": ("scene_summary", "vehicle_count_visual", "vehicles_involved_visual", "damage_description_visual"),
    "property_damage": (
        "scene_summary", "property_condition_visual", "roof_condition_visual", "water_damage_visual",
        "fire_damage_visual", "damage_description_visual",
    ),
    "document_photo": ("scene_summary", "odometer_visual", "license_plate_visual", "vin_plate_visual"),
}
FACT_NAMES["generic"] = tuple(dict.fromkeys(n for names in FACT_NAMES.values() for n in names))

_FACT_HINTS = {
    "scene_summary": "one or two sentences describing what the picture shows",
    "vehicle_count_visual": "number of vehicles visible (integer)",
    "vehicles_involved_visual": "the vehicles visible: colour, body type, make/model only if legible",
    "damage_description_visual": "visible damage: where on the object, what kind, how severe",
    "property_condition_visual": "overall condition of the property or room shown",
    "roof_condition_visual": "roof condition if a roof is visible",
    "water_damage_visual": "signs of water damage if any are visible",
    "fire_damage_visual": "signs of fire or smoke damage if any are visible",
    "odometer_visual": "the odometer reading exactly as displayed, digits only",
    "license_plate_visual": "the licence plate text exactly as legible",
    "vin_plate_visual": "the VIN exactly as legible on a plate or sticker",
}

Completion = Callable[[str, bytes], str]


class VisionUnavailable(Exception):
    """The model could not be called or did not answer usably; the caller records it and moves on."""


def _one_line(exc: BaseException, limit: int = 200) -> str:
    """``ClassName: message`` on one line — litellm puts a traceback into the
    exception text (seen 2026-09-27), which would bloat the report's reason."""
    text = re.sub(r"\s+", " ", str(exc)).strip()
    return f"{type(exc).__name__}: {text[:limit]}" if text else type(exc).__name__


def _env_flag(name: str) -> str:
    return os.environ.get(name, "").strip().lower()


def _governor():
    try:
        from .. import cost_governance as cg  # type: ignore
    except ImportError:
        import cost_governance as cg  # type: ignore
    return cg


def _qp():
    try:
        from . import quality_probe as qp  # type: ignore
    except ImportError:
        import quality_probe as qp  # type: ignore
    return qp


def vision_model(backend: str | None = None) -> tuple[str | None, str | None]:
    """``(litellm model id, None)`` for the active backend, or ``(None, reason)``.

    ``ollama`` → ``ollama/<ASSURE_OLLAMA_MODEL_VISION or qwen2.5vl:3b>``;
    ``openrouter`` (and the legacy ``cloud`` policies when an OpenRouter key
    exists) → ``openrouter/<ASSURE_OPENROUTER_MODEL_VISION or amazon/nova-lite-v1>``;
    ``bedrock`` → ``ASSURE_BEDROCK_MODEL_VISION`` or the drafting Sonnet id,
    qualified with the region's inference-profile prefix exactly as
    ``cost_governance.bedrock_model`` does.
    """
    cg = _governor()
    backend = cg.llm_backend() if backend is None else backend
    if backend == "ollama":
        raw = os.environ.get(OLLAMA_VISION_ENV, "").strip() or OLLAMA_VISION_DEFAULT
        return (raw if raw.startswith("ollama/") else f"ollama/{raw}"), None
    if backend == "bedrock":
        raw = os.environ.get(BEDROCK_VISION_ENV, "").strip()
        return (cg._bedrock_qualify(raw) if raw else cg.bedrock_model("draft")), None  # noqa: SLF001
    if backend == "openrouter" or (backend == "" and os.environ.get("OPENROUTER_API_KEY", "").strip()):
        raw = os.environ.get(OPENROUTER_VISION_ENV, "").strip() or OPENROUTER_VISION_DEFAULT
        return (raw if raw.startswith("openrouter/") else f"openrouter/{raw}"), None
    return None, f"no vision model for backend {backend or 'cloud'!r} (no OpenRouter key; set ASSURE_LLM_BACKEND); {model_table()}"


def model_table() -> str:
    """The backend → variable → default table, in one line, for a reason string:
    a report that says ``disabled`` must say what would enable it."""
    return (
        "vision models: "
        f"ollama={OLLAMA_VISION_ENV} (default {OLLAMA_VISION_DEFAULT}, off until set), "
        f"openrouter={OPENROUTER_VISION_ENV} (default {OPENROUTER_VISION_DEFAULT}), "
        f"bedrock={BEDROCK_VISION_ENV} (default ASSURE_BEDROCK_MODEL_DRAFT)"
    )


def vision_enabled() -> tuple[bool, str | None, str | None]:
    """``(enabled, model, reason)`` from ``PARSURE_VISION`` and the backend.

    ``0/false/no/off`` → off. ``1/true/yes/on`` → on with the backend's model
    (a missing model still disables, with the reason). Unset → on when a
    vision model is configured for the active backend: OpenRouter and Bedrock
    defaults are multimodal, so they count; on Ollama only an explicit
    ``ASSURE_OLLAMA_MODEL_VISION`` counts, because the default tag is not
    part of ``ollama-pull`` and every photo would otherwise fail with
    "model not found" on a stock compose stack.
    """
    raw = _env_flag("PARSURE_VISION")
    if raw in ("0", "false", "no", "off"):
        return False, None, "PARSURE_VISION is off"
    cg = _governor()
    backend = cg.llm_backend()
    model, why = vision_model(backend)
    if model is None:
        return False, None, why
    if raw in ("1", "true", "yes", "on"):
        return True, model, None
    if backend == "ollama" and not os.environ.get(OLLAMA_VISION_ENV, "").strip():
        return False, model, f"no vision model configured for backend ollama (set {OLLAMA_VISION_ENV}, or PARSURE_VISION=1 to use {model}); {model_table()}"
    return True, model, None


def _timeout_s() -> float:
    raw = os.environ.get("PARSURE_VISION_TIMEOUT_S", "").strip()
    try:
        value = float(raw) if raw else DEFAULT_TIMEOUT_S
    except ValueError:
        value = DEFAULT_TIMEOUT_S
    return max(5.0, min(180.0, value))


def _max_pages() -> int:
    raw = os.environ.get("PARSURE_VISION_MAX_PAGES", "").strip()
    try:
        value = int(raw) if raw else DEFAULT_MAX_PAGES
    except ValueError:
        value = DEFAULT_MAX_PAGES
    return max(0, min(50, value))


# --------------------------------------------------------------------------
# Rendering and picture quality — quality_probe's PyMuPDF path, no second renderer
# --------------------------------------------------------------------------

def render_page_png(file_bytes: bytes, filename: str | None, page_no: int, *, long_side_px: int = RENDER_LONG_SIDE_PX) -> bytes:
    """PNG of page ``page_no`` (1-based) of a PDF or image, long side capped.

    ``quality_probe._open_document`` opens PDFs and PNG/JPEG/TIFF/BMP alike
    (an image is a one-page document whose rect is its pixel grid at 72 dpi),
    so one ``get_pixmap`` call serves both. Raises ``ValueError`` when the
    bytes do not render or the page does not exist.
    """
    qp = _qp()
    doc = qp._open_document(file_bytes, filename or "")  # noqa: SLF001
    if doc is None:
        raise ValueError(f"not a renderable document: {filename or '<bytes>'}")
    try:
        if page_no < 1 or page_no > len(doc):
            raise ValueError(f"page {page_no} out of range (1..{len(doc)})")
        page = doc[page_no - 1]
        fitz = qp._fitz()  # noqa: SLF001
        rect = page.rect
        long_side = max(rect.width, rect.height) or 1.0
        if doc.is_pdf:
            # Render up to the cap (a letter page at 1568 px is ~190 dpi,
            # enough for plates and odometer digits).
            scale = long_side_px / long_side
        else:
            # An image page's rect is its pixel grid at the file's resolution
            # metadata (a 96-dpi PNG of 640 px is a 480 pt page), so scale 1
            # would resample. Go by the native pixels: keep them when under
            # the cap, shrink to the cap otherwise, never upscale.
            native = fitz.Pixmap(file_bytes)
            native_long = max(native.width, native.height) or 1
            scale = (native.width / (rect.width or 1.0)) * min(1.0, long_side_px / native_long)
        pm = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csRGB, alpha=False)
        return pm.tobytes("png")
    finally:
        doc.close()


def picture_quality(image_bytes: bytes, filename: str = "picture.png") -> dict[str, Any]:
    """Measured quality of one picture: ``quality_probe.probe_visual_quality``
    on the bytes (Laplacian blur variance, background−ink contrast range,
    pixel size), summarised as ``good`` / ``acceptable`` / ``poor``.

    ``poor`` = the long side is under ``MIN_PICTURE_LONG_SIDE_PX`` or the
    probe flagged both ``blurry`` and ``low_contrast`` (nothing legible for a
    model to name evidence in). ``acceptable`` = any single measured flag.
    ``good`` = none. The probe's ``low_res`` flag is reported but not used
    for the status: it is a DPI estimate that assumes a letter-size page,
    which a photo is not. When the bytes do not render the status is
    ``poor`` with ``basis`` saying so — no number is invented.
    """
    qp = _qp()
    try:
        probes = qp.probe_visual_quality(image_bytes, filename)
    except Exception as exc:  # noqa: BLE001 — quality unknown is reported, not raised
        probes = []
        log.info("vision: picture quality probe failed: %s", exc)
    if not probes:
        return {
            "status": "poor", "flags": ["not_renderable"], "width": None, "height": None,
            "sharpness": None, "contrast_range": None, "contrast_std": None,
            "basis": "picture could not be rendered by PyMuPDF; no measurement",
        }
    p = probes[0]
    width, height = p.get("width_px"), p.get("height_px")
    flags = [f for f in (p.get("flags") or []) if f in ("blurry", "low_contrast", "low_res")]
    long_side = max(int(width or 0), int(height or 0))
    reasons: list[str] = []
    if long_side < MIN_PICTURE_LONG_SIDE_PX:
        status = "poor"
        flags.append("too_small")
        reasons.append(f"long side {long_side} px < {MIN_PICTURE_LONG_SIDE_PX}")
    elif "blurry" in flags and "low_contrast" in flags:
        status = "poor"
        reasons.append("blurry and low_contrast together")
    elif any(f in flags for f in ("blurry", "low_contrast")):
        status = "acceptable"
        reasons.append("one measured flag: " + ", ".join(f for f in flags if f in ("blurry", "low_contrast")))
    else:
        status = "good"
        reasons.append("no blur or contrast flag")
    return {
        "status": status,
        "flags": flags,
        "width": width,
        "height": height,
        "sharpness": p.get("blur_variance"),
        "contrast_range": p.get("contrast_range"),
        "contrast_std": p.get("contrast_std"),
        "basis": f"{status}: {'; '.join(reasons)}. probe: {p.get('basis')}",
    }


# --------------------------------------------------------------------------
# Prompt and answer
# --------------------------------------------------------------------------

def build_prompt(analysis_type: str, context: str | None) -> str:
    names = FACT_NAMES.get(analysis_type) or FACT_NAMES["generic"]
    lines = [
        "You are examining one picture attached to an insurance or title document intake.",
        f"Analysis type: {analysis_type}.",
        "Report only what is visible in the picture. Do not guess, do not fill in typical values.",
        "Answer with exactly one JSON object and nothing else, in this shape:",
        '{"facts": [{"name": "<one of the names below>", "value": <string or number>, '
        '"confidence": <your own 0-1 estimate or null>, "evidence": "<what in the image supports this>", '
        '"bbox": [x0, y0, x1, y1] as fractions 0-1 of width/height, or null}]}',
        "Allowed names (use no others, at most one fact per name):",
    ]
    lines += [f"- {n}: {_FACT_HINTS.get(n, '')}".rstrip(": ") for n in names]
    lines += [
        "Every fact must have a non-empty evidence sentence naming the visible detail it rests on.",
        'If nothing can be determined, answer {"facts": []}.',
    ]
    if context and str(context).strip():
        lines.append(f"Context from the document: {str(context).strip()[:500]}")
    return "\n".join(lines)


def _balance_brackets(text: str) -> str:
    """Drop closing brackets/braces that have no opener (outside strings)."""
    out: list[str] = []
    stack: list[str] = []
    in_str = False
    escape = False
    for ch in text:
        if in_str:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "[{":
            stack.append(ch)
        elif ch in "]}":
            want = "[" if ch == "]" else "{"
            if stack and stack[-1] == want:
                stack.pop()
            else:
                continue  # unmatched closer: the stray bracket, dropped
        out.append(ch)
    return "".join(out)


def _parse_answer(text: str) -> list[Any] | None:
    """The ``facts`` list in a model answer (code fences and prose around the
    JSON tolerated; a bare list accepted), or None when there is no JSON."""
    if not isinstance(text, str) or not text.strip():
        return None
    body = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I | re.M)
    candidates = [body]
    start, end = body.find("{"), body.rfind("}")
    if 0 <= start < end:
        candidates.append(body[start:end + 1])
    start, end = body.find("["), body.rfind("]")
    if 0 <= start < end:
        candidates.append(body[start:end + 1])
    # Syntactic repair only (measured live 2026-09-28: Nova Lite closed a
    # bbox list with a stray "]" in about one answer in two — valid facts,
    # invalid JSON). Unmatched closing brackets outside strings are dropped;
    # nothing inside a string is touched and no value is invented.
    candidates.extend(_balance_brackets(c) for c in list(candidates))
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            facts = parsed.get("facts")
            if not parsed or ("facts" in parsed and facts is None):
                return []
            if isinstance(facts, list):
                return facts
            if "facts" not in parsed and all(isinstance(v, (str, int, float, dict)) for v in parsed.values()):
                # {"scene_summary": {...}, ...} — a keyed object; fold it into a list.
                return [dict(v, name=k) if isinstance(v, dict) else {"name": k, "value": v} for k, v in parsed.items()]
            continue
        if isinstance(parsed, list):
            return parsed
    return None


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip() if not isinstance(value, (dict, list)) else json.dumps(value, ensure_ascii=False)
    return s[:MAX_TEXT_CHARS] if s else None


def _clean_confidence(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        c = float(value)
    except (TypeError, ValueError):
        return None
    if c != c:  # NaN
        return None
    return round(min(1.0, max(0.0, c)), 3)


def _clean_bbox(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        box = [float(v) for v in value]
    except (TypeError, ValueError):
        return None
    if any(b != b for b in box):
        return None
    if max(box) > 1.0:
        # Qwen-VL answers in 0–1000 units; scale down when every value fits that grid.
        if max(box) <= 1000.0 and min(box) >= 0.0:
            box = [b / 1000.0 for b in box]
        else:
            return None
    if min(box) < 0.0 or box[0] >= box[2] or box[1] >= box[3]:
        return None
    return [round(b, 4) for b in box]


def normalise_facts(raw: list[Any], *, allowed: tuple[str, ...]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Facts the answer supports, and what was dropped and why.

    Kept: a dict with an allowed ``name``, a non-empty ``value`` and a
    non-empty ``evidence`` string. Dropped and counted: unknown names
    (``unknown_name``), missing evidence (``no_evidence``), missing value
    (``no_value``), not an object (``not_object``), a second fact for a name
    already kept (``duplicate``).
    """
    kept: list[dict[str, Any]] = []
    dropped = {"not_object": 0, "unknown_name": 0, "no_value": 0, "no_evidence": 0, "duplicate": 0}
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            dropped["not_object"] += 1
            continue
        name = str(item.get("name") or "").strip().lower()
        if name not in allowed:
            dropped["unknown_name"] += 1
            continue
        value = item.get("value")
        if isinstance(value, str):
            value = value.strip() or None
        if value is None:
            dropped["no_value"] += 1
            continue
        evidence = _clean_text(item.get("evidence"))
        if not evidence:
            dropped["no_evidence"] += 1
            continue
        if name in seen:
            dropped["duplicate"] += 1
            continue
        seen.add(name)
        kept.append({
            "name": name,
            "value": value if isinstance(value, (int, float)) and not isinstance(value, bool) else _clean_text(value),
            "confidence": _clean_confidence(item.get("confidence")),
            "evidence": evidence,
            "bbox": _clean_bbox(item.get("bbox")),
        })
    return kept, {k: v for k, v in dropped.items() if v}


# --------------------------------------------------------------------------
# Model call — litellm with an image content part, hard wall-clock bound
# --------------------------------------------------------------------------

def default_completion(prompt: str, image_png_bytes: bytes, *, model: str) -> str:
    """One multimodal call through litellm with the app's provider kwargs
    (``cost_governance._litellm_api_kwargs``: OpenRouter key + headers,
    Ollama api_base, Bedrock region). The PNG travels as an ``image_url``
    data URI. Raises ``VisionUnavailable`` on any provider error."""
    import litellm

    cg = _governor()
    kwargs = cg._litellm_api_kwargs(model)  # noqa: SLF001
    data_uri = "data:image/png;base64," + base64.b64encode(image_png_bytes).decode("ascii")
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": data_uri}},
        ],
    }]
    try:
        with _mc.stage_context("vision"):
            resp = litellm.completion(
                model=model, messages=messages, max_tokens=MAX_OUTPUT_TOKENS, stream=False,
                timeout=int(_timeout_s()), metadata=_mc.litellm_metadata(kwargs.pop("metadata", None)), **kwargs,
            )
    except Exception as exc:  # noqa: BLE001 — the class name is what the report records
        raise VisionUnavailable(_one_line(exc)) from exc
    try:
        try:
            from .model_utils import extract_litellm_response_text
        except ImportError:
            from model_utils import extract_litellm_response_text  # type: ignore
        text = extract_litellm_response_text(resp)
    except Exception:  # noqa: BLE001
        text = ""
    if not isinstance(text, str) or not text.strip():
        raise VisionUnavailable(f"{model}: empty answer")
    return text


_OLLAMA_CAPS: dict[tuple[str, str], list[str] | None] = {}


def _ollama_capabilities(model: str) -> list[str] | None:
    """``/api/show`` capabilities of an Ollama tag (Ollama 0.34.3 on the
    compose stack lists ``['completion', 'tools']`` for qwen2.5:1.5b and
    llama3.2:1b, 2026-09-27; a vision tag lists ``'vision'``), or None when
    the daemon cannot say. Cached per process: one GET per tag, not per page."""
    import urllib.request

    cg = _governor()
    base = (cg._litellm_api_kwargs(model).get("api_base") or "http://127.0.0.1:11434").rstrip("/")  # noqa: SLF001
    tag = model.split("/", 1)[-1]
    key = (base, tag)
    if key in _OLLAMA_CAPS:
        return _OLLAMA_CAPS[key]
    caps: list[str] | None = None
    try:
        req = urllib.request.Request(f"{base}/api/show", data=json.dumps({"model": tag}).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as resp:  # noqa: S310 — the configured Ollama daemon
            data = json.loads(resp.read().decode("utf-8", "replace"))
        raw = data.get("capabilities") if isinstance(data, dict) else None
        caps = [str(c) for c in raw] if isinstance(raw, list) else None
    except Exception as exc:  # noqa: BLE001 — unknown is unknown; the call itself will say
        log.info("vision: could not read capabilities of %s: %s", tag, _one_line(exc))
    _OLLAMA_CAPS[key] = caps
    return caps


def model_cannot_see(model: str | None) -> str | None:
    """A reason when the configured model is known to have no vision
    capability (Ollama only — the daemon reports it), else None. A text model
    handed an image answers from the prompt alone, and every "fact" it named
    would be invented; that is refused before any picture is sent."""
    if not model or not model.startswith("ollama/"):
        return None
    caps = _ollama_capabilities(model)
    if caps is not None and "vision" not in caps:
        return f"{model} has no vision capability (ollama /api/show capabilities: {caps}); pick a multimodal tag"
    return None


def _call_with_timeout(fn: Callable[[], str], timeout_s: float) -> str:
    """Run ``fn`` on a daemon thread and wait at most ``timeout_s`` (same
    guard as ``llm_extraction._call_with_timeout``: an injected or stalled
    completion must not hold the ingest worker; a late answer is discarded)."""
    box: dict[str, Any] = {}

    def _run() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc

    t = threading.Thread(target=_run, name="parsure-vision", daemon=True)
    t.start()
    t.join(max(0.0, float(timeout_s)))
    if t.is_alive():
        raise VisionUnavailable(f"TimeoutError: no answer after {timeout_s:g}s")
    if "error" in box:
        exc = box["error"]
        if isinstance(exc, VisionUnavailable):
            raise exc
        raise VisionUnavailable(_one_line(exc)) from exc
    return str(box.get("value") or "")


def analyze_page(
    image_png_bytes: bytes,
    *,
    analysis_type: str = "generic",
    context: str | None = None,
    completion: Completion | None = None,
    model: str | None = None,
    timeout_s: float | None = None,
) -> dict[str, Any]:
    """Ask the vision model about one picture.

    Returns ``{"status": "completed"|"failed", "model", "facts": [...],
    "dropped": {...}, "calls", "ms", "reason"}``. ``facts`` are the answer's
    supported facts (``normalise_facts``); an empty answer is ``[]`` with
    status ``completed``. A non-JSON answer is asked once more; a second
    non-JSON answer, a timeout or a provider error is ``failed`` with the
    reason (exception class first) and no facts. ``completion`` is
    ``(prompt, png_bytes) -> text`` for tests; production resolves the
    backend's vision model.
    """
    if analysis_type not in ANALYSIS_TYPES:
        analysis_type = "generic"
    started = time.monotonic()
    if completion is None:
        if model is None:
            model, why = vision_model()
            if model is None:
                return {"status": "failed", "model": None, "facts": [], "dropped": {}, "calls": 0, "ms": 0, "reason": why}
        blind = model_cannot_see(model)
        if blind:
            return {"status": "failed", "model": model, "facts": [], "dropped": {}, "calls": 0, "ms": 0, "reason": blind}
        resolved = model
        call: Completion = lambda p, b: default_completion(p, b, model=resolved)  # noqa: E731
    else:
        model = model or "injected"
        call = completion
    if not image_png_bytes:
        return {"status": "failed", "model": model, "facts": [], "dropped": {}, "calls": 0, "ms": 0, "reason": "ValueError: empty image"}
    bound = float(timeout_s) if timeout_s is not None else _timeout_s()
    prompt = build_prompt(analysis_type, context)
    calls = 0
    raw_facts = None
    answer = ""
    reason = None
    for attempt in range(JSON_RETRIES + 1):
        calls += 1
        try:
            answer = _call_with_timeout(lambda: call(prompt, image_png_bytes), bound)
        except VisionUnavailable as exc:
            reason = str(exc)
            break
        raw_facts = _parse_answer(answer)
        if raw_facts is not None:
            break
        if attempt < JSON_RETRIES:
            log.info("vision: %s answered without JSON; asking once more", model)
    ms = int((time.monotonic() - started) * 1000)
    if reason is not None:
        log.warning("vision: %s failed (%s) after %d ms", model, reason, ms)
        return {"status": "failed", "model": model, "facts": [], "dropped": {}, "calls": calls, "ms": ms, "reason": reason}
    if raw_facts is None:
        # Head and tail, not the head alone: live 2026-09-28 Nova Lite answered
        # a well-formed object except for one stray ``]`` in ``bbox`` at the
        # very end (``[165, 181, 355, 195]]}]}``), and an 80-character head
        # showed a valid-looking answer with no hint of why it failed.
        flat = re.sub(r"\s+", " ", str(answer))
        head, tail = flat[:80], flat[-60:] if len(flat) > 140 else ""
        log.warning("vision: unparsable answer from %s: %r", model, str(answer)[:400])
        return {
            "status": "failed", "model": model, "facts": [], "dropped": {}, "calls": calls, "ms": ms,
            "reason": (
                f"ValueError: model answer was not JSON after {calls} call(s) "
                f"({len(flat)} chars; starts: {head!r}" + (f"; ends: {tail!r}" if tail else "") + ")"
            ),
        }
    facts, dropped = normalise_facts(raw_facts, allowed=FACT_NAMES.get(analysis_type) or FACT_NAMES["generic"])
    for fact in facts:
        fact["model"] = model
    log.info("vision: %s %s → %d fact(s), dropped %s, %d call(s), %d ms", model, analysis_type, len(facts), dropped or "none", calls, ms)
    # What the model answered when no fact survived, so an empty page entry
    # says "the model answered and named nothing" rather than nothing at all
    # (live 2026-09-28: Nova Lite answered a rendered text page in 2 calls —
    # the first without JSON — and named no fact; the report showed ``facts:
    # []`` and ``reason: null``, which reads as "not asked").
    answer_note = None
    if not facts:
        head = re.sub(r"\s+", " ", str(answer)).strip()[:160]
        parts = [f"the model answered and named no fact ({len(raw_facts)} offered, {sum(dropped.values())} dropped)"]
        if calls > 1:
            parts.append(f"first answer was not JSON, asked {calls} times")
        if head:
            parts.append(f"answer starts: {head!r}")
        answer_note = "; ".join(parts)
    return {"status": "completed", "model": model, "facts": facts, "dropped": dropped, "calls": calls, "ms": ms, "reason": None, "note": answer_note}


# --------------------------------------------------------------------------
# Which pages are pictures, and which analysis fits the document
# --------------------------------------------------------------------------

_PICTURE_MODALITIES = {"phone_photo": "photo", "screenshot": "screenshot"}
_PICTURE_MATERIALS = {"photo": "photo", "screenshot": "screenshot", "image": "image"}
_ACCIDENT_TYPES = frozenset({"auto_claim", "auto_policy"})
_PROPERTY_TYPES = frozenset({"property_claim", "property_policy"})


def picture_pages(intake: dict | None, report: dict | None = None) -> list[tuple[int, str]]:
    """``[(page_no, kind), ...]`` — the pages that are pictures.

    A picture upload (``modality`` phone_photo / screenshot, or
    ``material_type`` photo / screenshot / image) makes every page a picture;
    otherwise only the pages the visual probe flagged ``photo`` are. Kinds:
    ``photo`` / ``screenshot`` / ``image``. A PDF with a text layer and no
    ``photo`` flag has no picture pages — its embedded figures are not
    analysed in V1.
    """
    intake = intake or {}
    report = report or {}
    modality = str(intake.get("modality") or report.get("modality") or "").lower()
    material = str(intake.get("material_type") or report.get("material_type") or "").lower()
    visual_pages = [v for v in (intake.get("visual_pages") or []) if isinstance(v, dict)]
    if not visual_pages:
        visual_pages = [p.get("visual") for p in (report.get("pages") or []) if isinstance(p, dict) and isinstance(p.get("visual"), dict)]
    page_count = int(report.get("page_count") or 0) or len(visual_pages) or (1 if modality in _PICTURE_MODALITIES or material in _PICTURE_MATERIALS else 0)
    kind = _PICTURE_MODALITIES.get(modality) or _PICTURE_MATERIALS.get(material)
    if kind:
        return [(n, kind) for n in range(1, page_count + 1)]
    out: list[tuple[int, str]] = []
    for i, v in enumerate(visual_pages):
        if "photo" in (v.get("flags") or []):
            out.append((int(v.get("page") or i + 1), "photo"))
    return out


def analysis_type_for(kind: str, document_type: str | None) -> str:
    """Which question to ask: a screenshot or a plain image is a picture *of a
    document* (plates, odometers, VIN stickers); a photo under an auto type
    is an accident scene, under a property type property damage; else generic."""
    if kind in ("screenshot", "image"):
        return "document_photo"
    dt = str(document_type or "").lower()
    if dt in _ACCIDENT_TYPES:
        return "accident_scene"
    if dt in _PROPERTY_TYPES:
        return "property_damage"
    return "generic"


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def picture_rule(intake: dict | None, report: dict | None, pages: list[tuple[int, str]]) -> dict[str, Any]:
    """Why ``picture_pages`` answered as it did — recorded on the block so a
    ``not_run`` is legible without reading this module: the modality and
    material type read, whether they make every page a picture, how many
    pages the visual probe flagged ``photo``, and the resulting count.

    Live reports of 2026-09-28 all said ``not_run`` with the modality in the
    reason; every one was a digital PDF, which is the correct answer, but a
    reader could not tell the rule from a failure to configure the model.
    """
    intake = intake or {}
    report = report or {}
    modality = str(intake.get("modality") or report.get("modality") or "").lower() or None
    material = str(intake.get("material_type") or report.get("material_type") or "").lower() or None
    visual_pages = [v for v in (intake.get("visual_pages") or []) if isinstance(v, dict)]
    if not visual_pages:
        visual_pages = [p.get("visual") for p in (report.get("pages") or []) if isinstance(p, dict) and isinstance(p.get("visual"), dict)]
    flagged = sum(1 for v in visual_pages if "photo" in (v.get("flags") or []))
    whole = bool(_PICTURE_MODALITIES.get(modality or "") or _PICTURE_MATERIALS.get(material or ""))
    return {
        "modality": modality,
        "material_type": material,
        "picture_modalities": sorted(_PICTURE_MODALITIES),
        "picture_materials": sorted(_PICTURE_MATERIALS),
        "every_page_is_a_picture": whole,
        "pages_probed": len(visual_pages),
        "pages_flagged_photo": flagged,
        "picture_pages": len(pages),
        "basis": (
            f"modality {modality!r} / material {material!r} make every page a picture"
            if whole
            else f"modality {modality!r} / material {material!r} are not picture kinds; "
            f"{flagged} of {len(visual_pages)} probed page(s) flagged photo"
        ),
    }


def _execution(report: dict[str, Any], block: dict[str, Any]) -> None:
    report.setdefault("execution", {})["vision"] = {
        "status": block["status"],
        "model": block.get("model"),
        "pages_analyzed": sum(1 for p in block.get("pages") or [] if p.get("status") == "completed"),
        "facts": sum(len(p.get("facts") or []) for p in block.get("pages") or []),
        "ms": block.get("ms", 0),
        "reason": block.get("reason"),
    }


def attach_vision(
    report: dict[str, Any],
    *,
    file_bytes: bytes | None,
    intake: dict | None,
    filename: str | None = None,
    completion: Completion | None = None,
) -> dict[str, Any]:
    """Write ``report["vision"]`` and ``report["execution"]["vision"]``; return the report.

    Never raises: a failure anywhere is ``status: failed`` with the exception
    class in ``reason``. Never touches ``fields``, ``review_summary`` or any
    count. Fast no-op (no render, no model) when disabled or when the intake
    has no picture pages. The rule, stated once (2026-09-28, after the live
    reports read ``not_run`` on every upload and the operator could not tell
    why):

    * ``PARSURE_VISION`` off, or no vision model for the backend →
      ``status: disabled``; ``reason`` names the switch or the backend and
      carries the model table (``model_table``).
    * a digital or scanned PDF with no page the visual probe flagged ``photo``
      → ``status: not_run``, ``reason: "no picture pages"``, ``rule`` saying
      which test decided (``picture_rule``). This is the correct answer for a
      text PDF, not a failure.
    * a photo, screenshot or image upload, or a PDF page flagged ``photo`` →
      the configured model is asked; ``completed`` with the facts it named
      (possibly none — the page entry's ``note`` then says what it answered),
      ``failed`` with the provider's error when it did not answer usably.

    ``filename`` defaults to ``report["filename"]``; ``completion`` is the
    test injection (``(prompt, png_bytes) -> text``).
    """
    started = time.monotonic()
    block: dict[str, Any] = {"status": "not_run", "model": None, "reason": None, "pages": [], "ms": 0}
    try:
        if _env_flag("PARSURE_VISION") in ("0", "false", "no", "off"):
            enabled, model, reason = False, None, "PARSURE_VISION is off"
        elif completion is None:
            enabled, model, reason = vision_enabled()
        else:
            enabled, model, reason = True, "injected", None
        block["model"] = model
        if not enabled:
            block.update(status="disabled", reason=reason)
            return report
        pages = picture_pages(intake, report)
        block["rule"] = picture_rule(intake, report, pages)
        if not pages:
            # The honest answer for a digital PDF: nothing on it is a picture, so
            # the model was not asked. ``rule`` says which test decided.
            block["reason"] = "no picture pages"
            return report
        if not file_bytes:
            block["reason"] = f"{len(pages)} picture page(s) but no file bytes to render"
            return report
        if completion is None:
            blind = model_cannot_see(model)
            if blind:
                block.update(status="failed", reason=blind)
                return report
        limit = _max_pages()
        selected, skipped = pages[:limit], pages[limit:]
        name = filename or report.get("filename") or ""
        document_type = ((report.get("classification") or {}).get("document_type")) or report.get("document_type")
        context = f"document type {document_type}" if document_type else None
        completed = failed = 0
        for page_no, kind in selected:
            t0 = time.monotonic()
            entry: dict[str, Any] = {"page": page_no, "kind": kind, "analysis_type": analysis_type_for(kind, document_type),
                                     "status": "not_run", "quality": None, "facts": [], "reason": None, "ms": 0}
            try:
                png = render_page_png(file_bytes, name, page_no)
                entry["quality"] = picture_quality(png, "page.png")
                if entry["quality"]["status"] == "poor":
                    entry["status"] = "skipped"
                    entry["reason"] = f"picture quality poor: {entry['quality']['basis'].split('. probe:')[0]}"
                else:
                    result = analyze_page(png, analysis_type=entry["analysis_type"], context=context, completion=completion, model=model)
                    entry["status"] = result["status"]
                    entry["reason"] = result["reason"]
                    entry["dropped"] = result["dropped"]
                    entry["calls"] = result["calls"]
                    if result.get("note"):
                        entry["note"] = result["note"]
                    entry["facts"] = [
                        dict(f, grounding={"kind": "image", "page": page_no, "bbox": f.get("bbox")}) for f in result["facts"]
                    ]
                    if result["status"] == "completed":
                        completed += 1
                    else:
                        failed += 1
            except Exception as exc:  # noqa: BLE001 — per-page failure is a status
                log.exception("vision: page %s of %s failed", page_no, name)
                entry["status"] = "failed"
                entry["reason"] = _one_line(exc)
                failed += 1
            entry["ms"] = int((time.monotonic() - t0) * 1000)
            block["pages"].append(entry)
        for page_no, kind in skipped:
            block["pages"].append({"page": page_no, "kind": kind, "analysis_type": analysis_type_for(kind, document_type),
                                   "status": "skipped", "quality": None, "facts": [],
                                   "reason": f"beyond PARSURE_VISION_MAX_PAGES ({limit})", "ms": 0})
        if completed == 0 and failed > 0:
            block["status"] = "failed"
            block["reason"] = next((p["reason"] for p in block["pages"] if p["status"] == "failed"), "every picture failed")
        else:
            block["status"] = "completed"
            if completed == 0:
                block["reason"] = "no picture reached the model: " + "; ".join(
                    f"page {p['page']} {p['reason']}" for p in block["pages"] if p.get("reason")
                )[:600]
            elif failed:
                block["reason"] = f"{failed} of {len(selected)} picture(s) failed"
        return report
    except Exception as exc:  # noqa: BLE001 — the ingest already has its revision; record, never raise
        log.exception("vision: attach_vision failed for %s", report.get("filename"))
        block["status"] = "failed"
        block["reason"] = _one_line(exc)
        return report
    finally:
        block["ms"] = int((time.monotonic() - started) * 1000)
        report["vision"] = block
        try:
            _execution(report, block)
        except Exception:  # noqa: BLE001
            log.exception("vision: execution block failed")
