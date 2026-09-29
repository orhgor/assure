"""Textract's reading as a JDF document (``services/textract_jdf``, 2026-09-29).

User decision: every PDF and image goes to Textract and the pipeline continues
on JDF — so the document Textract produces must be the shape jdf-cli emits for
a scan (image element with ``ocr.blocks``, table elements, forms) and the
router must send everything there when ``PARSER_BACKEND=textract``.
"""

from __future__ import annotations

import fitz

from prompt_matrix.services import parser_router, textract_jdf as tj


def _line(bid, text, left, top, width, height, conf=98.5):
    return {"Id": bid, "BlockType": "LINE", "Text": text, "Confidence": conf,
            "Geometry": {"BoundingBox": {"Left": left, "Top": top, "Width": width, "Height": height}}}


def _blocks_page_one():
    words = [{"Id": "w1", "BlockType": "WORD", "Text": "Coverage"}, {"Id": "w2", "BlockType": "WORD", "Text": "Limit"},
             {"Id": "w3", "BlockType": "WORD", "Text": "Dwelling"}, {"Id": "w4", "BlockType": "WORD", "Text": "$425,000"},
             {"Id": "k1", "BlockType": "WORD", "Text": "Policy"}, {"Id": "k2", "BlockType": "WORD", "Text": "Number:"},
             {"Id": "v1", "BlockType": "WORD", "Text": "AP-2025-0001"}]
    cells = [
        {"Id": "c11", "BlockType": "CELL", "RowIndex": 1, "ColumnIndex": 1, "Relationships": [{"Type": "CHILD", "Ids": ["w1"]}]},
        {"Id": "c12", "BlockType": "CELL", "RowIndex": 1, "ColumnIndex": 2, "Relationships": [{"Type": "CHILD", "Ids": ["w2"]}]},
        {"Id": "c21", "BlockType": "CELL", "RowIndex": 2, "ColumnIndex": 1, "Relationships": [{"Type": "CHILD", "Ids": ["w3"]}]},
        {"Id": "c22", "BlockType": "CELL", "RowIndex": 2, "ColumnIndex": 2, "Relationships": [{"Type": "CHILD", "Ids": ["w4"]}]},
    ]
    table = {"Id": "t1", "BlockType": "TABLE", "Confidence": 91.0, "Relationships": [{"Type": "CHILD", "Ids": ["c11", "c12", "c21", "c22"]}],
             "Geometry": {"BoundingBox": {"Left": 0.1, "Top": 0.5, "Width": 0.8, "Height": 0.2}}}
    kv_key = {"Id": "kv1", "BlockType": "KEY_VALUE_SET", "EntityTypes": ["KEY"], "Confidence": 95.0,
              "Relationships": [{"Type": "CHILD", "Ids": ["k1", "k2"]}, {"Type": "VALUE", "Ids": ["kv2"]}],
              "Geometry": {"BoundingBox": {"Left": 0.1, "Top": 0.2, "Width": 0.2, "Height": 0.02}}}
    kv_val = {"Id": "kv2", "BlockType": "KEY_VALUE_SET", "EntityTypes": ["VALUE"], "Relationships": [{"Type": "CHILD", "Ids": ["v1"]}],
              "Geometry": {"BoundingBox": {"Left": 0.32, "Top": 0.2, "Width": 0.2, "Height": 0.02}}}
    return [
        _line("l1", "AUTO POLICY DECLARATIONS", 0.1, 0.05, 0.5, 0.03, 99.2),
        _line("l2", "Policy Number: AP-2025-0001", 0.1, 0.2, 0.42, 0.02, 97.8),
        _line("l3", "Named Insured: John Q. Sample", 0.1, 0.25, 0.45, 0.02, 96.0),
        table, *cells, *words, kv_key, kv_val,
    ]


class FakeBoto:
    def __init__(self):
        self.calls: list[str] = []

    def analyze_document(self, Document, FeatureTypes):
        self.calls.append("analyze")
        return {"Blocks": _blocks_page_one()}

    def detect_document_text(self, Document):
        self.calls.append("detect")
        return {"Blocks": [b for b in _blocks_page_one() if b["BlockType"] == "LINE"]}


def _pdf_bytes(pages=1) -> bytes:
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=612, height=792)  # US Letter
        page.insert_text((72, 72), f"page {i + 1}")
    return doc.tobytes()


def _client(monkeypatch, fake, mode="analyze"):
    from prompt_matrix.lib.textract import TextractClient

    monkeypatch.setenv("ASSURE_TEXTRACT_MODE", mode)
    monkeypatch.setenv("ASSURE_TEXTRACT_MONTHLY_USD_CAP", "1000")
    monkeypatch.setattr(TextractClient, "_charge", staticmethod(lambda pages, api: None))
    return TextractClient(client=fake)


