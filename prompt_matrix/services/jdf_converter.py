"""PDF → JDF → chunks via @uurtech/jdf-cli."""
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

try:
    from .field_extractor import (
        NODE_ID_POLICY,
        _element_bbox,
        _element_text,
        _walk_elements,
        derive_element_id,
        ocr_block_elements,
    )
except ImportError:  # pragma: no cover - flat-import fallback
    from services.field_extractor import (  # type: ignore
        NODE_ID_POLICY,
        _element_bbox,
        _element_text,
        _walk_elements,
        derive_element_id,
        ocr_block_elements,
    )

log = logging.getLogger(__name__)
JDF_BIN = shutil.which("jdf") or "/opt/node-v24.11.1-linux-arm64/bin/jdf"
JDF_TIMEOUT = 60


class JdfConversionError(RuntimeError):
    pass


def _run(cmd, *, timeout: int | None = None, **kw):
    env = os.environ.copy()
    env["PATH"] = env.get("PATH", "") + ":/opt/node-v24.11.1-linux-arm64/bin"
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout if timeout is not None else JDF_TIMEOUT,
            env=env,
            **kw,
        )
    except FileNotFoundError as exc:
        # A missing jdf binary is a conversion failure, not a crash: callers
        # fall back best-effort, so it surfaces as the one clean error type.
        raise JdfConversionError(f"jdf binary not found: {exc}") from exc


#: OCR engine jdf-cli runs on pages that have no text layer. ``tesseract`` is
#: tesseract.js, local and free (jdf-cli >= 0.2.3 bundles it); ``none`` turns
#: OCR off so a scan comes back as image-only pages. ``JDF_OCR`` overrides.
JDF_OCR_DEFAULT = "tesseract"
#: OCR wall-clock: tesseract.js takes ~2 s per scanned page on one core, so a
#: 50-page scan is well past the 60 s convert timeout used for text-layer PDFs.
JDF_OCR_TIMEOUT = int(os.environ.get("JDF_OCR_TIMEOUT", "600"))


def ocr_engine() -> str:
    """The OCR engine jdf-cli should use for scanned pages (``none`` disables)."""
    raw = (os.environ.get("JDF_OCR") or JDF_OCR_DEFAULT).strip().lower()
    return raw if raw in ("tesseract", "openai", "none") else JDF_OCR_DEFAULT


def pdf_to_jdf(pdf_bytes: bytes, *, ocr: str | None = None) -> dict:
    """PDF → JDF via ``jdf convert``.

    ``ocr`` names the engine for pages with no text layer (``"tesseract"``);
    ``None`` runs a plain text-layer parse. jdf-cli emits per-line OCR blocks
    with confidences under ``pages[].elements[].ocr`` and ``jdf chunk`` folds
    that text into ordinary chunks, so a scanned PDF flows through the same
    pipeline as a born-digital one.
    """
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(pdf_bytes)
        pdf_path = f.name
    jdf_path = pdf_path[:-4] + ".jdf"
    cmd = [JDF_BIN, "convert", pdf_path, "-o", jdf_path, "--json"]
    timeout = JDF_TIMEOUT
    if ocr and ocr != "none":
        cmd += ["--ocr", ocr]
        timeout = JDF_OCR_TIMEOUT
    try:
        r = _run(cmd, timeout=timeout)
        if r.returncode != 0:
            raise JdfConversionError(f"jdf convert failed: {r.stderr[:500]}")
        return json.loads(Path(jdf_path).read_text())
    finally:
        for p in (pdf_path, jdf_path):
            try:
                os.unlink(p)
            except FileNotFoundError:
                pass


# --------------------------------------------------------------------------- #
# Orientation of scanned pages (2026-09-28)
# --------------------------------------------------------------------------- #

#: DPI of the probe renders the orientation is measured on. tesseract.js on a
#: 100-dpi US-Letter page takes 0.6–1.0 s per rotation (measured 2026-09-28,
#: M-series laptop) against 1.3 s for the 200-dpi page.
ORIENTATION_DPI = 100
#: An OCR block (line) at or above this confidence counts its words as
#: "confident". On the bench rotated scan the four rotations read 5 / 0 / 2 /
#: 99 confident words (0° / 90° / 180° / 270°), while raw word counts were
#: 165 / 43 / 60 / 99 — tesseract reads sideways text as many low-confidence
#: fragments, so word count alone picks the wrong rotation.
ORIENTATION_CONFIDENT_BLOCK = 0.70
#: The upright render is accepted without trying the other rotations when
#: it reads at least this many confident words at at least this mean block
#: confidence. Measured 2026-09-28 on the bench: upright scans read 33–99
#: confident words at mean 0.84–0.94, the 72-dpi phone photo 64 at 0.71;
#: sideways or upside-down renders read 0–5 confident words at mean
#: 0.34–0.53. 0.6 / 20 accepts every upright bench page after one pass and
#: none of the turned ones.
ORIENTATION_ACCEPT_MEAN = 0.60
ORIENTATION_ACCEPT_WORDS = 20
#: A rotation replaces 0° only when it reads at least this many confident
#: words and at least this multiple of the 0° render's; otherwise the page is
#: left as scanned and the record says the measurement was inconclusive.
ORIENTATION_MIN_WORDS = 10
ORIENTATION_MIN_GAIN = 2.0
ORIENTATION_ROTATIONS = (0, 90, 180, 270)
#: Pages beyond this many measured scan pages are left as scanned and recorded
#: as not measured (each measured page costs 1–4 OCR passes).
ORIENTATION_MAX_PAGES = int(os.environ.get("JDF_ORIENTATION_MAX_PAGES", "50") or 50)

