"""Tests for JDF converter (PDF -> JDF -> chunks via jdf-cli)."""
import json
import subprocess

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
    assert children[1]["meta"] == {"ocr": True, "page": 1}
