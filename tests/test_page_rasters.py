"""Page rasters for OCR documents (services/source_jdf, routers/documents_routes, 2026-09-28).

A scanned or photographed page has only OCR text in its source JDF; the worker
renders each page once (PNG, long side ≤ 1600 px) into the object store under
``documents/<project>/<doc>/<revision>/pages/<n>.png``, the stored JDF puts a
full-page ``image`` element first on each such page pointing at
``GET …/documents/<doc>/pages/<n>.png``, and the descriptor counts them. A
digital PDF stores none and its pages answer 404 — no page is rendered to fill
the gap.
"""

from __future__ import annotations

import io
import json

import pytest

from prompt_matrix.services import source_jdf as sj
from tests.test_quality_probe import crisp_pdf
from tests.test_source_jdf import _fake_converter  # noqa: F401 — the fixture module
from tests.test_source_jdf import client  # noqa: F401 — same app + store fixture
from tests.test_v1_orchestrator import POLICY_LINES, jdf_cli_bundle

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _ocr_bundle():
    bundle = jdf_cli_bundle(ocr_conf=0.91)
    bundle["parser_name"] = "jdf-cli+tesseract"
    bundle["source_kind"] = "scanned"
    return bundle


def _import(monkeypatch, bundle_factory, project="srcjdf", pages=1):
    import prompt_matrix.services.jdf_converter as conv

    monkeypatch.setattr(conv, "pdf_to_parse_bundle", lambda data, **kw: bundle_factory())
    from prompt_matrix.db.ingest_jobs_repository import create_job
    from prompt_matrix.services.pdf_ingest import ingest_pdf_for_project

    job_id = create_job(project, kind="import_pdf", filename="scan.pdf")
    return job_id, ingest_pdf_for_project(project, "scan.pdf", crisp_pdf(pages=pages), job_id=job_id)


# --------------------------------------------------------------------------
# Rules and keys
# --------------------------------------------------------------------------
def test_ocr_decision_reads_the_parser_the_modality_or_the_source_kind():
    assert sj.pages_are_ocr("jdf-cli+tesseract") is True
    assert sj.pages_are_ocr("textract") is True
    assert sj.pages_are_ocr("jdf-cli", modality="phone_photo") is True
    assert sj.pages_are_ocr("jdf-cli", modality="scanned_pdf") is True
    assert sj.pages_are_ocr("jdf-cli", source_kind="image") is True
    assert sj.pages_are_ocr("jdf-cli", modality="digital_pdf", source_kind="pdf") is False
    assert sj.pages_are_ocr(None) is False


def test_raster_key_sits_beside_the_source_key_of_the_same_revision():
    assert sj.page_raster_key("documents/p/doc-1/rev-9.jdf", 3) == "documents/p/doc-1/rev-9/pages/3.png"
    assert sj.page_raster_url("p", "doc-1", 3) == "/api/projects/p/documents/doc-1/pages/3.png"
    with pytest.raises(ValueError):
        sj.page_raster_key("documents/p/doc-1/rev-9/pages/1.png", 1)


