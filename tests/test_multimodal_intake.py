"""Multimodal intake: images route through the one router into the parse pipeline.

Covers ``parser_router.route_intake`` end to end, ``pdf_ingest`` on image
uploads (PDF wrapping for jdf-cli OCR, PNG re-encoding for Textract, the
guarded Parsure hook) and the upload routes' accepted types.
"""

from __future__ import annotations

import importlib.util
import io
import struct
import sys
import types

import fitz
import pytest

from prompt_matrix.services import parser_router
from prompt_matrix.services.parser_router import route_intake

from tests.test_quality_probe import crisp_pdf, image_of, rerender_pdf, solid_image

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def bmp_image(width: int = 40, height: int = 30) -> bytes:
    """A white 24-bit BMP built by hand: PyMuPDF decodes BMP but cannot write it,
    and BMP is a format Textract does not read — the re-encode case."""
    row = (width * 3 + 3) & ~3
    pixels = b"".join((b"\xff\xff\xff" * width + b"\x00" * (row - width * 3)) for _ in range(height))
    header = b"BM" + struct.pack("<IHHI", 54 + len(pixels), 0, 0, 54)
    dib = struct.pack("<IiiHHIIiiII", 40, width, height, 1, 24, 0, len(pixels), 2835, 2835, 0, 0)
    return header + dib + pixels


def _bundle(**overrides):
    base = {
        "jdf": {"$jdf": "1.0", "meta": {}, "pages": []},
        "chunks": [{"id": "c0", "text": "Policy number 4471-0021 premium 1240.", "tokens": 8}],
        "text": "Policy number 4471-0021 premium 1240.",
        "page_count": 1,
        "parser_name": "jdf-cli+tesseract",
        "source_kind": "scanned",
        "parse_confidence": None,
        "ocr_confidence": 0.91,
        "tables": [],
        "images": [],
        "figures": [],
        "table_count": 0,
        "image_count": 0,
        "figure_count": 0,
        "asset_summary": {"tables": 0, "images": 0, "figures": 0},
    }
    base.update(overrides)
    return base


@pytest.fixture
def ingest_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "intake.db"))
    monkeypatch.setenv("PARSE_ASYNC", "0")
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db

    init_db()
    return monkeypatch


# --------------------------------------------------------------------------- #
# route_intake
# --------------------------------------------------------------------------- #


class TestRouteIntake:
    def test_png_routes_to_scan_backend_with_probe_and_laya(self, monkeypatch):
        monkeypatch.setenv("PARSER_SCAN_BACKEND", "textract")
        out = route_intake(image_of(crisp_pdf(), 150), "scan.png")
        assert out["parser"] == "textract"
        assert out["material_type"] == "image" and out["source_kind"] == "image"
        assert len(out["visual_pages"]) == 1 and out["visual_pages"][0]["page"] == 1
        assert out["laya"]["model"] == "rules-v1"
        assert out["laya"]["suggested_route"] in ("textract", "human_review")

    def test_digital_pdf_stays_jdf(self, monkeypatch):
        monkeypatch.delenv("PARSER_SCAN_BACKEND", raising=False)
        out = route_intake(crisp_pdf(pages=2), "policy.pdf")
        assert out["parser"] == "jdf"
        assert (out["material_type"], out["modality"]) == ("pdf", "digital_pdf")
        assert len(out["visual_pages"]) == 2
        assert out["laya"]["suggested_route"] == "jdf" and out["laya"]["escalate"] is False

    def test_scanned_pdf_routes_and_probes(self, monkeypatch):
        monkeypatch.delenv("PARSER_SCAN_BACKEND", raising=False)
        monkeypatch.delenv("JDF_OCR", raising=False)
        out = route_intake(rerender_pdf(crisp_pdf(), 60), "scan.pdf")
        assert out["parser"] == "jdf-ocr"
        assert out["modality"] == "scanned_pdf"
        assert "low_res" in out["visual_pages"][0]["flags"]
        assert out["laya"]["flagged_pages"] == 1

    def test_text_source_kind_is_text_wrap(self):
        out = route_intake(b"plain notes", "notes.txt", source_kind="text")
        assert out["parser"] == "jdf"
        assert out["material_type"] == "text_file"
        assert out["visual_pages"] == []
        assert out["laya"]["suggested_route"] == "text_wrap"

    def test_probe_failure_never_changes_the_parser(self, monkeypatch):
        import prompt_matrix.services.quality_probe as qp

        def boom(*a, **k):
            raise RuntimeError("probe exploded")

        monkeypatch.setattr(qp, "probe_visual_quality", boom)
        out = route_intake(crisp_pdf(), "policy.pdf")
        assert out["parser"] == "jdf"
        assert out["visual_pages"] == []

    def test_laya_never_overrides_select_parser(self, monkeypatch):
        # Whatever Laya suggests, ``parser`` is select_parser's answer.
        monkeypatch.setenv("PARSER_SCAN_BACKEND", "textract")
        out = route_intake(solid_image(2000, 1500, "jpg"), "IMG_1.jpg")
        assert out["parser"] == parser_router.select_parser(b"", "IMG_1.jpg")
        assert out["material_type"] == "photo"


