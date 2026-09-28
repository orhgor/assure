#!/usr/bin/env python3
"""Build the ``tests/golden/proof_suite_v1`` fixtures (customer plan V4 Part 5, 2026-09-28).

Three documents, each with the parse bundle jdf-cli produced for it recorded
beside it, so the proof suite runs offline (no node, no tesseract, no network)
on exactly the bytes and the OCR the real parser gave:

* ``site_report_photo.jpg`` — the customer's site-report photo (a copy of the
  file given on the command line; ``demo/review/review.jpeg`` on the
  developer's machine, gitignored there) and ``site_report_photo.ocr.json``,
  its jdf-cli + tesseract bundle with the base64 raster (``jdf.resources``)
  removed — 1.1 MB of pixels the tests never read.
* ``site_report_paraphrase.pdf`` — a synthetic report of the same class with
  different wording and values (a roofing inspection, not a vehicle), rendered
  with PyMuPDF, plus ``site_report_paraphrase.bundle.json`` (jdf-cli text-layer
  parse). Plan Part 2 guardrail 2: a fix that fires only on the customer's
  text is a failure.
* ``ocr_degraded_policy.pdf`` — an auto-policy page rendered, rasterised at a
  low resolution, salted with deterministic noise and re-embedded as an
  image-only PDF (a poor scan), plus ``ocr_degraded_policy.ocr.json`` (jdf-cli
  + tesseract bundle of that scan).

Every recorded bundle carries a ``_fixture`` block: the tool, the jdf-cli
version, the date and the SHA-256 of the source bytes, so a re-recording is
traceable. Run (needs jdf-cli and tesseract language data reachable):

    .venv/bin/python scripts/make_proof_fixtures.py --photo demo/review/review.jpeg
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURES = ROOT / "tests" / "golden" / "proof_suite_v1"

PARAPHRASE_LINES = [
    ("INSPECTION REPORT", 8, (0.2, 0.4, 0.8)),
    ("LOGGED · 14 MARCH 2026    REF · SR-2026-0314-A1", 7, (0.4, 0.4, 0.4)),
    ("Roof Works — 14 Mar", 22, (0, 0, 0)),
    ("FINDINGS", 8, (0.2, 0.4, 0.8)),
    ("STATUS — Roofing membrane lifted along the north parapet after the storm; two flashings", 10, (0, 0, 0)),
    ("are loose and the gutter outlet is blocked. A licensed roofer should re-seal the membrane", 10, (0, 0, 0)),
    ("before the next rainfall to stop water entering the plant room below.", 10, (0, 0, 0)),
    ("NORTH PARAPET", 7, (0.2, 0.4, 0.8)),
    ("Membrane edge lifted over roughly four metres; ponding visible at the outlet.", 9, (0, 0, 0)),
    ("NEXT STEPS  Book a roofing contractor to re-seal the membrane and clear the outlet.", 9, (0, 0, 0)),
    ("CAPTURED · 14 MAR 2026 · 09:12 GMT+1   ·   GPS · 51.50740, -0.12780   ·   EXIF VERIFIED", 7, (0.4, 0.4, 0.4)),
    ("SITE LOCATION: 12 Harbour Quay, Plant Room Roof", 9, (0, 0, 0)),
    ("By signing below the parties confirm the observations above describe the site as found.", 8, (0, 0, 0)),
    ("CONTRACTOR: Northgate Roofing Ltd", 9, (0, 0, 0)),
    ("CLIENT: Harbour Estates Management", 9, (0, 0, 0)),
    ("Signature & Date ______________________        Signature & Date ______________________", 8, (0.4, 0.4, 0.4)),
    ("SR-2026-0314-A1", 7, (0.4, 0.4, 0.4)),
]

DEGRADED_POLICY_LINES = [
    "AUTO POLICY DECLARATIONS",
    "Policy Number: DG-4471-2026",
    "Named Insured: Priya N. Raman",
    "Policy Period: 03/01/2026 to 03/01/2027",
    "Vehicle: 2019 Toyota Corolla",
    "VIN: 2T1BURHE0KC123456",
    "Total Premium: $1,480.00",
    "Liability Limit: $250,000",
    "Collision Deductible: $500",
    "Comprehensive Deductible: $250",
    "Agent: Dana Whitfield",
    "Authorized Signature: /s/ Dana Whitfield",
    "Ref DG-4471-2026 (policy) issued to Priya N. Raman, the named insured.",
]


def _fitz():
    import fitz  # PyMuPDF

    return fitz


def render_paraphrase_pdf() -> bytes:
    fitz = _fitz()
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    y = 60.0
    for text, size, color in PARAPHRASE_LINES:
        page.insert_text((56, y), text, fontsize=size, fontname="helv", color=color)
        y += size * 2.1 if size > 12 else max(16.0, size * 1.9)
    out = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return out


def render_policy_pdf() -> bytes:
    fitz = _fitz()
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    y = 70.0
    for i, line in enumerate(DEGRADED_POLICY_LINES):
        page.insert_text((60, y), line, fontsize=15 if i == 0 else 11, fontname="helv")
        y += 30 if i == 0 else 22
    out = doc.tobytes()
    doc.close()
    return out


def degrade_to_scan(pdf_bytes: bytes, *, dpi: int = 100, noise: float = 0.02, seed: int = 20260928) -> bytes:
    """A poor scan: low-resolution grayscale raster with deterministic salt
    noise, re-embedded as an image-only PDF at the original page size."""
    fitz = _fitz()
    src = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = src[0]
    pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72.0, dpi / 72.0), colorspace=fitz.csGRAY, alpha=False)
    buf = bytearray(pix.samples)
    rng = random.Random(seed)
    n = len(buf)
    for _ in range(int(n * noise)):
        i = rng.randrange(n)
        buf[i] = 0 if rng.random() < 0.5 else 255
    # Light blur: average each pixel with its right neighbour (one pass).
    w = pix.width
    for i in range(0, n - 1):
        if (i + 1) % w:
            buf[i] = (buf[i] + buf[i + 1]) // 2
    noisy = fitz.Pixmap(fitz.csGRAY, pix.width, pix.height, bytes(buf), False)
    png = noisy.tobytes("png")
    out_doc = fitz.open()
    out_page = out_doc.new_page(width=page.rect.width, height=page.rect.height)
    out_page.insert_image(out_page.rect, stream=png)
    out = out_doc.tobytes(garbage=3, deflate=True)
    out_doc.close()
    src.close()
    return out


def record_bundle(data: bytes, filename: str, *, ocr: str | None) -> dict:
    from prompt_matrix.services.jdf_converter import pdf_to_parse_bundle
    from prompt_matrix.services.v1_orchestrator import current_jdf_cli_version

    bundle = pdf_to_parse_bundle(data, filename=filename, ocr=ocr)
    jdf = bundle.get("jdf") if isinstance(bundle.get("jdf"), dict) else None
    if jdf is not None and "resources" in jdf:
        jdf["resources"] = {}  # the base64 raster; the tests read text and OCR blocks only
    bundle["_fixture"] = {
        "tool": "scripts/make_proof_fixtures.py",
        "parser": bundle.get("parser_name"),
        "ocr": ocr,
        "jdf_cli_version": current_jdf_cli_version(),
        "recorded_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "source_bytes": len(data),
        "note": "jdf.resources (base64 raster) removed; everything else is the parser's own output",
    }
    return bundle


def _write_json(path: Path, obj: dict) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=str) + "\n", encoding="utf-8")
    print(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size} bytes)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--photo", required=True, help="the customer's site-report photo (jpeg)")
    ap.add_argument("--skip-ocr", action="store_true", help="write the PDFs and the photo copy only (no jdf-cli run)")
    args = ap.parse_args()
    FIXTURES.mkdir(parents=True, exist_ok=True)

    photo = Path(args.photo).read_bytes()
    (FIXTURES / "site_report_photo.jpg").write_bytes(photo)
    print(f"wrote site_report_photo.jpg ({len(photo)} bytes)")
    paraphrase = render_paraphrase_pdf()
    (FIXTURES / "site_report_paraphrase.pdf").write_bytes(paraphrase)
    print(f"wrote site_report_paraphrase.pdf ({len(paraphrase)} bytes)")
    degraded = degrade_to_scan(render_policy_pdf())
    (FIXTURES / "ocr_degraded_policy.pdf").write_bytes(degraded)
    print(f"wrote ocr_degraded_policy.pdf ({len(degraded)} bytes)")
    if args.skip_ocr:
        return 0
    _write_json(FIXTURES / "site_report_photo.ocr.json", record_bundle(photo, "site_report_photo.jpg", ocr="tesseract"))
    _write_json(FIXTURES / "site_report_paraphrase.bundle.json", record_bundle(paraphrase, "site_report_paraphrase.pdf", ocr=None))
    _write_json(FIXTURES / "ocr_degraded_policy.ocr.json", record_bundle(degraded, "ocr_degraded_policy.pdf", ocr="tesseract"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