# --------------------------------------------------------------------------
# Import path
# --------------------------------------------------------------------------
def test_an_ocr_import_stores_a_raster_per_page_and_puts_an_image_element_first(client, tmp_path, monkeypatch):
    job_id, payload = _import(monkeypatch, _ocr_bundle)
    src = payload["source_jdf"]
    assert src["rasters"] == 1 and src["pages"] == 1 and src["elements"] == len(POLICY_LINES)
    key = src["key"]
    raster = tmp_path / "objects" / f"documents/srcjdf/doc-srcjdf/{payload['revision_id']}/pages/1.png"
    assert raster.is_file(), "the PNG is in the object store beside the .jdf"
    png = raster.read_bytes()
    assert png[:8] == PNG_MAGIC
    width, height = sj._png_size(png)
    assert max(width, height) <= sj.RASTER_LONG_SIDE_PX and max(width, height) > 1500, (width, height)

    doc = json.loads((tmp_path / "objects" / key).read_text("utf-8"))
    page = doc["pages"][0]
    first = page["elements"][0]
    assert first["type"] == "image" and first["fit"] == "contain"
    assert first["src"] == "/api/projects/srcjdf/documents/doc-srcjdf/pages/1.png"
    assert first["position"] == {"x": 0, "y": 0}
    assert first["width"] == page["pageSize"]["width"] and first["height"] == page["pageSize"]["height"]
    assert first["assure"] == {
        "kind": "page_raster", "page": 1, "key": f"documents/srcjdf/doc-srcjdf/{payload['revision_id']}/pages/1.png",
        "width_px": width, "height_px": height,
    }
    # The text elements follow, ids intact — the raster is not a text element.
    assert [el["content"] for el in page["elements"][1:]] == POLICY_LINES
    assert all(el["assure"]["element_id"] for el in page["elements"][1:])
    assert doc["meta"]["assure"]["rasters"] == 1 and doc["meta"]["assure"]["elements_stamped"] == len(POLICY_LINES)

    # The descriptor everywhere says how many pages the reader can see.
    from prompt_matrix.db.parsure_repository import get_latest_report

    assert get_latest_report("srcjdf")["source_jdf"]["rasters"] == 1
    info = client.get("/api/projects/srcjdf/documents/doc-srcjdf/source.json").get_json()
    assert info["rasters"] == 1 and info["elements"] == len(POLICY_LINES)
    # A text selection still maps to text elements only.
    hit = client.get("/api/projects/srcjdf/documents/doc-srcjdf/source.json", query_string={"text": POLICY_LINES[0]}).get_json()
    assert hit["found"] is True and len(hit["element_ids"]) == 1


def test_the_raster_route_streams_the_png_with_etag_and_private_cache(client, monkeypatch):
    _job, payload = _import(monkeypatch, _ocr_bundle)
    key = f"documents/srcjdf/doc-srcjdf/{payload['revision_id']}/pages/1.png"
    res = client.get("/api/projects/srcjdf/documents/doc-srcjdf/pages/1.png")
    assert res.status_code == 200 and res.mimetype == "image/png"
    assert res.data[:8] == PNG_MAGIC
    assert res.headers["Cache-Control"] == "private, max-age=3600"
    assert res.headers["ETag"] == f'"{key}"'
    assert res.headers["X-Source-Jdf-Key"] == payload["source_jdf"]["key"]
    again = client.get("/api/projects/srcjdf/documents/doc-srcjdf/pages/1.png", headers={"If-None-Match": f'"{key}"'})
    assert again.status_code == 304
    # A revision that is not stored, a page past the end, and page 0.
    assert client.get("/api/projects/srcjdf/documents/doc-srcjdf/pages/1.png?revision=rev-nope").status_code == 404
    missing = client.get("/api/projects/srcjdf/documents/doc-srcjdf/pages/2.png")
    assert missing.status_code == 404
    assert missing.get_json() == {"ok": False, "error": "no page raster is stored for this page"}
    assert client.get("/api/projects/srcjdf/documents/doc-srcjdf/pages/0.png").status_code == 400
    # Another project cannot read it, and an unknown document is 404.
    assert client.get("/api/projects/other/documents/doc-srcjdf/pages/1.png").status_code == 404
    assert client.get("/api/projects/default/documents/doc-nothing/pages/1.png").status_code == 404


def test_a_digital_pdf_stores_no_raster_and_its_pages_are_404(client, tmp_path, monkeypatch):
    _job, payload = _import(monkeypatch, jdf_cli_bundle)
    src = payload["source_jdf"]
    assert src["rasters"] == 0
    assert not (tmp_path / "objects" / f"documents/srcjdf/doc-srcjdf/{payload['revision_id']}/pages").exists()
    doc = json.loads((tmp_path / "objects" / src["key"]).read_text("utf-8"))
    assert all(el["type"] == "text" for el in doc["pages"][0]["elements"]), "digital PDFs are unchanged"
    res = client.get("/api/projects/srcjdf/documents/doc-srcjdf/pages/1.png")
    assert res.status_code == 404 and res.get_json()["error"] == "no page raster is stored for this page"
    assert client.get("/api/projects/srcjdf/documents/doc-srcjdf/source.json").get_json()["rasters"] == 0