# --------------------------------------------------------------------------- #
# pdf_ingest with images
# --------------------------------------------------------------------------- #


class TestIngestImages:
    def test_image_is_wrapped_as_pdf_for_jdf_ocr(self, ingest_env, monkeypatch):
        monkeypatch.setenv("PARSER_SCAN_BACKEND", "jdf-ocr")
        import prompt_matrix.services.jdf_converter as conv
        from prompt_matrix.services.pdf_ingest import ingest_pdf_for_project

        calls = []

        def fake_bundle(data, **kwargs):
            calls.append((data, kwargs))
            return _bundle()

        monkeypatch.setattr(conv, "pdf_to_parse_bundle", fake_bundle)
        png = image_of(crisp_pdf(), 150)
        payload = ingest_pdf_for_project("img1", "scan.png", png)

        assert len(calls) == 1
        data, kwargs = calls[0]
        assert data[:5] == b"%PDF-", "jdf-cli reads PDF: the image must be wrapped"
        assert kwargs["source_kind"] == "scanned" and kwargs["ocr"]
        assert kwargs["filename"] == "scan.png"
        with fitz.open(stream=data, filetype="pdf") as doc:
            assert len(doc) == 1
        assert payload["ok"] is True
        assert payload["parser_name"] == "jdf-cli+tesseract"
        assert payload["material_type"] == "image"
        assert payload["laya"]["model"] == "rules-v1"
        # The Parsure hook is optional: a build without services/v1_orchestrator
        # reports None; with it, the orchestrator's report id is passed through.
        if importlib.util.find_spec("prompt_matrix.services.v1_orchestrator") is None:
            assert payload["parsure_report_id"] is None
        else:
            assert isinstance(payload["parsure_report_id"], str) and payload["parsure_report_id"]

    def test_bmp_is_reencoded_to_png_for_textract(self, ingest_env, monkeypatch):
        monkeypatch.setenv("PARSER_SCAN_BACKEND", "textract")
        import prompt_matrix.routers.jdf_routes as jdf_routes
        from prompt_matrix.services.pdf_ingest import ingest_pdf_for_project

        seen = []

        def fake_textract(data, filename):
            seen.append((data, filename))
            return _bundle(parser_name="textract", source_kind="pdf", ocr_confidence=None)

        monkeypatch.setattr(jdf_routes, "_textract_parse_bundle", fake_textract)
        payload = ingest_pdf_for_project("img2", "scan.bmp", bmp_image())

        assert len(seen) == 1
        data, filename = seen[0]
        assert data[:8] == PNG_MAGIC
        assert filename.endswith(".png")
        assert payload["parser_name"] == "textract"

    def test_png_goes_to_textract_untouched(self, ingest_env, monkeypatch):
        monkeypatch.setenv("PARSER_SCAN_BACKEND", "textract")
        import prompt_matrix.routers.jdf_routes as jdf_routes
        from prompt_matrix.services.pdf_ingest import ingest_pdf_for_project

        seen = []
        monkeypatch.setattr(
            jdf_routes,
            "_textract_parse_bundle",
            lambda data, filename: seen.append((data, filename)) or _bundle(parser_name="textract"),
        )
        png = image_of(crisp_pdf(), 100)
        ingest_pdf_for_project("img3", "scan.png", png)
        assert seen == [(png, "scan.png")]

    def test_undecodable_image_is_a_400(self, ingest_env, monkeypatch):
        from prompt_matrix.services.pdf_ingest import PdfIngestError, ingest_pdf_for_project

        with pytest.raises(PdfIngestError) as exc:
            ingest_pdf_for_project("img4", "broken.png", b"\x00\x01\x02 not an image")
        assert exc.value.http_status == 400

    def test_bmp_wraps_as_pdf_for_jdf_ocr(self, ingest_env, monkeypatch):
        monkeypatch.setenv("PARSER_SCAN_BACKEND", "jdf-ocr")
        import prompt_matrix.services.jdf_converter as conv
        from prompt_matrix.services.pdf_ingest import ingest_pdf_for_project

        seen = []
        monkeypatch.setattr(conv, "pdf_to_parse_bundle", lambda data, **k: seen.append(data) or _bundle())
        ingest_pdf_for_project("img7", "old.bmp", bmp_image())
        assert seen and seen[0][:5] == b"%PDF-"

    def test_parsure_hook_receives_intake_and_its_report_id_is_returned(self, ingest_env, monkeypatch):
        monkeypatch.setenv("PARSER_SCAN_BACKEND", "jdf-ocr")
        import prompt_matrix.services.jdf_converter as conv
        from prompt_matrix.services.pdf_ingest import ingest_pdf_for_project

        monkeypatch.setattr(conv, "pdf_to_parse_bundle", lambda data, **k: _bundle())
        received = {}

        def run_after_parse(project_id, **kwargs):
            received["project_id"] = project_id
            received.update(kwargs)
            return {"report_id": "rep-1"}

        fake = types.ModuleType("prompt_matrix.services.v1_orchestrator")
        fake.run_after_parse = run_after_parse
        monkeypatch.setitem(sys.modules, "prompt_matrix.services.v1_orchestrator", fake)

        payload = ingest_pdf_for_project("img5", "scan.png", image_of(crisp_pdf(), 100), job_id=None)
        assert payload["parsure_report_id"] == "rep-1"
        assert received["project_id"] == "img5"
        assert set(received) >= {"bundle", "verification", "filename", "file_bytes", "result", "job_id", "intake"}
        assert received["intake"]["parser"] == "jdf-ocr"
        assert received["intake"]["material_type"] == "image"
        assert received["bundle"]["parser_name"] == "jdf-cli+tesseract"
        assert received["result"].get("version") is not None

    def test_parsure_hook_failure_leaves_parse_result_intact(self, ingest_env, monkeypatch):
        monkeypatch.setenv("PARSER_SCAN_BACKEND", "jdf-ocr")
        import prompt_matrix.services.jdf_converter as conv
        from prompt_matrix.services.pdf_ingest import ingest_pdf_for_project

        monkeypatch.setattr(conv, "pdf_to_parse_bundle", lambda data, **k: _bundle())

        def run_after_parse(*a, **k):
            raise RuntimeError("parsure down")

        fake = types.ModuleType("prompt_matrix.services.v1_orchestrator")
        fake.run_after_parse = run_after_parse
        monkeypatch.setitem(sys.modules, "prompt_matrix.services.v1_orchestrator", fake)

        payload = ingest_pdf_for_project("img6", "scan.png", image_of(crisp_pdf(), 100))
        assert payload["ok"] is True
        assert payload["parsure_report_id"] is None


