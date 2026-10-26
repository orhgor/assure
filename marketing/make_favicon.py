#!/usr/bin/env python3
"""Generate marketing/static/favicon.ico from marketing/static/favicon.svg.

Tries cairosvg, then rsvg-convert, then macOS qlmanage. The resulting .ico is
committed, so CI builds do not need any of these tools.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SVG = HERE / "static" / "favicon.svg"
ICO = HERE / "static" / "favicon.ico"
SIZES = [16, 32, 48]


def rasterize(size: int) -> bytes:
    if shutil.which("rsvg-convert"):
        return subprocess.run(
            ["rsvg-convert", "-w", str(size), "-h", str(size), str(SVG)],
            check=True, capture_output=True,
        ).stdout
    try:
        import cairosvg  # type: ignore

        return cairosvg.svg2png(url=str(SVG), output_width=size, output_height=size)
    except ImportError:
        pass
    if shutil.which("qlmanage"):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(
                ["qlmanage", "-t", "-s", str(size), "-o", tmp, str(SVG)],
                check=True, capture_output=True,
            )
            out = Path(tmp) / f"{SVG.name}.png"
            return out.read_bytes()
    raise SystemExit("no SVG rasterizer found (cairosvg, rsvg-convert, qlmanage)")


def main() -> int:
    if not SVG.is_file():
        raise SystemExit(f"missing {SVG}")
    from PIL import Image
    from io import BytesIO
    import struct

    frames = []
    for size in SIZES:
        png = rasterize(size)
        img = Image.open(BytesIO(png)).convert("RGBA")
        if img.size != (size, size):
            img = img.resize((size, size), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, format="PNG")
        frames.append((size, buf.getvalue()))

    # Manual ICO container with PNG-compressed entries (Vista+ format).
    out = BytesIO()
    out.write(struct.pack("<HHH", 0, 1, len(frames)))
    offset = 6 + 16 * len(frames)
    for size, png in frames:
        w = size if size < 256 else 0
        out.write(struct.pack("<BBBBHHII", w, w, 0, 0, 1, 32, len(png), offset))
        offset += len(png)
    for _, png in frames:
        out.write(png)
    ICO.write_bytes(out.getvalue())
    print(f"wrote {ICO} ({ICO.stat().st_size} bytes, {len(frames)} frames)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())