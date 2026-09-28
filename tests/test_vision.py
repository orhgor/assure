"""Vision facts for picture pages (services/vision): off → ``disabled`` with
a reason; a poor picture is never sent to the model and the page says why;
facts carry image grounding; a non-JSON answer is asked once more and then
``failed`` with no facts; nothing in ``fields`` / ``review_summary`` moves.
Every model call is an injected completion — no network."""

from __future__ import annotations

import copy
import json
import time

import fitz  # PyMuPDF, already a dependency
import pytest

from prompt_matrix.services import vision as vz


def _png(width: int, height: int, *, text: str | None = "ODOMETER 123456") -> bytes:
    """A rendered picture: white page, black text and a few shapes — sharp,
    high-contrast, so the probe measures it ``good``/``acceptable``."""
    doc = fitz.open()
    page = doc.new_page(width=width, height=height)
    if text:
        page.insert_text((width * 0.1, height * 0.5), text, fontsize=max(12, height // 8), color=(0, 0, 0))
        shape = page.new_shape()
        shape.draw_rect(fitz.Rect(width * 0.05, height * 0.3, width * 0.95, height * 0.7))
        shape.finish(color=(0, 0, 0), width=4)
        shape.commit()
    png = page.get_pixmap(alpha=False).tobytes("png")
    doc.close()
    return png


def _pdf(pages: int) -> bytes:
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=600, height=800)
        page.insert_text((60, 400), f"PAGE {i + 1} ODOMETER 123456", fontsize=36, color=(0, 0, 0))
    out = doc.tobytes()
    doc.close()
    return out


def _report(**over) -> dict:
    base = {
        "filename": "photo.jpg",
        "material_type": "photo",
        "modality": "phone_photo",
        "page_count": 1,
        "classification": {"document_type": "auto_claim"},
        "fields": [
            {"name": "policy_number", "value": None, "field_state": "not_found", "extraction_confidence": 0.0},
            {"name": "vin", "value": "1HGCM82633A004352", "field_state": "found", "extraction_confidence": 0.85},
        ],
        "review_summary": {"fields_total": 2, "fields_found": 1, "fields_review": 0, "decision": "auto_accept"},
        "pages": [{"page": 1, "flags": [], "visual": {"page": 1, "flags": []}}],
    }
    base.update(over)
    return base


INTAKE_PHOTO = {"material_type": "photo", "modality": "phone_photo", "visual_pages": [{"page": 1, "flags": []}]}
INTAKE_PDF = {"material_type": "pdf", "modality": "digital_pdf", "visual_pages": [{"page": 1, "flags": []}]}

GOOD_ANSWER = {
    "facts": [
        {"name": "odometer_visual", "value": "123456", "confidence": 0.9, "evidence": "digits 123456 printed in the centre", "bbox": [0.1, 0.4, 0.7, 0.6]},
        {"name": "scene_summary", "value": "A dashboard odometer showing 123456", "confidence": None, "evidence": "rectangular frame around a numeric display", "bbox": [100, 300, 950, 700]},
        {"name": "vehicle_count_visual", "value": 1, "confidence": 0.8},  # no evidence → dropped
        {"name": "license_plate_visual", "value": "ABC 123", "evidence": "plate at the bottom"},  # not an accident_scene name → dropped
        {"name": "weather_visual", "value": "sunny", "evidence": "bright light"},  # unknown name → dropped
        "not an object",
    ]
}


def _never(prompt, png):
    pytest.fail("the model must not be called")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in ("PARSURE_VISION", "PARSURE_VISION_MAX_PAGES", "PARSURE_VISION_TIMEOUT_S", "ASSURE_LLM_BACKEND",
                "OPENROUTER_API_KEY", vz.OLLAMA_VISION_ENV, vz.OPENROUTER_VISION_ENV, vz.BEDROCK_VISION_ENV):
        monkeypatch.delenv(key, raising=False)


# ---------------------------------------------------------------- config ---

def test_off_flag_is_disabled_with_reason_and_never_renders(monkeypatch):
    monkeypatch.setenv("PARSURE_VISION", "0")
    report = _report()
    before = copy.deepcopy(report)
    out = vz.attach_vision(report, file_bytes=b"not even an image", intake=INTAKE_PHOTO, completion=_never)
    assert out is report
    assert report["vision"]["status"] == "disabled" and report["vision"]["reason"] == "PARSURE_VISION is off"
    assert report["vision"]["pages"] == [] and report["vision"]["model"] is None
    assert report["execution"]["vision"] == {"status": "disabled", "model": None, "pages_analyzed": 0, "facts": 0,
                                             "ms": report["vision"]["ms"], "reason": "PARSURE_VISION is off"}
    assert report["fields"] == before["fields"] and report["review_summary"] == before["review_summary"]


def test_backend_without_a_vision_model_is_disabled_with_reason(monkeypatch):
    monkeypatch.setenv("ASSURE_LLM_BACKEND", "cloud")
    enabled, model, reason = vz.vision_enabled()
    assert enabled is False and model is None and "no vision model for backend 'cloud'" in reason
    report = _report()
    vz.attach_vision(report, file_bytes=_png(800, 600), intake=INTAKE_PHOTO)
    assert report["vision"]["status"] == "disabled" and "no vision model" in report["vision"]["reason"]


def test_ollama_default_is_off_until_a_vision_model_is_named(monkeypatch):
    monkeypatch.setenv("ASSURE_LLM_BACKEND", "ollama")
    enabled, model, reason = vz.vision_enabled()
    assert enabled is False and model == "ollama/qwen2.5vl:3b" and vz.OLLAMA_VISION_ENV in reason
    monkeypatch.setenv("PARSURE_VISION", "1")
    assert vz.vision_enabled() == (True, "ollama/qwen2.5vl:3b", None)
    monkeypatch.delenv("PARSURE_VISION")
    monkeypatch.setenv(vz.OLLAMA_VISION_ENV, "llava:7b")
    assert vz.vision_enabled() == (True, "ollama/llava:7b", None)


def test_openrouter_and_bedrock_models_resolve(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    assert vz.vision_enabled() == (True, "openrouter/amazon/nova-lite-v1", None)
    monkeypatch.setenv(vz.OPENROUTER_VISION_ENV, "qwen/qwen2.5-vl-72b-instruct")
    assert vz.vision_model("openrouter") == ("openrouter/qwen/qwen2.5-vl-72b-instruct", None)
    monkeypatch.setenv("ASSURE_LLM_BACKEND", "bedrock")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")
    assert vz.vision_model() == ("bedrock/eu.anthropic.claude-sonnet-5", None)
    monkeypatch.setenv(vz.BEDROCK_VISION_ENV, "anthropic.claude-opus-5")
    assert vz.vision_model() == ("bedrock/eu.anthropic.claude-opus-5", None)


def test_text_only_ollama_tag_is_refused_before_any_picture_is_sent(monkeypatch):
    monkeypatch.setenv("ASSURE_LLM_BACKEND", "ollama")
    monkeypatch.setenv(vz.OLLAMA_VISION_ENV, "qwen2.5:1.5b")
    monkeypatch.setattr(vz, "_ollama_capabilities", lambda model: ["completion", "tools"])
    monkeypatch.setattr(vz, "default_completion", lambda *a, **k: pytest.fail("no model call for a blind model"))
    report = _report()
    vz.attach_vision(report, file_bytes=_png(1000, 700), intake=INTAKE_PHOTO)
    assert report["vision"]["status"] == "failed" and report["vision"]["pages"] == []
    assert report["vision"]["reason"].startswith("ollama/qwen2.5:1.5b has no vision capability (ollama /api/show capabilities: ['completion', 'tools'])")
    res = vz.analyze_page(_png(800, 600), model="ollama/qwen2.5:1.5b")
    assert res["status"] == "failed" and res["calls"] == 0 and "no vision capability" in res["reason"]
    # a tag that lists vision, or a daemon that cannot say, is not refused here
    monkeypatch.setattr(vz, "_ollama_capabilities", lambda model: ["completion", "vision"])
    assert vz.model_cannot_see("ollama/qwen2.5vl:3b") is None
    monkeypatch.setattr(vz, "_ollama_capabilities", lambda model: None)
    assert vz.model_cannot_see("ollama/qwen2.5vl:3b") is None
    assert vz.model_cannot_see("openrouter/amazon/nova-lite-v1") is None


# --------------------------------------------------------------- quality ---

def test_picture_quality_reports_measured_numbers():
    q = vz.picture_quality(_png(1000, 700))
    assert q["status"] in ("good", "acceptable") and q["width"] == 1000 and q["height"] == 700
    assert isinstance(q["sharpness"], float) and isinstance(q["contrast_range"], float)
    assert "probe:" in q["basis"] and "low_res" not in q["basis"].split(". probe:")[0]
    tiny = vz.picture_quality(_png(200, 150))
    assert tiny["status"] == "poor" and "too_small" in tiny["flags"] and "long side 200 px < 480" in tiny["basis"]
    bad = vz.picture_quality(b"\x00\x01 not an image")
    assert bad["status"] == "poor" and bad["flags"] == ["not_renderable"] and bad["width"] is None


def test_poor_picture_skips_the_model_and_says_why():
    report = _report()
    vz.attach_vision(report, file_bytes=_png(200, 150), intake=INTAKE_PHOTO, completion=_never)
    block = report["vision"]
    assert block["status"] == "completed" and block["model"] == "injected"
    page = block["pages"][0]
    assert page["status"] == "skipped" and page["facts"] == [] and page["quality"]["status"] == "poor"
    assert page["reason"].startswith("picture quality poor: poor: long side 200 px < 480")
    assert block["reason"].startswith("no picture reached the model: page 1 picture quality poor")
    ex = report["execution"]["vision"]
    assert ex["pages_analyzed"] == 0 and ex["facts"] == 0 and ex["status"] == "completed"


# ----------------------------------------------------------------- facts ---

def test_facts_are_parsed_with_grounding_and_unsupported_ones_dropped():
    seen = {}

    def completion(prompt, png):
        seen["prompt"] = prompt
        seen["png"] = png
        return "```json\n" + json.dumps(GOOD_ANSWER) + "\n```"

    report = _report()
    before = copy.deepcopy(report)
    vz.attach_vision(report, file_bytes=_png(1000, 700), intake=INTAKE_PHOTO, completion=completion)
    block = report["vision"]
    assert block["status"] == "completed" and block["reason"] is None
    page = block["pages"][0]
    assert page["kind"] == "photo" and page["analysis_type"] == "accident_scene" and page["status"] == "completed"
    # accident_scene allows scene_summary but not odometer_visual: the vocabulary is per analysis type
    names = [f["name"] for f in page["facts"]]
    assert names == ["scene_summary"]
    fact = page["facts"][0]
    assert fact["value"].startswith("A dashboard odometer") and fact["confidence"] is None
    assert fact["evidence"] == "rectangular frame around a numeric display"
    assert fact["bbox"] == [0.1, 0.3, 0.95, 0.7]  # 0–1000 grid scaled down
    assert fact["grounding"] == {"kind": "image", "page": 1, "bbox": [0.1, 0.3, 0.95, 0.7]}
    assert fact["model"] == "injected"
    assert page["dropped"] == {"not_object": 1, "unknown_name": 3, "no_evidence": 1}
    assert seen["png"][:8] == b"\x89PNG\r\n\x1a\n" and "document type auto_claim" in seen["prompt"]
    assert "odometer_visual" not in seen["prompt"] and "vehicle_count_visual" in seen["prompt"]
    # counts and fields are exactly what they were
    assert report["fields"] == before["fields"] and report["review_summary"] == before["review_summary"]
    ex = report["execution"]["vision"]
    assert ex == {"status": "completed", "model": "injected", "pages_analyzed": 1, "facts": 1, "ms": block["ms"], "reason": None}


def test_document_photo_vocabulary_for_a_screenshot_and_empty_answer_is_empty():
    report = _report(material_type="screenshot", modality="screenshot", classification={"document_type": "auto_claim"})
    intake = {"material_type": "screenshot", "modality": "screenshot", "visual_pages": [{"page": 1, "flags": []}]}
    vz.attach_vision(report, file_bytes=_png(1200, 700), intake=intake, completion=lambda p, b: '{"facts": []}')
    page = report["vision"]["pages"][0]
    assert page["analysis_type"] == "document_photo" and page["status"] == "completed" and page["facts"] == []
    assert report["execution"]["vision"]["facts"] == 0 and report["execution"]["vision"]["pages_analyzed"] == 1
    # analyze_page standalone keeps the odometer under document_photo
    res = vz.analyze_page(_png(1000, 700), analysis_type="document_photo", completion=lambda p, b: json.dumps(GOOD_ANSWER))
    assert [f["name"] for f in res["facts"]] == ["odometer_visual", "scene_summary", "license_plate_visual"]
    assert res["facts"][0]["value"] == "123456" and res["facts"][0]["confidence"] == 0.9 and res["facts"][0]["bbox"] == [0.1, 0.4, 0.7, 0.6]
    assert res["calls"] == 1 and res["status"] == "completed"


def test_non_json_answer_is_retried_once_then_failed_with_no_facts():
    calls = []

    def prose(prompt, png):
        calls.append(prompt)
        return "I can see a car with a dented bumper."

    report = _report()
    vz.attach_vision(report, file_bytes=_png(1000, 700), intake=INTAKE_PHOTO, completion=prose)
    block = report["vision"]
    page = block["pages"][0]
    assert len(calls) == 2 and page["calls"] == 2
    assert page["status"] == "failed" and page["facts"] == []
    assert page["reason"].startswith("ValueError: model answer was not JSON after 2 call(s)")
    assert block["status"] == "failed" and block["reason"] == page["reason"]
    assert report["execution"]["vision"]["status"] == "failed" and report["execution"]["vision"]["facts"] == 0


def test_provider_error_and_timeout_are_failed_with_the_exception_class():
    def boom(prompt, png):
        raise ConnectionError("refused")

    res = vz.analyze_page(_png(800, 600), completion=boom)
    assert res["status"] == "failed" and res["reason"].startswith("ConnectionError: refused") and res["facts"] == []

    def slow(prompt, png):
        time.sleep(1.5)
        return '{"facts": []}'

    res = vz.analyze_page(_png(800, 600), completion=slow, timeout_s=0.2)
    assert res["status"] == "failed" and res["reason"].startswith("TimeoutError") and res["calls"] == 1


def test_attach_never_raises_even_when_the_report_is_odd():
    class Weird(dict):
        def get(self, key, default=None):
            if key == "classification":
                raise RuntimeError("broken report")
            return super().get(key, default)

    report = Weird(_report())
    out = vz.attach_vision(report, file_bytes=_png(800, 600), intake=INTAKE_PHOTO, completion=lambda p, b: '{"facts": []}')
    assert out["vision"]["status"] == "failed" and out["vision"]["reason"].startswith("RuntimeError: broken report")
    assert out["execution"]["vision"]["status"] == "failed"


# ----------------------------------------------------------------- pages ---

def test_digital_pdf_without_photo_flags_is_not_run():
    """The rule, stated on the block (2026-09-28): a digital PDF with no page
    flagged ``photo`` is ``not_run`` / "no picture pages" and ``rule`` says which
    test decided — the correct answer for a text PDF, legible as such."""
    report = _report(filename="policy.pdf", material_type="pdf", modality="digital_pdf")
    vz.attach_vision(report, file_bytes=_pdf(1), intake=INTAKE_PDF, completion=_never)
    assert report["vision"]["status"] == "not_run" and report["vision"]["pages"] == []
    assert report["vision"]["reason"] == "no picture pages"
    rule = report["vision"]["rule"]
    assert rule["modality"] == "digital_pdf" and rule["every_page_is_a_picture"] is False
    assert rule["pages_flagged_photo"] == 0 and rule["picture_pages"] == 0
    assert "phone_photo" in rule["picture_modalities"] and "image" in rule["picture_materials"]
    assert "are not picture kinds" in rule["basis"]
    assert report["execution"]["vision"] == {"status": "not_run", "model": "injected", "pages_analyzed": 0, "facts": 0,
                                             "ms": report["vision"]["ms"], "reason": "no picture pages"}


def test_a_photo_runs_the_configured_model_and_an_empty_answer_says_so():
    """A photo is analysed; when the model answers and names no fact, the page
    entry says what came back (``note``) instead of a bare ``facts: []`` —
    live 2026-09-28: Nova Lite answered a rendered text page in 2 calls and
    named nothing, and the report read as if it had not been asked."""
    calls: list[str] = []

    def answers_nothing(prompt: str, png: bytes) -> str:
        calls.append(prompt)
        return "Sure — here is what I see." if len(calls) == 1 else '{"facts": []}'

    report = _report()
    vz.attach_vision(report, file_bytes=_png(800, 600), intake=INTAKE_PHOTO, completion=answers_nothing)
    block = report["vision"]
    assert block["status"] == "completed" and block["rule"]["every_page_is_a_picture"] is True
    (page,) = block["pages"]
    assert page["status"] == "completed" and page["facts"] == [] and page["calls"] == 2
    assert page["note"].startswith("the model answered and named no fact (0 offered, 0 dropped)")
    assert "first answer was not JSON, asked 2 times" in page["note"]
    assert "answer starts: '{\"facts\": []}'" in page["note"]
    assert report["execution"]["vision"]["pages_analyzed"] == 1 and report["execution"]["vision"]["facts"] == 0

    # A page that named facts carries no note — the facts are the answer.
    report = _report()
    vz.attach_vision(report, file_bytes=_png(800, 600), intake=INTAKE_PHOTO,
                     completion=lambda p, b: json.dumps({"facts": [{"name": "scene_summary", "value": "a page", "evidence": "printed text"}]}))
    assert "note" not in report["vision"]["pages"][0] and len(report["vision"]["pages"][0]["facts"]) == 1


def test_no_vision_model_names_the_model_table(monkeypatch):
    monkeypatch.setenv("ASSURE_LLM_BACKEND", "cloud")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    model, why = vz.vision_model()
    assert model is None
    assert "vision models: ollama=ASSURE_OLLAMA_MODEL_VISION (default qwen2.5vl:3b, off until set)" in why
    assert "openrouter=ASSURE_OPENROUTER_MODEL_VISION (default amazon/nova-lite-v1)" in why
    assert "bedrock=ASSURE_BEDROCK_MODEL_VISION" in why
    report = _report()
    vz.attach_vision(report, file_bytes=_png(800, 600), intake=INTAKE_PHOTO)
    assert report["vision"]["status"] == "disabled" and "vision models:" in report["vision"]["reason"]
    monkeypatch.setenv("ASSURE_LLM_BACKEND", "ollama")
    monkeypatch.delenv(vz.OLLAMA_VISION_ENV, raising=False)
    enabled, _model, reason = vz.vision_enabled()
    assert enabled is False and "vision models:" in reason


def test_photo_flagged_pages_of_a_pdf_are_selected_and_max_pages_bounds_them(monkeypatch):
    monkeypatch.setenv("PARSURE_VISION_MAX_PAGES", "1")
    intake = {"material_type": "pdf", "modality": "scanned_pdf",
              "visual_pages": [{"page": 1, "flags": ["photo"]}, {"page": 2, "flags": []}, {"page": 3, "flags": ["photo"]}]}
    assert vz.picture_pages(intake) == [(1, "photo"), (3, "photo")]
    report = _report(filename="bundle.pdf", material_type="pdf", modality="scanned_pdf", page_count=3,
                     classification={"document_type": "property_claim"})
    vz.attach_vision(report, file_bytes=_pdf(3), intake=intake, completion=lambda p, b: '{"facts": []}')
    pages = report["vision"]["pages"]
    assert [(p["page"], p["status"]) for p in pages] == [(1, "completed"), (3, "skipped")]
    assert pages[0]["analysis_type"] == "property_damage"
    assert pages[1]["reason"] == "beyond PARSURE_VISION_MAX_PAGES (1)"
    assert report["execution"]["vision"]["pages_analyzed"] == 1


def test_no_file_bytes_is_not_run_with_reason():
    report = _report()
    vz.attach_vision(report, file_bytes=None, intake=INTAKE_PHOTO, completion=_never)
    assert report["vision"]["status"] == "not_run" and report["vision"]["reason"] == "1 picture page(s) but no file bytes to render"


def test_render_page_png_caps_the_long_side_and_never_upscales_an_image():
    big = fitz.open()
    big.new_page(width=2000, height=1500).insert_text((100, 700), "X", fontsize=200)
    pdf_png = vz.render_page_png(big.tobytes(), "big.pdf", 1)
    pm = fitz.Pixmap(pdf_png)
    assert max(pm.width, pm.height) == vz.RENDER_LONG_SIDE_PX
    small_png = vz.render_page_png(_png(640, 480), "small.png", 1)
    pm2 = fitz.Pixmap(small_png)
    assert (pm2.width, pm2.height) == (640, 480)
    with pytest.raises(ValueError):
        vz.render_page_png(_pdf(1), "one.pdf", 2)
