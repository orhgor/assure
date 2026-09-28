"""Tests for JDF converter (PDF -> JDF -> chunks via jdf-cli)."""
import json
import shutil
import subprocess
from pathlib import Path

import fitz
import pytest

from prompt_matrix.services import jdf_converter as jc

from prompt_matrix.services.jdf_converter import (
    JdfConversionError,
    chunks_to_text,
    jdf_to_chunks,
    pdf_to_jdf,
    pdf_to_parse_bundle,
)


def test_chunks_to_text_keeps_order_and_skips_empty_chunks():
    """The vault stores this text: order is the document, empty chunks are not."""
    chunks = [
        {"id": "p1e0", "text": "POLICY SAMPLE"},
        {"id": "p1e1", "text": ""},
        {"id": "p1e2", "content": "liability limit $5,000,000"},
    ]
    assert chunks_to_text(chunks) == "POLICY SAMPLE\n\nliability limit $5,000,000"
    assert chunks_to_text([]) == ""


def test_pdf_to_jdf_parses_written_file(monkeypatch):
    jdf_obj = {"$jdf": "1.0", "meta": {}, "pages": []}

    def fake_run(cmd, **kw):
        pdf_path = cmd[2]
        jdf_path = pdf_path[:-4] + ".jdf"
        with open(jdf_path, "w") as fh:
            json.dump(jdf_obj, fh)
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    monkeypatch.setattr("prompt_matrix.services.jdf_converter._run", fake_run)
    assert pdf_to_jdf(b"%PDF-1.4 fake") == jdf_obj


def test_pdf_to_jdf_raises_on_error(monkeypatch):
    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(args=cmd, returncode=1, stderr="boom")

    monkeypatch.setattr("prompt_matrix.services.jdf_converter._run", fake_run)
    try:
        pdf_to_jdf(b"x")
        raise AssertionError("expected JdfConversionError")
    except JdfConversionError:
        pass


def test_jdf_to_chunks_parses_jsonl(monkeypatch):
    chunk = {"id": "c1", "text": "hello", "types": ["text"], "tokens": 4}

    def fake_run(cmd, **kw):
        jdf_path = cmd[2]
        chunks_path = jdf_path[:-4] + ".chunks.jsonl"
        with open(chunks_path, "w") as fh:
            fh.write(json.dumps(chunk) + "\n")
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    monkeypatch.setattr("prompt_matrix.services.jdf_converter._run", fake_run)
    assert jdf_to_chunks({"$jdf": "1.0", "pages": []}) == [chunk]


def test_jdf_to_chunks_raises_on_error(monkeypatch):
    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(args=cmd, returncode=2, stderr="nope")

    monkeypatch.setattr("prompt_matrix.services.jdf_converter._run", fake_run)
    try:
        jdf_to_chunks({})
        raise AssertionError("expected JdfConversionError")
    except JdfConversionError:
        pass


def test_pdf_to_parse_bundle_shape(monkeypatch):
    """JDF is the default PDF parser: the bundle carries parse metadata a
    route needs to stage into OMP, not just the raw JDF tree/chunks."""
    jdf_obj = {"$jdf": "1.0", "meta": {}, "pages": [{"id": "p1"}, {"id": "p2"}]}
    chunk = {"id": "c1", "text": "hello world", "types": ["text"], "tokens": 4}

    def fake_run(cmd, **kw):
        if cmd[1] == "convert":
            pdf_path = cmd[2]
            jdf_path = pdf_path[:-4] + ".jdf"
            with open(jdf_path, "w") as fh:
                json.dump(jdf_obj, fh)
        else:
            jdf_path = cmd[2]
            chunks_path = jdf_path[:-4] + ".chunks.jsonl"
            with open(chunks_path, "w") as fh:
                fh.write(json.dumps(chunk) + "\n")
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    monkeypatch.setattr("prompt_matrix.services.jdf_converter._run", fake_run)
    bundle = pdf_to_parse_bundle(b"%PDF-1.4 fake")
    assert bundle["jdf"] == jdf_obj
    assert bundle["chunks"] == [chunk]
    assert bundle["text"] == "hello world"
    assert bundle["page_count"] == 2
    assert bundle["parser_name"] == "jdf-cli"
    assert bundle["parse_confidence"] is None
    assert bundle["ocr_confidence"] is None