# --------------------------------------------------------------------------- #
# upload routes
# --------------------------------------------------------------------------- #


@pytest.fixture
def client(ingest_env):
    from prompt_matrix.web import create_app

    return create_app(require_auth=False).test_client()


class TestUploadRoutes:
    @pytest.mark.parametrize(
        "filename,content_type",
        [
            ("policy.pdf", "application/pdf"),
            ("scan.png", "image/png"),
            ("photo.JPG", "image/jpeg"),
            ("fax.tiff", "image/tiff"),
            ("old.bmp", "application/octet-stream"),
            ("notes.txt", "text/plain"),
            ("notes.md", None),  # content type guessed from the name
        ],
    )
    def test_presign_accepts_documents_images_and_text(self, client, filename, content_type):
        body = {"filename": filename, "size_bytes": 1024}
        if content_type:
            body["content_type"] = content_type
        res = client.post("/api/projects/p1/uploads/presign", json=body)
        assert res.status_code == 200, res.get_json()
        assert res.get_json()["ok"] is True

    @pytest.mark.parametrize(
        "filename,content_type",
        [
            ("evil.exe", "application/octet-stream"),
            ("page.html", "text/html"),
            ("scan.png", "application/x-msdownload"),
            ("archive.zip", "application/zip"),
        ],
    )
    def test_presign_rejects_other_types(self, client, filename, content_type):
        res = client.post(
            "/api/projects/p1/uploads/presign",
            json={"filename": filename, "content_type": content_type, "size_bytes": 10},
        )
        assert res.status_code == 400

    def test_presign_keeps_size_limit(self, client):
        res = client.post(
            "/api/projects/p1/uploads/presign",
            json={"filename": "scan.png", "content_type": "image/png", "size_bytes": 10**12},
        )
        assert res.status_code == 413

    def test_import_pdf_accepts_a_png_upload(self, client, monkeypatch):
        monkeypatch.setenv("PARSER_SCAN_BACKEND", "jdf-ocr")
        import prompt_matrix.services.jdf_converter as conv

        monkeypatch.setattr(conv, "pdf_to_parse_bundle", lambda data, **k: _bundle())
        res = client.post(
            "/api/projects/p2/import-pdf",
            data={"file": (io.BytesIO(image_of(crisp_pdf(), 100)), "scan.png")},
            content_type="multipart/form-data",
        )
        assert res.status_code in (200, 201), res.get_json()
        body = res.get_json()
        assert body["parser_name"] == "jdf-cli+tesseract"
        assert body["material_type"] == "image"

    def test_object_store_content_type_follows_the_file(self, client, monkeypatch):
        monkeypatch.setenv("PARSE_ASYNC", "1")
        import prompt_matrix.routers.jdf_routes as jdf_routes

        stored = {}

        class FakeStore:
            def put_bytes(self, key, data, *, content_type="application/octet-stream"):
                stored[key] = content_type
                return key

            def exists(self, key):
                return True

            def presign_put(self, key, *, content_type, expires_seconds=900):
                return None

        monkeypatch.setattr(jdf_routes, "get_object_store", lambda: FakeStore())

        class FakeTask:
            id = "task-1"

        monkeypatch.setattr(
            "prompt_matrix.tasks.parse_tasks.import_project_pdf_task",
            types.SimpleNamespace(apply_async=lambda *a, **k: FakeTask()),
        )
        res = client.post(
            "/api/projects/p3/import-pdf",
            data={"file": (io.BytesIO(image_of(crisp_pdf(), 100)), "scan.png")},
            content_type="multipart/form-data",
        )
        assert res.status_code == 202, res.get_json()
        assert list(stored.values()) == ["image/png"]