def test_only_rendered_pages_get_an_element_and_the_count_says_so(monkeypatch):
    """Two OCR pages, the second fails to render: one raster, one image element,
    ``rasters: 1`` — nothing is synthesised for the page that did not render."""
    from prompt_matrix.services import vision

    bundle = _ocr_bundle()
    bundle["jdf"]["pages"].append({"id": "page-2", "pageSize": "Letter", "elements": [{"type": "text", "content": "Page two", "position": {"x": 10, "y": 10}, "width": 100}]})
    real = vision.render_page_png

    def flaky(file_bytes, filename, page_no, **kw):
        if page_no == 2:
            raise ValueError("page 2 does not render")
        return real(file_bytes, filename, page_no, **kw)

    monkeypatch.setattr(vision, "render_page_png", flaky)
    monkeypatch.setenv("ASSURE_S3_BUCKET", "")
    rasters = sj.store_page_rasters("p", "doc-x", "rev-x", crisp_pdf(pages=2), "scan.pdf", 2)
    assert [r["page"] for r in rasters] == [1]
    assert rasters[0]["key"] == "documents/p/doc-x/rev-x/pages/1.png" and rasters[0]["bytes"] > 0
    added = sj.add_page_rasters(bundle["jdf"], rasters)
    assert added == 1 and sj.count_page_rasters(bundle["jdf"]) == 1
    assert bundle["jdf"]["pages"][0]["elements"][0]["type"] == "image"
    assert bundle["jdf"]["pages"][1]["elements"][0]["type"] == "text"
    # Idempotent: adding again duplicates nothing.
    assert sj.add_page_rasters(bundle["jdf"], rasters) == 0
    # A named page size resolves to jdf.js's millimetres.
    bundle["jdf"]["pages"][1]["elements"] = []
    sj.add_page_rasters(bundle["jdf"], [{"page": 2, "url": "/x/2.png", "key": "k"}])
    el = bundle["jdf"]["pages"][1]["elements"][0]
    assert (el["width"], el["height"]) == (215.9, 279.4)
    # No bytes, no pages: nothing stored, nothing raised.
    assert sj.store_page_rasters("p", "doc-x", "rev-x", None, "scan.pdf", 1) == []
    assert sj.store_page_rasters("p", "doc-x", "rev-x", b"not a pdf", "scan.pdf", 1) == []


# --------------------------------------------------------------------------
# Sources path
# --------------------------------------------------------------------------
def test_a_sources_upload_of_a_scan_stores_rasters_under_the_vault_row(client, tmp_path, monkeypatch):
    import prompt_matrix.services.jdf_converter as conv

    monkeypatch.setattr(conv, "pdf_to_parse_bundle", lambda data, **kw: _ocr_bundle())
    res = client.post(
        "/api/projects/srcvault/substrate/upload",
        data={"file": (io.BytesIO(crisp_pdf()), "scan.pdf")},
        content_type="multipart/form-data",
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    src = body["source_jdf"]
    assert src["rasters"] == 1
    revision = src["key"].rsplit("/", 1)[-1][: -len(".jdf")]
    assert revision.startswith("sha-")
    assert (tmp_path / "objects" / f"documents/srcvault/{body['id']}/{revision}/pages/1.png").is_file()
    got = client.get(f"/api/projects/srcvault/documents/{body['id']}/pages/1.png")
    assert got.status_code == 200 and got.mimetype == "image/png"
    doc = client.get(f"/api/projects/srcvault/documents/{body['id']}/source.jdf").get_json()
    first = doc["pages"][0]["elements"][0]
    assert first["type"] == "image" and first["src"] == f"/api/projects/srcvault/documents/{body['id']}/pages/1.png"