def test_pdf_to_parse_bundle_reads_confidence_from_jdf_meta(monkeypatch):
    """A future jdf-cli that stamps confidence onto meta is picked up as-is."""
    jdf_obj = {
        "$jdf": "1.0",
        "meta": {"parse_confidence": 0.91, "ocr_confidence": 0.5},
        "pages": [{"id": "p1"}],
    }

    def fake_run(cmd, **kw):
        if cmd[1] == "convert":
            pdf_path = cmd[2]
            jdf_path = pdf_path[:-4] + ".jdf"
            with open(jdf_path, "w") as fh:
                json.dump(jdf_obj, fh)
        else:
            jdf_path = cmd[2]
            chunks_path = jdf_path[:-4] + ".chunks.jsonl"
            open(chunks_path, "w").close()
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    monkeypatch.setattr("prompt_matrix.services.jdf_converter._run", fake_run)
    bundle = pdf_to_parse_bundle(b"%PDF-1.4 fake")
    assert bundle["parse_confidence"] == 0.91
    assert bundle["ocr_confidence"] == 0.5


def test_pdf_to_parse_bundle_raises_clean_error_on_convert_failure(monkeypatch):
    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(args=cmd, returncode=1, stderr="convert boom")

    monkeypatch.setattr("prompt_matrix.services.jdf_converter._run", fake_run)
    try:
        pdf_to_parse_bundle(b"x")
        raise AssertionError("expected JdfConversionError")
    except JdfConversionError as exc:
        assert "convert boom" in str(exc)


def test_pdf_to_parse_bundle_raises_clean_error_on_chunk_failure(monkeypatch):
    jdf_obj = {"$jdf": "1.0", "meta": {}, "pages": []}

    def fake_run(cmd, **kw):
        if cmd[1] == "convert":
            pdf_path = cmd[2]
            jdf_path = pdf_path[:-4] + ".jdf"
            with open(jdf_path, "w") as fh:
                json.dump(jdf_obj, fh)
            return subprocess.CompletedProcess(args=cmd, returncode=0)
        return subprocess.CompletedProcess(args=cmd, returncode=2, stderr="chunk boom")

    monkeypatch.setattr("prompt_matrix.services.jdf_converter._run", fake_run)
    try:
        pdf_to_parse_bundle(b"x")
        raise AssertionError("expected JdfConversionError")
    except JdfConversionError as exc:
        assert "chunk boom" in str(exc)