_OSD_ROTATE = re.compile(r"^Rotate:\s*(\d+)", re.M)
_OSD_CONFIDENCE = re.compile(r"^Orientation confidence:\s*([\d.]+)", re.M)


def orientation_enabled() -> bool:
    """``JDF_ORIENTATION`` (default on) — off skips the measurement entirely."""
    return os.environ.get("JDF_ORIENTATION", "1").strip().lower() not in ("0", "false", "no", "off")


def _fitz():
    import fitz  # PyMuPDF, already a dependency (quality_probe, pdf_import)

    return fitz


def ocr_reading(jdf_dict: dict) -> dict[str, Any]:
    """What one OCR pass read: blocks, words, words in confident blocks, mean
    block confidence — the numbers an orientation decision is made from."""
    confs: list[float] = []
    words = confident = 0
    for page in (jdf_dict.get("pages") or []) if isinstance(jdf_dict, dict) else []:
        if not isinstance(page, dict):
            continue
        for el in _walk_elements(page.get("elements")):
            ocr = el.get("ocr")
            if not isinstance(ocr, dict):
                continue
            for block in ocr.get("blocks") or []:
                if not isinstance(block, dict):
                    continue
                n = len(str(block.get("text") or "").split())
                conf = block.get("confidence")
                words += n
                if isinstance(conf, (int, float)):
                    confs.append(float(conf))
                    if float(conf) >= ORIENTATION_CONFIDENT_BLOCK:
                        confident += n
    return {
        "blocks": len(confs),
        "words": words,
        "confident_words": confident,
        "mean_confidence": round(sum(confs) / len(confs), 4) if confs else None,
    }


def _image_only_pdf(png: bytes, width_pt: float, height_pt: float) -> bytes:
    fitz = _fitz()
    doc = fitz.open()
    try:
        page = doc.new_page(width=width_pt, height=height_pt)
        page.insert_image(page.rect, stream=png)
        return doc.tobytes()
    finally:
        doc.close()


def _ocr_probe(page, rotation: int, *, ocr: str) -> dict[str, Any]:
    """OCR one page rendered at ``ORIENTATION_DPI`` and rotated ``rotation``
    degrees clockwise (``fitz.Matrix.prerotate``; verified 2026-09-28: text at
    the top of a page lands on the right after ``prerotate(90)``)."""
    fitz = _fitz()
    zoom = ORIENTATION_DPI / 72.0
    pm = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom).prerotate(rotation), colorspace=fitz.csGRAY, alpha=False)
    pdf = _image_only_pdf(pm.tobytes("png"), pm.width * 72.0 / ORIENTATION_DPI, pm.height * 72.0 / ORIENTATION_DPI)
    return ocr_reading(pdf_to_jdf(pdf, ocr=ocr))


def _osd_rotation(page) -> dict[str, Any] | None:
    """Tesseract's orientation-and-script detection (``--psm 0``) when the
    ``tesseract`` binary is installed; None otherwise (the image ships
    tesseract.js through jdf-cli, not the binary, so this is None there).
    ``rotate`` is tesseract's clockwise correction, verified by an OCR pass
    before it is applied (:func:`detect_orientation`)."""
    binary = shutil.which("tesseract")
    if not binary:
        return None
    fitz = _fitz()
    zoom = ORIENTATION_DPI / 72.0
    pm = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csGRAY, alpha=False)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(pm.tobytes("png"))
        path = f.name
    try:
        r = _run([binary, path, "-", "--psm", "0"], timeout=60)
    except JdfConversionError:
        return None
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
    if r.returncode != 0:
        return None
    out = (r.stdout or "") + (r.stderr or "")
    rot, conf = _OSD_ROTATE.search(out), _OSD_CONFIDENCE.search(out)
    if not rot:
        return None
    return {"rotate": int(rot.group(1)) % 360, "confidence": float(conf.group(1)) if conf else None}


