"""Laya: rules-v1 triage behind the router — annotates, never routes."""

from __future__ import annotations

from pathlib import Path

import prompt_matrix
from prompt_matrix.services import laya


def _page(flags=()):
    return {"page": 1, "flags": list(flags), "blur_variance": 1.0, "contrast_std": 1.0}


def test_result_shape_and_model_identity():
    out = laya.triage(material_type="pdf", modality="digital_pdf", visual_pages=[_page()], parser="jdf")
    assert out["model"] == "rules-v1"
    assert out["policy_version"] == "v1"
    assert out["suggested_route"] in ("jdf", "jdf-ocr", "textract", "text_wrap", "human_review")
    assert isinstance(out["escalate"], bool) and isinstance(out["human_review"], bool)
    assert out["reasons"] and all(isinstance(r, str) for r in out["reasons"])


def test_clean_digital_pdf_keeps_router_decision():
    out = laya.triage(material_type="pdf", modality="digital_pdf", visual_pages=[_page(), _page()], parser="jdf")
    assert out["suggested_route"] == "jdf"
    assert out["escalate"] is False and out["human_review"] is False
    assert out["probed_pages"] == 2 and out["flagged_pages"] == 0


def test_text_material_is_text_wrap():
    out = laya.triage(material_type="text_file", modality="text", visual_pages=[], parser="jdf")
    assert out["suggested_route"] == "text_wrap"
    assert out["escalate"] is False and out["human_review"] is False


def test_no_renderable_page_escalates_to_human_review():
    out = laya.triage(material_type="pdf", modality="digital_pdf", visual_pages=[], parser="jdf")
    assert out["suggested_route"] == "human_review"
    assert out["escalate"] is True and out["human_review"] is True
    assert "no renderable page" in out["reasons"][0]


def test_one_flagged_page_suggests_scan_backend_without_escalation(monkeypatch):
    monkeypatch.setenv("PARSER_SCAN_BACKEND", "textract")
    pages = [_page(["blurry"]), _page(), _page(), _page()]
    out = laya.triage(material_type="pdf", modality="digital_pdf", visual_pages=pages, parser="jdf")
    assert out["suggested_route"] == "textract"
    assert out["escalate"] is False and out["human_review"] is False
    assert "blurry x1" in " ".join(out["reasons"])
    monkeypatch.setenv("PARSER_SCAN_BACKEND", "jdf-ocr")
    out = laya.triage(material_type="pdf", modality="digital_pdf", visual_pages=pages, parser="jdf")
    assert out["suggested_route"] == "jdf-ocr"


def test_half_flagged_escalates():
    pages = [_page(["low_res"]), _page(["low_contrast", "blurry"]), _page(), _page()]
    out = laya.triage(material_type="pdf", modality="scanned_pdf", visual_pages=pages, parser="jdf-ocr")
    assert out["suggested_route"] == "human_review"
    assert out["escalate"] is True and out["human_review"] is True
    assert out["flagged_pages"] == 2
    assert any("50%" in r for r in out["reasons"])


def test_photo_goes_to_scan_backend_even_when_clean(monkeypatch):
    monkeypatch.delenv("PARSER_SCAN_BACKEND", raising=False)
    monkeypatch.delenv("JDF_OCR", raising=False)
    out = laya.triage(material_type="photo", modality="phone_photo", visual_pages=[_page()], parser="jdf-ocr")
    assert out["suggested_route"] == "jdf-ocr"
    assert any("camera photo" in r for r in out["reasons"])
    assert out["escalate"] is False


def test_laya_is_called_only_from_the_router():
    """Spec §8 rule 8: Laya sits behind routing. Only parser_router imports it."""
    root = Path(prompt_matrix.__file__).parent
    importers = []
    for path in root.rglob("*.py"):
        if path.name in ("laya.py", "parser_router.py"):
            continue
        text = path.read_text(encoding="utf-8")
        if "import laya" in text or "from .laya" in text or "services.laya" in text or "laya.triage" in text:
            importers.append(str(path.relative_to(root)))
    assert importers == [], f"Laya must be called from parser_router only, found in {importers}"


def test_laya_never_claims_to_be_ml():
    source = Path(laya.__file__).read_text(encoding="utf-8").lower()
    for phrase in ("machine learning", "neural", "trained model", "classifier"):
        assert phrase not in source
    assert "rules-v1" in source