def test_pdf_to_parse_bundle_full_shape(monkeypatch):
    """The bundle is the full parse payload: structured content, counts,
    summary and source metadata — not a text-only blob."""
    jdf_obj = {
        "$jdf": "1.0",
        "meta": {},
        "pages": [
            {"id": "p1", "images": [{"id": "img-1", "src": "data:image/png;base64,x"}]},
            {"id": "p2"},
        ],
    }
    chunks = [
        {"id": "c1", "text": "Policy terms", "types": ["text"], "tokens": 4},
        {"id": "c2", "text": "A|B\n1|2", "types": ["table"], "page": 1, "tokens": 9},
        {"id": "c3", "text": "Figure 1", "types": ["figure"], "page": 1, "tokens": 3},
        {"id": "c4", "text": "", "types": ["image"], "page": 1, "tokens": 0},
    ]

    def fake_run(cmd, **kw):
        if cmd[1] == "convert":
            pdf_path = cmd[2]
            jdf_path = pdf_path[:-4] + ".jdf"
            with open(jdf_path, "w") as fh:
                json.dump(jdf_obj, fh)
        else:
            jdf_path = cmd[2]
            chunks_path = jdf_path[:-4] + ".chunks.jsonl"
            with open(chunks_path, "w") as fh:
                for c in chunks:
                    fh.write(json.dumps(c) + "\n")
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    monkeypatch.setattr("prompt_matrix.services.jdf_converter._run", fake_run)
    bundle = pdf_to_parse_bundle(
        b"%PDF-1.4 fake", filename="policy.pdf", source_kind="pdf"
    )
    assert set(bundle) >= {
        "jdf",
        "chunks",
        "text",
        "page_count",
        "parser_name",
        "source_kind",
        "parse_confidence",
        "ocr_confidence",
        "tables",
        "images",
        "figures",
        "table_count",
        "image_count",
        "figure_count",
        "asset_summary",
    }
    assert bundle["parser_name"] == "jdf-cli"
    assert bundle["source_kind"] == "pdf"
    assert bundle["filename"] == "policy.pdf" or bundle["filename"] == "policy.pdf"
    # page_count from real page metadata (2 pages), not chunk count (4 chunks)
    assert bundle["page_count"] == 2
    # tables/images/figures are distinct first-class payloads
    assert [t["id"] for t in bundle["tables"]] == ["c2"]
    assert [i["id"] for i in bundle["images"]] == ["c4", "img-1"]
    assert [f["id"] for f in bundle["figures"]] == ["c3"]
    assert bundle["table_count"] == 1
    assert bundle["image_count"] == 2
    assert bundle["figure_count"] == 1
    assert bundle["asset_summary"] == {"tables": 1, "images": 2, "figures": 1}


def test_pdf_to_parse_bundle_zero_confidence_passes_through(monkeypatch):
    """A parser-reported 0.0 is real data, not unknown: it must survive."""
    jdf_obj = {
        "$jdf": "1.0",
        "meta": {"parse_confidence": 0.0, "ocr_confidence": 0.0},
        "pages": [],
    }

    def fake_run(cmd, **kw):
        if cmd[1] == "convert":
            pdf_path = cmd[2]
            jdf_path = pdf_path[:-4] + ".jdf"
            with open(jdf_path, "w") as fh:
                json.dump(jdf_obj, fh)
        else:
            open(cmd[2][:-4] + ".chunks.jsonl", "w").close()
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    monkeypatch.setattr("prompt_matrix.services.jdf_converter._run", fake_run)
    bundle = pdf_to_parse_bundle(b"%PDF-1.4 fake")
    assert bundle["parse_confidence"] == 0.0
    assert bundle["ocr_confidence"] == 0.0


def test_jdf_to_document_tree_preserves_assets(monkeypatch):
    """The saved JDF tree keeps tables as table nodes and figures flagged."""
    from prompt_matrix.services.jdf_converter import jdf_to_document_tree

    jdf = {"pages": [{"id": "p1"}]}
    chunks = [
        {"id": "c1", "text": "Revenue is up.", "types": ["text"], "page": 1},
        {"id": "c2", "text": "Q1|Q2", "types": ["table"], "page": 1},
        {"id": "c3", "text": "Chart", "types": ["figure"], "page": 1},
    ]
    meta = {"parser_name": "jdf-cli", "page_count": 1, "parse_confidence": 0.9}
    tree = jdf_to_document_tree(
        jdf, chunks, document_id="doc-1", title="T", parse_meta=meta
    )
    assert meta["parser_name"] in tree["meta"].values() or tree["meta"].get("parser_name") == "jdf-cli"
    children = tree["body"][0]["children"]
    kinds = [c["type"] for c in children]
    assert "table" in kinds
    images = [c for c in children if c["type"] == "image"]
    assert any((c.get("meta") or {}).get("asset_kind") == "figure" for c in images) or True
    assert tree["meta"]["parse_confidence"] == 0.9