def _reads_well(reading: dict[str, Any]) -> bool:
    mean = reading.get("mean_confidence")
    return isinstance(mean, (int, float)) and mean >= ORIENTATION_ACCEPT_MEAN and int(reading.get("confident_words") or 0) >= ORIENTATION_ACCEPT_WORDS


def _native_dpi(doc, page) -> int:
    """Pixel width of the page's largest embedded image over the page width in
    inches — the resolution the rotated page is re-rendered at, so nothing is
    lost that the scan had (clamped to 150–300; 200 when no image is found)."""
    best = 0
    try:
        for info in page.get_images(full=True):
            xref = info[0]
            width = doc.extract_image(xref).get("width") or 0
            best = max(best, int(width))
    except Exception:  # pragma: no cover - PyMuPDF quirks on odd files
        best = 0
    if best <= 0 or page.rect.width <= 0:
        return 200
    return int(max(150, min(300, round(best / (page.rect.width / 72.0)))))


def detect_orientation(pdf_bytes: bytes, *, ocr: str) -> tuple[bytes, dict[str, Any]]:
    """Measure the orientation of every page without a text layer and return
    the PDF with those pages rotated upright, plus the record of what was
    measured.

    Method, per scan page: OCR the ``ORIENTATION_DPI`` render as scanned
    (0°); if it reads well (``ORIENTATION_ACCEPT_MEAN`` / ``_WORDS``) the page
    is upright and nothing else is tried. Otherwise the rotation the previous
    pages needed (a scanner batch is rotated as a whole) and tesseract's OSD
    suggestion (when the binary exists) are OCR'd next and accepted when they
    read well; failing that, the remaining rotations are OCR'd and the one
    with the most confident words wins, provided it beats 0° by
    ``ORIENTATION_MIN_GAIN`` with at least ``ORIENTATION_MIN_WORDS`` — else the
    page stays as scanned and the record says ``inconclusive``. Every record
    lists the measurements the decision rests on; a page that was not
    measured (text layer, page cap) says so and is never rotated.

    Rotation is done by re-rendering the page at its native DPI with
    ``Matrix.prerotate(correction)`` into a fresh image-only page: jdf-cli
    ignores ``/Rotate`` (measured 2026-09-28: ``page.set_rotation(270)`` on
    the bench rotated scan changed nothing in its OCR), so the pixels have
    to move. Pages that need no correction are copied untouched.

    ``detected_degrees`` is how far clockwise the scanned content is turned
    from upright (the bench generator's ``rotate=90`` case reads 90);
    ``correction_degrees`` is the clockwise rotation applied to fix it
    (``(360 − detected) % 360``).
    """
    fitz = _fitz()
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    records: list[dict[str, Any]] = []
    corrections: dict[int, int] = {}
    preferred: int | None = None
    measured_pages = 0
    try:
        for index, page in enumerate(doc):
            page_no = index + 1
            if page.get_text("text").strip():
                records.append({"page": page_no, "detected_degrees": None, "correction_degrees": 0, "method": "not measured",
                                "basis": "page has a text layer; orientation is measured on scanned pages only"})
                continue
            if measured_pages >= ORIENTATION_MAX_PAGES:
                records.append({"page": page_no, "detected_degrees": None, "correction_degrees": 0, "method": "not measured",
                                "basis": f"beyond the {ORIENTATION_MAX_PAGES}-page measurement cap (JDF_ORIENTATION_MAX_PAGES); left as scanned"})
                continue
            measured_pages += 1
            measurements: dict[int, dict[str, Any]] = {}

            def measure(rotation: int) -> dict[str, Any]:
                if rotation not in measurements:
                    measurements[rotation] = _ocr_probe(page, rotation, ocr=ocr)
                return measurements[rotation]

            method = "ocr-4-rotations"
            chosen: int | None = None
            if _reads_well(measure(0)):
                chosen, method = 0, "ocr-upright-accepted"
            else:
                osd = _osd_rotation(page)
                shortlist = [r for r in (preferred, (osd or {}).get("rotate")) if isinstance(r, int) and r % 360 not in (0,)]
                for rotation in shortlist:
                    if _reads_well(measure(rotation % 360)):
                        chosen = rotation % 360
                        method = "tesseract-osd+ocr" if osd and rotation == osd.get("rotate") else "ocr-batch-rotation"
                        break
                if chosen is None:
                    for rotation in ORIENTATION_ROTATIONS:
                        measure(rotation)
                    upright = int(measurements[0]["confident_words"])
                    best = max(ORIENTATION_ROTATIONS, key=lambda r: (measurements[r]["confident_words"], measurements[r]["mean_confidence"] or 0.0))
                    best_words = int(measurements[best]["confident_words"])
                    if best != 0 and best_words >= ORIENTATION_MIN_WORDS and best_words >= ORIENTATION_MIN_GAIN * max(1, upright):
                        chosen = best
                    else:
                        chosen, method = 0, "inconclusive"
            summary = ", ".join(
                f"{r}°: {m['confident_words']} confident words of {m['words']} (mean {m['mean_confidence'] if m['mean_confidence'] is not None else 'n/a'})"
                for r, m in sorted(measurements.items())
            )
            detected = (360 - chosen) % 360
            if method == "inconclusive":
                basis = f"no rotation read clearly better than the scan as given ({summary}); left as scanned"
            elif method == "ocr-upright-accepted":
                basis = f"the page as scanned reads well ({summary}); other rotations not tried"
            else:
                basis = f"best OCR reading after rotating {chosen}° clockwise ({summary})"
            records.append({"page": page_no, "detected_degrees": detected, "correction_degrees": chosen, "method": method,
                            "basis": basis, "measurements": {str(r): m for r, m in sorted(measurements.items())}, "dpi": ORIENTATION_DPI})
            if chosen:
                corrections[index] = chosen
                preferred = chosen
        if not corrections:
            return pdf_bytes, {"pages": records, "rotated_pages": [], "dpi": ORIENTATION_DPI}
        out = fitz.open()
        try:
            for index, page in enumerate(doc):
                correction = corrections.get(index)
                if not correction:
                    out.insert_pdf(doc, from_page=index, to_page=index)
                    continue
                dpi = _native_dpi(doc, page)
                zoom = dpi / 72.0
                pm = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom).prerotate(correction), colorspace=fitz.csRGB, alpha=False)
                new_page = out.new_page(width=pm.width * 72.0 / dpi, height=pm.height * 72.0 / dpi)
                new_page.insert_image(new_page.rect, stream=pm.tobytes("png"))
                records[index]["rerendered_dpi"] = dpi
            return out.tobytes(deflate=True, garbage=3), {"pages": records, "rotated_pages": [i + 1 for i in sorted(corrections)], "dpi": ORIENTATION_DPI}
        finally:
            out.close()
    finally:
        doc.close()