def test_build_jdf_is_the_scan_shape_jdf_cli_emits():
    jdf, chunks, forms = tj.build_jdf([{"page": 1, "blocks": _blocks_page_one()}], filename="decl.pdf", sizes_mm=[(215.9, 279.4)], api="analyze")
    assert jdf["$jdf"] == "1.0" and jdf["meta"]["unit"] == "mm" and jdf["meta"]["source"] == "textract:analyze"
    page = jdf["pages"][0]
    assert page["id"] == "page-1" and page["pageSize"] == {"width": 215.9, "height": 279.4}
    image = page["elements"][0]
    assert image["type"] == "image" and image["id"] == "scan-1" and image["fit"] == "fill" and image["width"] == 215.9
    blocks = image["ocr"]["blocks"]
    assert [b["text"] for b in blocks] == ["AUTO POLICY DECLARATIONS", "Policy Number: AP-2025-0001", "Named Insured: John Q. Sample"]
    assert blocks[0]["confidence"] == 0.992 and blocks[0]["bbox"] == {"x": 0.1, "y": 0.05, "w": 0.5, "h": 0.03}
    assert image["ocr"]["source"] == "textract:analyze"
    table = page["elements"][1]
    assert table["type"] == "table" and table["headers"] == ["Coverage", "Limit"] and table["rows"] == [["Dwelling", "$425,000"]]
    assert table["position"] == {"x": 21.59, "y": 139.7} and table["width"] == 172.72 and table["height"] == 55.88
    assert [c["id"] for c in chunks] == ["scan-1", "tbl-1-1"] and chunks[0]["types"] == ["image"] and chunks[1]["types"] == ["table"]
    assert chunks[0]["text"].startswith("AUTO POLICY DECLARATIONS\nPolicy Number") and chunks[0]["page"] == 1 and chunks[0]["hash"]
    assert chunks[1]["text"] == "Coverage | Limit\nDwelling | $425,000"
    assert forms == [{"key": "Policy Number:", "value": "AP-2025-0001", "page": 1, "key_bbox": {"x": 0.1, "y": 0.2, "w": 0.2, "h": 0.02},
                      "value_bbox": {"x": 0.32, "y": 0.2, "w": 0.2, "h": 0.02}, "confidence": 0.95}]
    # No confidence is invented: a LINE without one carries none.
    jdf2, _, _ = tj.build_jdf([{"page": 1, "blocks": [{"BlockType": "LINE", "Text": "x"}]}], filename=None, sizes_mm=[], api="detect")
    assert "confidence" not in jdf2["pages"][0]["elements"][0]["ocr"]["blocks"][0] and jdf2["meta"]["page_size_source"] == "default_a4"


def test_textract_parse_bundle_has_the_pdf_to_parse_bundle_keys_and_one_call_per_page(monkeypatch):
    fake = FakeBoto()
    bundle = tj.textract_parse_bundle(_pdf_bytes(pages=2), "decl.pdf", client=_client(monkeypatch, fake))
    assert fake.calls == ["analyze", "analyze"]
    for key in ("jdf", "chunks", "text", "page_count", "parser_name", "source_kind", "parse_confidence", "ocr_confidence",
                "tables", "images", "figures", "table_count", "image_count", "figure_count", "asset_summary", "forms", "ocr_engine"):
        assert key in bundle, key
    assert bundle["parser_name"] == "textract" and bundle["page_count"] == 2 and bundle["parse_confidence"] is None
    assert bundle["ocr_confidence"] == 0.9767 and bundle["ocr_line_count"] == 6 and bundle["ocr_engine"] == "textract:analyze"
    assert bundle["table_count"] == 2 and bundle["image_count"] == 2 and len(bundle["forms"]) == 2
    assert bundle["jdf"]["pages"][1]["elements"][0]["id"] == "scan-2" and bundle["jdf"]["pages"][0]["pageSize"] == {"width": 215.9, "height": 279.4}
    assert "Policy Number: AP-2025-0001" in bundle["text"]
    assert bundle["textract"]["api"] == "analyze" and [p["page"] for p in bundle["textract"]["pages"]] == [1, 2]
    # The source-JDF store recognises it and the reader wants the page rasters.
    from prompt_matrix.services.source_jdf import is_source_jdf, pages_are_ocr

    assert is_source_jdf(bundle["jdf"]) and pages_are_ocr(bundle["parser_name"], source_kind=bundle["source_kind"])
    # detect mode: text only, no tables/forms, still the same shape
    fake2 = FakeBoto()
    b2 = tj.textract_parse_bundle(_pdf_bytes(), "scan.pdf", client=_client(monkeypatch, fake2, mode="detect"))
    assert fake2.calls == ["detect"] and b2["table_count"] == 0 and b2["forms"] == [] and b2["ocr_engine"] == "textract:detect"