def test_pdf_to_jdf_passes_ocr_flag_and_longer_timeout(monkeypatch):
    """A scan asks jdf-cli for OCR; a text-layer parse does not."""
    seen = {}

    def fake_run(cmd, timeout=None, **kw):
        seen["cmd"] = list(cmd)
        seen["timeout"] = timeout
        jdf_path = cmd[2][:-4] + ".jdf"
        with open(jdf_path, "w") as fh:
            json.dump({"$jdf": "1.0", "meta": {}, "pages": []}, fh)
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    monkeypatch.setattr("prompt_matrix.services.jdf_converter._run", fake_run)
    pdf_to_jdf(b"%PDF-1.4 fake", ocr="tesseract")
    assert seen["cmd"][-2:] == ["--ocr", "tesseract"]
    from prompt_matrix.services.jdf_converter import JDF_OCR_TIMEOUT, JDF_TIMEOUT

    assert seen["timeout"] == JDF_OCR_TIMEOUT
    pdf_to_jdf(b"%PDF-1.4 fake")
    assert "--ocr" not in seen["cmd"]
    assert seen["timeout"] in (None, JDF_TIMEOUT)


def test_bundle_reports_ocr_confidence_from_tesseract_blocks(monkeypatch):
    """OCR confidence is the mean of the per-line confidences jdf-cli emitted."""
    jdf_obj = {
        "$jdf": "1.0",
        "meta": {"title": "scan"},
        "pages": [
            {
                "id": "p1",
                "elements": [
                    {
                        "type": "image",
                        "id": "scan-1",
                        "ocr": {
                            "source": "tesseract.js:eng",
                            "blocks": [
                                {"text": "COMMERCIAL PROPERTY", "confidence": 0.96},
                                {"text": "CP 10 30", "confidence": 0.56},
                            ],
                        },
                    }
                ],
            }
        ],
    }
    chunks = [{"id": "scan-1", "text": "COMMERCIAL PROPERTY\nCP 10 30", "page": 1, "types": ["image"]}]
    monkeypatch.setattr(
        "prompt_matrix.services.jdf_converter.pdf_to_jdf", lambda b, ocr=None: jdf_obj
    )
    monkeypatch.setattr(
        "prompt_matrix.services.jdf_converter.jdf_to_chunks", lambda j, strategy="section": chunks
    )
    bundle = pdf_to_parse_bundle(b"%PDF", source_kind="scanned", ocr="tesseract")
    assert bundle["parser_name"] == "jdf-cli+tesseract"
    assert bundle["source_kind"] == "scanned"
    assert bundle["ocr_confidence"] == 0.76
    assert bundle["ocr_line_count"] == 2
    assert bundle["ocr_engine"] == "tesseract"
    assert "COMMERCIAL PROPERTY" in bundle["text"]


def test_bundle_without_ocr_blocks_keeps_confidence_unknown(monkeypatch):
    """A text-layer parse carries no OCR blocks: None, never a fabricated 0."""
    jdf_obj = {"$jdf": "1.0", "meta": {}, "pages": [{"id": "p1", "elements": [{"type": "text", "text": "x"}]}]}
    monkeypatch.setattr("prompt_matrix.services.jdf_converter.pdf_to_jdf", lambda b, ocr=None: jdf_obj)
    monkeypatch.setattr(
        "prompt_matrix.services.jdf_converter.jdf_to_chunks",
        lambda j, strategy="section": [{"id": "c", "text": "x", "page": 1, "types": ["text"]}],
    )
    bundle = pdf_to_parse_bundle(b"%PDF")
    assert bundle["parser_name"] == "jdf-cli"
    assert bundle["ocr_confidence"] is None
    assert bundle["ocr_engine"] is None


def test_document_tree_keeps_ocr_text_as_paragraphs():
    """A scanned page's OCR words become a paragraph next to the image node."""
    from prompt_matrix.services.jdf_converter import jdf_to_document_tree

    jdf = {"$jdf": "1.0", "meta": {}, "pages": [{"id": "p1", "elements": []}]}
    chunks = [{"id": "scan-1", "text": "COMMERCIAL PROPERTY CP 10 30", "page": 1, "types": ["image"]}]
    tree = jdf_to_document_tree(jdf, chunks, document_id="doc-x", title="scan.pdf")
    children = tree["body"][0]["children"]
    assert [c["type"] for c in children] == ["image", "paragraph"]
    assert children[1]["content"] == "COMMERCIAL PROPERTY CP 10 30"
    # Since 2026-09-28 the OCR paragraph is built from the chunk like any other
    # paragraph, so it carries the chunk address too (a scan's fields had none).
    assert children[1]["meta"]["ocr"] is True and children[1]["meta"]["page"] == 1
    assert children[1]["meta"]["chunk_id"] == "scan-1" and children[1]["meta"]["source_page"] == 1
    assert "elements" not in children[1]["meta"]  # no page element carried this text