def _ocr_confidence_from_blocks(jdf_dict: dict) -> tuple[float | None, int]:
    """Mean OCR line confidence over the document, and how many lines it covers.

    Read from what jdf-cli actually emitted (``elements[].ocr.blocks[].confidence``,
    tesseract's 0–1 per line). ``(None, 0)`` when no OCR block carries a
    confidence: the honest unknown, never a fabricated score.
    """
    total = 0.0
    count = 0
    pages = jdf_dict.get("pages") if isinstance(jdf_dict, dict) else None
    for page in pages or []:
        if not isinstance(page, dict):
            continue
        for el in page.get("elements") or []:
            if not isinstance(el, dict):
                continue
            ocr = el.get("ocr")
            if not isinstance(ocr, dict):
                continue
            for block in ocr.get("blocks") or []:
                if not isinstance(block, dict):
                    continue
                conf = block.get("confidence")
                if isinstance(conf, (int, float)):
                    total += float(conf)
                    count += 1
    if count == 0:
        return None, 0
    return round(total / count, 4), count


def _jdf_page_count(jdf_dict: dict) -> int:
    pages = jdf_dict.get("pages")
    return len(pages) if isinstance(pages, list) and pages else 1


def _jdf_confidence(jdf_dict: dict, key: str) -> float | None:
    """Pull a confidence figure off jdf-cli's own meta, if it ever emits one.

    jdf-cli's `convert` is a text-layer parse, not an OCR pass, so it carries
    no per-page confidence today. This reads `meta.<key>` defensively so a
    future jdf-cli version that does emit one is picked up without a code
    change here, and returns ``None`` (the honest "unknown", not a faked
    number) when it does not.
    """
    meta = jdf_dict.get("meta") if isinstance(jdf_dict, dict) else None
    if not isinstance(meta, dict):
        return None
    value = meta.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def chunks_to_text(chunks: list[dict]) -> str:
    """The document text of a chunk list, in order — what a vault entry stores.

    The ingest converts and chunks a PDF but never keeps the extraction on its
    own, so the chunks are the only copy of the text: joining their `text` in
    the order jdf-cli emitted them reproduces the document.
    """
    parts = [str(c.get("text") or c.get("content") or "").strip() for c in chunks or []]
    return "\n\n".join(p for p in parts if p)


def jdf_to_chunks(jdf_dict: dict, strategy: str = "section") -> list[dict]:
    with tempfile.NamedTemporaryFile(suffix=".jdf", delete=False, mode="w") as f:
        json.dump(jdf_dict, f)
        jdf_path = f.name
    chunks_path = jdf_path[:-4] + ".chunks.jsonl"
    try:
        r = _run(
            [JDF_BIN, "chunk", jdf_path, "--strategy", strategy, "--format", "jsonl", "-o", chunks_path]
        )
        if r.returncode != 0:
            raise JdfConversionError(f"jdf chunk failed: {r.stderr[:500]}")
        chunks = []
        with open(chunks_path) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    chunks.append(json.loads(line))
        return chunks
    finally:
        for p in (jdf_path, chunks_path):
            try:
                os.unlink(p)
            except FileNotFoundError:
                pass