def test_the_layout_and_table_readers_consume_the_textract_jdf(monkeypatch):
    from prompt_matrix.services import field_extractor as fx
    from prompt_matrix.services import table_extraction as te

    bundle = tj.textract_parse_bundle(_pdf_bytes(), "decl.pdf", client=_client(monkeypatch, FakeBoto()))
    layout = fx.page_layout(bundle)
    assert len(layout) == 1 and layout[0] and layout[0][0]["ocr_confidence"] == 0.9767
    assert layout[0][0]["elements"] and any(e.get("kind") != "element" for e in layout[0][0]["elements"])  # the OCR lines
    tables = te.collect_tables(bundle)
    assert tables and tables[0]["headers"] == ["Coverage", "Limit"] and tables[0]["page"] == 1
    fields = fx.extract_fields("auto_policy", fx.page_texts(bundle), layout=layout, parser_name="textract", parse_confidence=None,
                               ocr_confidence=bundle["ocr_confidence"], page_quality=[0.9], visual_pages=[None])
    pn = next(f for f in fields if f["name"] == "policy_number")
    assert pn["value"] == "AP-2025-0001" and pn["source_span"]["page"] == 1


def test_a_text_only_client_still_yields_a_jdf(monkeypatch):
    class OnlyExtract:
        def extract_text(self, file_bytes, filename):
            return {"text": "Hello\nWorld", "pages": [{"page": 1, "text": "Hello\nWorld"}], "page_count": 1, "tables": [], "forms": []}

    bundle = tj.textract_parse_bundle(b"%PDF-1.4 fake", "x.pdf", client=OnlyExtract())
    assert bundle["ocr_engine"] == "textract:text_only" and bundle["text"] == "Hello\nWorld" and bundle["ocr_confidence"] is None
    assert bundle["jdf"]["pages"][0]["elements"][0]["ocr"]["blocks"][0] == {"text": "Hello"}


def test_parser_backend_textract_routes_every_document_there(monkeypatch):
    monkeypatch.delenv("PARSER_BACKEND", raising=False)
    monkeypatch.delenv("PARSER_SCAN_BACKEND", raising=False)
    assert parser_router.parser_backend() == "auto"
    assert parser_router.select_parser(_pdf_bytes(), filename="text.pdf") == "jdf"
    monkeypatch.setenv("PARSER_BACKEND", "textract")
    assert parser_router.parser_backend() == "textract"
    assert parser_router.select_parser(_pdf_bytes(), filename="text.pdf") == "textract"
    assert parser_router.select_parser(b"\x89PNG....", filename="photo.png") == "textract"
    assert parser_router.select_parser(b"%PDF-1.4", filename=None) == "textract"
    assert parser_router.select_parser(b"notes", filename="notes.txt") == "jdf"  # text-like: wrapped, never parsed
    assert parser_router.select_parser(b"x", filename="a.pdf", source_kind="text") == "jdf"


def test_a_text_selection_on_a_textract_page_narrows_to_the_ocr_lines_box():
    """The page is one image element; the source view must highlight the line,
    not the page (2026-09-29)."""
    from prompt_matrix.services.source_jdf import find_elements_for_text

    jdf, chunks, _ = tj.build_jdf([{"page": 1, "blocks": _blocks_page_one()}], filename="decl.pdf", sizes_mm=[(215.9, 279.4)], api="analyze")
    hit = find_elements_for_text(jdf, "Policy Number: AP-2025-0001", page=1, chunks=chunks)
    assert hit["found"] and hit["element_ids"] and hit["bbox_source"] == "ocr_lines" and hit["ocr_lines"] == 1
    assert hit["bbox_rel"] == [0.1, 0.2, 0.52, 0.22]  # the LINE's own box, page-relative
    whole = find_elements_for_text(jdf, "AUTO POLICY DECLARATIONS\nPolicy Number: AP-2025-0001", page=1, chunks=chunks)
    assert whole["found"] and whole["ocr_lines"] == 2 and whole["bbox_rel"] == [0.1, 0.05, 0.6, 0.22]
    miss = find_elements_for_text(jdf, "not on the page", page=1, chunks=chunks)
    assert miss["found"] is False and miss["element_ids"] == []