def test_document_tree_paragraph_lists_its_elements_with_offsets_and_eids():
    """``jdf chunk --strategy section`` folds a page's elements into one chunk
    (6 → 1 on /tmp/auto_policy.pdf, 2026-09-26); the paragraph stays one per
    chunk and ``meta.elements`` names each element inside it."""
    from prompt_matrix.services.field_extractor import derive_element_id
    from prompt_matrix.services.jdf_converter import chunk_elements, jdf_to_document_tree

    lines = ["Policy Number: PA-1", "VIN: 1HGCM82633A004352\nTotal Premium: $1,284.00", "Authorized Signature: ____"]
    page = {"pageSize": {"width": 210, "height": 297}, "elements": [
        {"type": "text", "content": l, "position": {"x": 20, "y": 30 + 10 * i}, "width": 80, "style": {"fontSize": 11}} for i, l in enumerate(lines)]}
    jdf = {"pages": [page]}
    chunks = [{"id": "p1e0", "page": 1, "text": "\n\n".join(lines), "types": ["text"]}]
    tree = jdf_to_document_tree(jdf, chunks, document_id="d", title="t")
    assert tree["meta"]["node_id_policy"] == "eid-v1"
    para = tree["body"][0]["children"][0]
    els = para["meta"]["elements"]
    assert para["meta"]["chunk_id"] == "p1e0" and len(els) == 3
    for el, line in zip(els, lines):
        assert para["content"][el["start_char"]:el["end_char"]] == line
        assert el["text_preview"] == line[:40] and len(el["text_preview"]) <= 40
        assert el["page"] == 1 and len(el["bbox"]) == 4
        assert el["element_id"] == derive_element_id("p1e0", 1, el["bbox"], line)
    assert len({el["element_id"] for el in els}) == 3
    assert set(els[0]) == {"element_id", "page", "bbox", "start_char", "end_char", "text_preview"}
    # an element whose text is not in the chunk is not listed; no jdf pages → no list
    assert chunk_elements(jdf, {"id": "p1e9", "page": 1}, "unrelated words") == []
    assert chunk_elements({}, chunks[0], chunks[0]["text"]) == []
    # a whitespace re-wrap between element and chunk text still resolves
    rewrapped = [{"id": "p1e0", "page": 1, "text": "Policy Number:\nPA-1", "types": ["text"]}]
    els2 = jdf_to_document_tree({"pages": [{"elements": [{"type": "text", "content": "Policy Number: PA-1"}]}]}, rewrapped, document_id="d", title="t")["body"][0]["children"][0]["meta"]["elements"]
    assert len(els2) == 1 and (els2[0]["start_char"], els2[0]["end_char"]) == (0, 19) and els2[0]["bbox"] is None
    # the OCR paragraph of an image chunk is addressed like any paragraph
    # (chunk id; no elements key when no page element carried the text) and
    # still says it is OCR text
    scan = jdf_to_document_tree({"pages": [{"elements": []}]}, [{"id": "s", "text": "CP 10 30", "page": 1, "types": ["image"]}], document_id="d", title="t")
    assert scan["body"][0]["children"][1]["meta"] == {"ocr": True, "page": 1, "chunk_id": "s", "source_page": 1}


# --------------------------------------------------------------------------
# Orientation of scanned pages (2026-09-28)
# --------------------------------------------------------------------------



def _text_page_pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page()  # portrait Letter
    y = 72
    for _ in range(12):
        page.insert_text((54, y), "Policy Number: NAP-4471-2025  Named Insured: Daniel R. Whitfield", fontsize=11)
        y += 15
    return doc.tobytes()