def _chunk_asset_entries(chunks: list[dict], kind: str) -> list[dict]:
    """Chunks the jdf-cli chunker tagged as this structured-asset kind.

    A chunk's `types` list is jdf-cli's own label of what the block is, so it is
    the honest signal for tables/images/figures — nothing is inferred from text
    shape. Figures and images are kept apart: a figure is a semantic asset (a
    chart, a diagram — `figure` type), an image is the raw asset (`image` type);
    one may refer to the other but they never collapse into one list.
    """
    entries: list[dict] = []
    for chunk in chunks or []:
        if not isinstance(chunk, dict):
            continue
        types = chunk.get("types") or []
        if not isinstance(types, list):
            continue
        if not any(str(t).lower() == kind for t in types):
            continue
        entries.append(
            {
                "id": str(chunk.get("id") or ""),
                "text": str(chunk.get("text") or chunk.get("content") or "").strip(),
                "page": chunk.get("page"),
            }
        )
    return entries


def _page_asset_entries(jdf_dict: dict, kind: str) -> list[dict]:
    """Image/figure objects the JDF document itself lists on its pages.

    `pages[].images` is real document metadata when jdf-cli emits it. Read
    defensively: an older jdf-cli emits no asset lists and the result is an
    empty list, not a fabricated one.
    """
    entries: list[dict] = []
    pages = jdf_dict.get("pages")
    if not isinstance(pages, list):
        return entries
    for page_index, page in enumerate(pages):
        if not isinstance(page, dict):
            continue
        assets = page.get(kind) or page.get(f"{kind}s") or []
        if not isinstance(assets, list):
            continue
        for asset in assets:
            entry = {
                "page": page.get("page") or page.get("number") or page_index + 1,
            }
            if isinstance(asset, dict):
                entry["id"] = str(asset.get("id") or asset.get("name") or "")
                entry["src"] = asset.get("src") or asset.get("data") or ""
                entry["caption"] = str(asset.get("caption") or asset.get("alt") or "")
            else:
                entry["id"] = str(asset or "")
                entry["src"] = ""
                entry["caption"] = ""
            entries.append(entry)
    return entries


def _bundle_assets(jdf_dict: dict, chunks: list[dict]) -> dict:
    """Structured content of a parse: tables, images, figures as distinct lists.

    Tables/images/figures are first-class payloads, not text that happens to
    look tabular. Chunk tags are the primary signal (jdf-cli's own labels);
    page-level image metadata is merged in for images so an asset jdf-cli only
    lists on the page is not lost. Counts and `asset_summary` ride along so a
    caller can render a badge without walking the lists.
    """
    tables = _chunk_asset_entries(chunks, "table")
    images = _chunk_asset_entries(chunks, "image") + _page_asset_entries(jdf_dict, "image")
    figures = _chunk_asset_entries(chunks, "figure")
    return {
        "tables": tables,
        "images": images,
        "figures": figures,
        "table_count": len(tables),
        "image_count": len(images),
        "figure_count": len(figures),
        "asset_summary": {
            "tables": len(tables),
            "images": len(images),
            "figures": len(figures),
        },
    }


#: How `jdf chunk` splits a page. ``element`` keeps one chunk per jdf-cli
#: element (6 for a one-page declarations PDF, 16 for a 3-page bundle);
#: ``section`` collapsed each of those into ONE chunk — a 3-page bundle became
#: a single page-1 chunk — so every extracted field pointed at the same node
#: (measured 2026-09-26, see docs/parsure.md). ``JDF_CHUNK_STRATEGY`` overrides.
CHUNK_STRATEGY_DEFAULT = "element"


def chunk_strategy() -> str:
    raw = os.environ.get("JDF_CHUNK_STRATEGY", "").strip().lower()
    return raw if raw in ("element", "section", "fixed") else CHUNK_STRATEGY_DEFAULT


