#!/usr/bin/env python3
"""Build single-page demo PDFs from markdown source (PyMuPDF)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"


def _text_from_md(path: Path) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    for line in lines:
        if line.startswith("#"):
            out.append(line.lstrip("# ").strip())
            out.append("")
        elif line.strip() == "---":
            out.append("")
        else:
            out.append(line)
    return "\n".join(out)


def write_pdf(text: str, dest: Path) -> None:
    import fitz  # PyMuPDF

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)  # US Letter
    rect = fitz.Rect(54, 54, 558, 738)
    page.insert_textbox(
        rect,
        text,
        fontsize=10,
        fontname="helv",
        align=fitz.TEXT_ALIGN_LEFT,
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(dest))
    doc.close()


def main() -> int:
    policy_md = ASSETS / "naic-underwriting-policy-redacted.md"
    one_pager_md = ROOT / "one-pager.md"
    targets = [
        (policy_md, ASSETS / "naic-underwriting-policy-redacted.pdf"),
        (one_pager_md, ASSETS / "assure-insurance-demo-one-pager.pdf"),
    ]
    for src, dest in targets:
        if not src.is_file():
            print(f"skip missing {src}", file=sys.stderr)
            continue
        write_pdf(_text_from_md(src), dest)
        print(f"wrote {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