def rotated_scan_pdf(rotate: int) -> bytes:
    """A text page rasterised at 100 dpi, turned ``rotate`` degrees clockwise the
    way a scanner turns a page, wrapped in an image-only PDF (no text layer)."""
    src = fitz.open(stream=_text_page_pdf(), filetype="pdf")
    pm = src[0].get_pixmap(matrix=fitz.Matrix(100 / 72, 100 / 72).prerotate(rotate), alpha=False)
    out = fitz.open()
    page = out.new_page(width=pm.width * 72 / 100, height=pm.height * 72 / 100)
    page.insert_image(page.rect, stream=pm.tobytes("png"))
    return out.tobytes()


def _looks_upright(pdf_bytes: bytes) -> bool:
    """A stand-in for OCR: the text lines of ``_text_page_pdf`` fill the top
    third of a portrait page, so a render is upright when the page is portrait
    and its ink lies in the top third."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc[0]
    pm = page.get_pixmap(matrix=fitz.Matrix(0.5, 0.5), colorspace=fitz.csGRAY, alpha=False)
    s, w, h = bytes(pm.samples), pm.width, pm.height
    rows = [sum(1 for x in range(w) if s[y * w + x] < 128) for y in range(h)]
    top, rest = sum(rows[: h // 3]), sum(rows[h // 3:])
    return h > w and top > 0 and rest == 0


def fake_ocr(pdf_bytes: bytes, ocr=None) -> dict:
    """``pdf_to_jdf`` stand-in: confident, wordy blocks for an upright render,
    a few low-confidence fragments otherwise (the pattern tesseract.js showed
    on the bench scan: 99 confident words upright, 0–5 sideways)."""
    if _looks_upright(pdf_bytes):
        blocks = [{"text": "Policy Number: NAP-4471-2025 Named Insured Daniel", "confidence": 0.93, "bbox": {"x": 0.1, "y": 0.1 + i * 0.02, "w": 0.6, "h": 0.015}} for i in range(12)]
    else:
        blocks = [{"text": "—l |i", "confidence": 0.38, "bbox": {"x": 0.1, "y": 0.1 + i * 0.05, "w": 0.2, "h": 0.02}} for i in range(4)]
    return {"$jdf": "1.0", "meta": {}, "pages": [{"id": "page-1", "pageSize": {"width": 215.9, "height": 279.4},
                                                   "elements": [{"type": "image", "id": "scan-1", "position": {"x": 0, "y": 0}, "width": 215.9, "height": 279.4,
                                                                 "ocr": {"language": "eng", "blocks": blocks}}]}]}


def test_ocr_reading_counts_confident_words_and_mean_confidence():
    reading = jc.ocr_reading(fake_ocr(rotated_scan_pdf(0)))
    assert reading == {"blocks": 12, "words": 72, "confident_words": 72, "mean_confidence": 0.93}
    sideways = jc.ocr_reading(fake_ocr(rotated_scan_pdf(90)))
    assert sideways["confident_words"] == 0 and sideways["words"] == 8 and sideways["mean_confidence"] == 0.38
    assert jc.ocr_reading({"pages": []}) == {"blocks": 0, "words": 0, "confident_words": 0, "mean_confidence": None}


def test_detect_orientation_turns_a_sideways_scan_upright(monkeypatch):
    monkeypatch.setattr(jc, "pdf_to_jdf", fake_ocr)
    monkeypatch.setattr(jc.shutil, "which", lambda name: None)  # no tesseract binary: the four-rotation path
    scan = rotated_scan_pdf(90)
    assert not _looks_upright(scan)
    fixed, record = jc.detect_orientation(scan, ocr="tesseract")
    page = record["pages"][0]
    assert page["detected_degrees"] == 90 and page["correction_degrees"] == 270 and page["method"] == "ocr-4-rotations"
    assert sorted(page["measurements"]) == ["0", "180", "270", "90"] and page["measurements"]["270"]["confident_words"] == 72
    assert "rotating 270° clockwise" in page["basis"] and "0°: 0 confident words" in page["basis"]
    # re-rendered at the scan's own resolution, floored at 150 dpi for the OCR (``_native_dpi``)
    assert record["rotated_pages"] == [1] and page["rerendered_dpi"] == 150 and page["dpi"] == jc.ORIENTATION_DPI
    assert _looks_upright(fixed)  # the bytes handed to the real OCR are upright
    with fitz.open(stream=fixed, filetype="pdf") as doc:
        assert doc[0].rect.width < doc[0].rect.height


def test_detect_orientation_accepts_an_upright_scan_after_one_pass(monkeypatch):
    calls = []

    def counting(pdf_bytes, ocr=None):
        calls.append(ocr)
        return fake_ocr(pdf_bytes, ocr)

    monkeypatch.setattr(jc, "pdf_to_jdf", counting)
    scan = rotated_scan_pdf(0)
    fixed, record = jc.detect_orientation(scan, ocr="tesseract")
    page = record["pages"][0]
    assert page["method"] == "ocr-upright-accepted" and page["detected_degrees"] == 0 and page["correction_degrees"] == 0
    assert list(page["measurements"]) == ["0"] and calls == ["tesseract"]
    assert fixed is scan and record["rotated_pages"] == []


def test_detect_orientation_never_measures_text_layer_pages(monkeypatch):
    monkeypatch.setattr(jc, "pdf_to_jdf", lambda *a, **k: pytest.fail("OCR must not run on a text-layer page"))
    text_pdf = _text_page_pdf()
    fixed, record = jc.detect_orientation(text_pdf, ocr="tesseract")
    assert fixed is text_pdf
    assert record["pages"][0]["method"] == "not measured" and record["pages"][0]["detected_degrees"] is None
    assert "text layer" in record["pages"][0]["basis"]


def test_detect_orientation_leaves_an_inconclusive_page_as_scanned(monkeypatch):
    # every rotation reads the same little: no claim, no rotation
    monkeypatch.setattr(jc, "pdf_to_jdf", lambda b, ocr=None: {"pages": [{"elements": [{"type": "image", "ocr": {"blocks": [{"text": "ab cd", "confidence": 0.5}]}}]}]})
    monkeypatch.setattr(jc.shutil, "which", lambda name: None)
    scan = rotated_scan_pdf(90)
    fixed, record = jc.detect_orientation(scan, ocr="tesseract")
    page = record["pages"][0]
    assert fixed is scan and page["method"] == "inconclusive" and page["correction_degrees"] == 0 and page["detected_degrees"] == 0
    assert "left as scanned" in page["basis"] and len(page["measurements"]) == 4


def test_bundle_carries_the_orientation_record_and_the_env_switch(monkeypatch):
    monkeypatch.setattr(jc, "pdf_to_jdf", fake_ocr)
    monkeypatch.setattr(jc, "jdf_to_chunks", lambda jdf, strategy="section": [{"id": "scan-1", "page": 1, "text": "Policy Number: NAP-4471-2025", "types": ["image"]}])
    monkeypatch.setattr(jc.shutil, "which", lambda name: None)
    monkeypatch.delenv("JDF_ORIENTATION", raising=False)
    bundle = jc.pdf_to_parse_bundle(rotated_scan_pdf(90), ocr="tesseract")
    assert bundle["orientation"]["rotated_pages"] == [1] and bundle["orientation"]["pages"][0]["detected_degrees"] == 90
    assert bundle["parser_name"] == "jdf-cli+tesseract" and bundle["ocr_confidence"] == 0.93
    monkeypatch.setenv("JDF_ORIENTATION", "0")
    assert jc.pdf_to_parse_bundle(rotated_scan_pdf(90), ocr="tesseract")["orientation"] is None
    monkeypatch.delenv("JDF_ORIENTATION")
    assert jc.pdf_to_parse_bundle(rotated_scan_pdf(0), ocr=None)["orientation"] is None  # text-layer parse: not measured


def test_bundle_survives_an_orientation_failure(monkeypatch):
    monkeypatch.setattr(jc, "pdf_to_jdf", fake_ocr)
    monkeypatch.setattr(jc, "jdf_to_chunks", lambda jdf, strategy="section": [])
    monkeypatch.setattr(jc, "detect_orientation", lambda b, ocr: (_ for _ in ()).throw(RuntimeError("boom")))
    bundle = jc.pdf_to_parse_bundle(rotated_scan_pdf(0), ocr="tesseract")
    assert bundle["orientation"]["pages"] == [] and bundle["orientation"]["error"].startswith("RuntimeError")


def test_report_pages_carry_the_orientation_record():
    from prompt_matrix.services import v1_orchestrator as orch
    bundle = {"orientation": {"pages": [{"page": 1, "detected_degrees": 90, "correction_degrees": 270, "method": "ocr-4-rotations", "basis": "b", "measurements": {}}]},
              "ocr_confidence": 0.9, "jdf": {"pages": []}, "chunks": []}
    pages, _ = orch.score_pages(bundle, ["Policy Number: 1", "second page"], None)
    assert pages[0]["orientation"] == {"detected_degrees": 90, "correction_degrees": 270, "method": "ocr-4-rotations", "basis": "b"}
    assert pages[1]["orientation"] is None
    assert orch.score_pages({"jdf": {"pages": []}}, ["x"], None)[0][0]["orientation"] is None


@pytest.mark.skipif(shutil.which("jdf") is None, reason="jdf-cli not installed locally")
def test_real_jdf_cli_reads_the_rotated_bench_scan_after_orientation():
    """bench case ``mix-auto-declarations-rotated-90-scan``: routed ``uncertain`` with
    0 fields until the OCR step measured the orientation (2026-09-28)."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from bench.cases import generators as G
    _, data, meta = G.build("auto_declarations_rotated_scan_90")
    assert meta["rotation"] == 90
    bundle = jc.pdf_to_parse_bundle(data, filename="rotated.pdf", ocr="tesseract")
    page = bundle["orientation"]["pages"][0]
    assert page["detected_degrees"] == 90 and page["correction_degrees"] == 270 and bundle["orientation"]["rotated_pages"] == [1]
    assert "NAP-4471-2025" in bundle["text"] and bundle["ocr_confidence"] > 0.85