def pdf_to_parse_bundle(
    pdf_bytes: bytes,
    *,
    strategy: str | None = None,
    filename: str | None = None,
    source_kind: str = "pdf",
    ocr: str | None = None,
) -> dict:
    """PDF → JDF → chunks, plus the parse metadata routes/OMP staging need.

    JDF is the default PDF parser: this is the one call ingestion routes
    should make. ``pdf_to_jdf``/``jdf_to_chunks`` stay as the implementation
    underneath it — kept and exported for callers that already work directly
    with the JDF document or the chunk list — but a raw-JDF-only helper is
    not enough for a route that also has to stage a parse artifact into OMP
    (``services/omp.build_omp_artifact_from_parse``) or a vault row
    (``db/substrate_repository``), both of which expect ``parse_confidence``/
    ``ocr_confidence`` alongside the text. Structured content — tables,
    images, figures — travels in the bundle as distinct lists, never
    collapsed into text.

    ``page_count`` comes from the document's own page metadata (``pages``),
    not from the chunk count. ``parse_confidence``/``ocr_confidence`` are
    ``None`` when jdf-cli did not emit them: the honest unknown, never a
    fabricated score, and a 0.0 a real parser reported is passed through as
    0.0, not dropped. Any failure in either jdf-cli step surfaces as a single
    ``JdfConversionError`` carrying that step's stderr.
    """
    strategy = strategy or chunk_strategy()
    orientation: dict[str, Any] | None = None
    if ocr and ocr != "none" and orientation_enabled():
        # Scanned pages first get their orientation measured and corrected
        # (2026-09-28: the bench 90° scan routed ``uncertain`` with 0 fields
        # because tesseract read sideways text as fragments). A failure here
        # is recorded, never fatal: the page is then OCR'd as scanned.
        try:
            pdf_bytes, orientation = detect_orientation(pdf_bytes, ocr=ocr)
        except Exception as exc:  # noqa: BLE001 - the parse must go on
            log.warning("orientation detection failed; OCR runs on the pages as scanned: %s", exc)
            orientation = {"pages": [], "rotated_pages": [], "error": f"{type(exc).__name__}: {exc}"[:300]}
    # pdf_to_jdf/jdf_to_chunks already raise JdfConversionError carrying the
    # failing step's stderr (`_run`'s stdout/stderr, truncated) — nothing to
    # translate here, just let it propagate as the one clean error type.
    jdf = pdf_to_jdf(pdf_bytes, ocr=ocr)
    chunks = jdf_to_chunks(jdf, strategy=strategy)
    text = chunks_to_text(chunks)
    assets = _bundle_assets(jdf, chunks)
    # OCR confidence: what jdf-cli's meta says if it ever says anything, else
    # the mean of the per-line tesseract confidences it emitted (None when the
    # document carried no OCR blocks at all — a text-layer parse).
    ocr_confidence = _jdf_confidence(jdf, "ocr_confidence")
    ocr_lines = 0
    if ocr_confidence is None:
        ocr_confidence, ocr_lines = _ocr_confidence_from_blocks(jdf)
    parser_name = "jdf-cli" if not (ocr and ocr != "none") else f"jdf-cli+{ocr}"
    return {
        "jdf": jdf,
        "chunks": chunks,
        "text": text,
        "page_count": _jdf_page_count(jdf),
        "parser_name": parser_name,
        "source_kind": source_kind,
        "parse_confidence": _jdf_confidence(jdf, "parse_confidence"),
        "ocr_confidence": ocr_confidence,
        "ocr_line_count": ocr_lines,
        "ocr_engine": ocr if (ocr and ocr != "none") else None,
        "orientation": orientation,
        "tables": assets["tables"],
        "images": assets["images"],
        "figures": assets["figures"],
        "table_count": assets["table_count"],
        "image_count": assets["image_count"],
        "figure_count": assets["figure_count"],
        "asset_summary": assets["asset_summary"],
        "filename": filename,
    }


#: Longest ``text_preview`` on a ``meta.elements`` entry — enough to recognise
#: the line in a review UI, short enough that the tree does not carry the
#: page twice.
ELEMENT_PREVIEW_CHARS = 40


def _locate(content: str, needle: str, cursor: int) -> tuple[int, int] | None:
    """``(start, end)`` of ``needle`` in ``content`` at or after ``cursor`` —
    exact first, then whitespace-tolerant (jdf-cli's chunker may re-wrap a
    line it joined into a section chunk). None when the text is not there."""
    idx = content.find(needle, cursor)
    if idx != -1:
        return idx, idx + len(needle)
    words = needle.split()
    if not words:
        return None
    m = re.compile(r"\s+".join(re.escape(w) for w in words)).search(content, cursor)
    return (m.start(), m.end()) if m else None


def chunk_elements(jdf_dict: dict, chunk: dict, content: str) -> list[dict[str, Any]]:
    """``meta.elements`` for one chunk paragraph: the jdf-cli page elements whose
    text the chunk contains, each with its ``eid-v1`` id, bbox, page and
    character range inside ``content``.

    ``jdf chunk --strategy section`` (the ingest default) folds every element
    of a page — measured 2026-09-26: all 6 elements of a one-page declarations
    PDF, all 16 elements of a three-page bundle — into one chunk, so the tree
    had one paragraph and every field pointed at it (customer benchmark P0
    "evidence granularity is too coarse"). The paragraph stays one per chunk
    (the draft and verification pipelines read that shape); this list is what
    lets a field's ``source_span`` name the element and its bbox inside it.
    Elements are matched in document order with a moving cursor, the chunk's
    own page first, so a repeated line binds to its first unmatched copy.
    An element whose text is not in the chunk is simply not listed.
    """
    pages = jdf_dict.get("pages") if isinstance(jdf_dict, dict) else None
    if not isinstance(pages, list) or not content:
        return []
    chunk_id = str(chunk.get("id") or "") or None
    try:
        chunk_page = int(chunk.get("page") or 0)
    except (TypeError, ValueError):
        chunk_page = 0
    ordered = sorted(enumerate(pages, start=1), key=lambda ip: (ip[0] != chunk_page, ip[0]))
    out: list[dict[str, Any]] = []
    cursor = 0
    for page_no, page in ordered:
        if not isinstance(page, dict):
            continue
        for el in _walk_elements(page.get("elements")):
            text = _element_text(el).strip()
            if not text:
                continue
            hit = _locate(content, text, cursor)
            if hit is None:
                continue
            start, end = hit
            bbox = _element_bbox(el, page)
            out.append(
                {
                    "element_id": derive_element_id(chunk_id, page_no, bbox, text),
                    "page": page_no,
                    "bbox": bbox,
                    "start_char": start,
                    "end_char": end,
                    "text_preview": text[:ELEMENT_PREVIEW_CHARS],
                }
            )
            # A scanned page's OCR lines, after the image element that carries
            # them (first match keeps resolving to the element; the signature
            # band reads the smallest covering entry — field_extractor.ocr_block_elements).
            out.extend(ocr_block_elements(el, page, text, chunk_id=chunk_id, page_no=page_no, offset=start))
            cursor = end
    return out


def table_grids_by_page(jdf_dict: dict) -> dict[int, list[tuple[list, list]]]:
    """``{page_no: [(headers, rows), …]}`` for every ``type: "table"`` page
    element of a jdf-cli document, in element order; ``{}`` for a tree or a
    document without tables."""
    out: dict[int, list[tuple[list, list]]] = {}
    pages = jdf_dict.get("pages") if isinstance(jdf_dict, dict) else None
    if not isinstance(pages, list):
        return out
    for idx, page in enumerate(pages):
        if not isinstance(page, dict):
            continue
        for el in _walk_elements(page.get("elements")):
            if str(el.get("type") or "").lower() != "table":
                continue
            headers = el.get("headers") if isinstance(el.get("headers"), list) else []
            rows = [list(r) if isinstance(r, (list, tuple)) else [r] for r in (el.get("rows") if isinstance(el.get("rows"), list) else [])]
            out.setdefault(idx + 1, []).append((list(headers), rows))
    return out