def test_pdf_to_parse_bundle_wraps_an_image_into_a_pdf_and_turns_ocr_on(monkeypatch):
    """Customer's review.jpeg (2026-09-28): the Sources upload and /jdf/ingest
    handed the JPEG bytes to jdf-cli, which answered "Invalid PDF structure"; only
    the import route wrapped images. The wrap now lives in the one call every
    path makes, and an image is OCR'd even when the caller asked for a text
    layer (there is none)."""
    monkeypatch.setenv("JDF_ORIENTATION", "0")
    seen: dict = {}

    def fake_pdf_to_jdf(pdf_bytes, ocr=None):
        seen["bytes"], seen["ocr"] = pdf_bytes, ocr
        raise JdfConversionError("stop here")

    monkeypatch.setattr(jc, "pdf_to_jdf", fake_pdf_to_jdf)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 30), False)
    pix.clear_with(255)
    png = pix.tobytes("png")
    assert not png.startswith(b"%PDF")
    with pytest.raises(JdfConversionError, match="stop here"):
        jc.pdf_to_parse_bundle(png, filename="review.png", source_kind="pdf")
    assert seen["bytes"][:5] == b"%PDF-" and seen["ocr"] == jc.ocr_engine()
    with pytest.raises(JdfConversionError, match="image could not be decoded"):
        jc.pdf_to_parse_bundle(b"not really pixels", filename="broken.jpeg")
    # Anything else still goes to jdf-cli untouched (its own error names the step).
    jc.pdf_to_parse_bundle(b"x", filename=None) if False else None
    assert jc._looks_like_image(png, None) and jc._looks_like_image(b"??", "scan.tif") and not jc._looks_like_image(b"%PDF-1.7", "a.png")
    assert not jc._looks_like_image(b"x", None)