def jdf_to_document_tree(
    jdf_dict: dict,
    chunks: list[dict],
    *,
    document_id: str,
    title: str,
    parse_meta: dict[str, Any] | None = None,
) -> dict:
    """A JDF document tree built from the jdf-cli parse bundle.

    ``import_project_pdf`` saves a JDF revision, and the revision shape is the
    Assure tree (``document_id``/``meta``/``body``), not jdf-cli's raw output.
    Chunks are grouped into one section per page (``page_count`` page metadata
    drives the section list), text blocks become paragraphs and tagged blocks
    become table/image nodes so the structured content survives into the tree —
    a table is stored as a table node, an image as an image node, a figure as
    an image node flagged ``asset_kind: figure`` (JDF has no separate figure
    node type; the flag keeps figures and images distinguishable). ``parse_meta``
    rides into ``meta`` verbatim so parse/OCR confidence and asset counts are
    on the saved tree.

    One paragraph per chunk, always: the draft/verification pipelines read
    that shape. Granularity below it is ``meta.elements`` on each paragraph
    (``chunk_elements``) — the jdf-cli elements the chunk contains, with
    ``eid-v1`` ids, bboxes and character offsets — and ``meta.node_id_policy``
    on the tree names the policy those ids follow.
    """
    try:
        from ..models.jdf import empty_annotations, new_node_id
    except ImportError:  # pragma: no cover - flat-import fallback
        from models.jdf import empty_annotations, new_node_id

    meta: dict[str, Any] = {"title": title, "import_source": "jdf-cli", "node_id_policy": NODE_ID_POLICY}
    if parse_meta:
        meta.update(parse_meta)

    def _paragraph(chunk: dict | str) -> dict:
        # Empty pages pass a plain string; chunked pages pass the chunk dict.
        text = (
            chunk
            if isinstance(chunk, str)
            else str(chunk.get("text") or chunk.get("content") or "")
        )
        # The chunk id (``p1e0``) is the address a field's ``source_span``
        # carries; keeping it on the paragraph lets the intake report name the
        # exact tree node a value came from (2026-09-26, "JDF node addressing").
        # ``meta.elements`` (2026-09-26, ``eid-v1``) lists the elements the
        # chunk folded together, with offsets into ``content``, so the span
        # of a value resolves below the paragraph without changing its shape.
        pmeta: dict[str, Any] = {}
        content = text.strip()
        if isinstance(chunk, dict):
            if chunk.get("id"):
                pmeta["chunk_id"] = str(chunk["id"])
            if chunk.get("page") not in (None, ""):
                pmeta["source_page"] = chunk.get("page")
            elements = chunk_elements(jdf_dict, chunk, content)
            if elements:
                pmeta["elements"] = elements
        return {
            "type": "paragraph",
            "id": new_node_id("p"),
            "content": content,
            "entities_referenced": [],
            "provenance": [],
            "meta": pmeta,
            "annotations": empty_annotations(),
        }

    # jdf-cli 0.2.3's ``jdf chunk`` flattens a table to ``Header: cell | …`` text
    # and carries no headers/rows (measured 2026-09-27 on bench/cases
    # coverage_schedule.pdf), while the page element (``type: "table"``) has the
    # grid. The k-th table chunk of a page is the k-th table element of that
    # page, so the tree's table node gets the real grid (plan Part 9.5).
    grids_by_page = table_grids_by_page(jdf_dict)
    grids_taken: dict[int, int] = {}

    def _table_node(chunk: dict) -> dict:
        headers = list(chunk.get("headers") or [])
        rows = [list(r) for r in (chunk.get("rows") or [])]
        if not rows:
            try:
                page_no = int(chunk.get("page") or 0)
            except (TypeError, ValueError):
                page_no = 0
            grids = grids_by_page.get(page_no) or []
            k = grids_taken.get(page_no, 0)
            if k < len(grids):
                headers, rows = grids[k]
            grids_taken[page_no] = k + 1
        return {
            "type": "table",
            "id": new_node_id("tbl"),
            "caption": str(chunk.get("text") or "").strip()[:200],
            "headers": [str(h) for h in headers],
            "rows": [[str(c) for c in r] for r in rows],
            "bound_entities": [],
            "annotations": empty_annotations(),
        }

    def _image_node(chunk: dict, *, figure: bool) -> dict:
        node_meta: dict[str, Any] = {}
        if figure:
            node_meta["asset_kind"] = "figure"
        if chunk.get("page") is not None:
            node_meta["page"] = chunk.get("page")
        return {
            "type": "image",
            "id": new_node_id("img"),
            "src": str(chunk.get("src") or ""),
            "alt": str(chunk.get("caption") or chunk.get("text") or "").strip(),
            "caption": str(chunk.get("caption") or "").strip(),
            "meta": node_meta,
            "annotations": empty_annotations(),
        }

    pages = jdf_dict.get("pages")
    page_count = len(pages) if isinstance(pages, list) and pages else 1
    body: list[dict[str, Any]] = []
    for page_num in range(1, page_count + 1):
        children: list[dict[str, Any]] = []
        for chunk in chunks or []:
            if not isinstance(chunk, dict):
                continue
            chunk_page = chunk.get("page")
            if chunk_page is not None and int(chunk_page) != page_num:
                continue
            types = [str(t).lower() for t in (chunk.get("types") or [])]
            if "table" in types:
                children.append(_table_node(chunk))
            elif "figure" in types:
                children.append(_image_node(chunk, figure=True))
            elif "image" in types:
                children.append(_image_node(chunk, figure=False))
                # A scanned page is one image chunk whose text is the OCR
                # output. The image node keeps the asset; the words must also
                # be a paragraph, or the tree of a scan carries no readable,
                # citable text at all.
                ocr_text = str(chunk.get("text") or chunk.get("content") or "").strip()
                if ocr_text:
                    # Built from the chunk (not the bare string) since 2026-09-28
                    # so the OCR paragraph carries the chunk id and
                    # ``meta.elements`` — the image element plus one
                    # ``ocr_block`` entry per OCR line with its bbox — like
                    # every other paragraph; until then a scan's fields had no
                    # chunk address and the signature band had no line bbox.
                    para = _paragraph(chunk)
                    para["meta"].update({"ocr": True, "page": chunk.get("page")})
                    children.append(para)
            else:
                children.append(_paragraph(chunk))
        if not children:
            children.append(_paragraph(f"(Empty page {page_num})"))
        body.append(
            {
                "type": "section",
                "id": new_node_id("sec"),
                "title": f"Page {page_num}",
                "children": children,
                "meta": {"source_page": page_num},
                "annotations": empty_annotations(),
            }
        )
    return {
        "document_id": document_id,
        "meta": meta,
        "truth_ledger": {},
        "body": body,
    }