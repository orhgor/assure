"""Screenshot baseline helpers for visual regression tests."""

from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops
from playwright.sync_api import Locator, Page

SNAPSHOT_ROOT = Path(__file__).resolve().parent / "snapshots"


def _pixel_diff_ratio(actual: bytes, expected: bytes) -> float:
    img_a = Image.open(BytesIO(actual)).convert("RGB")
    img_b = Image.open(BytesIO(expected)).convert("RGB")
    if img_a.size != img_b.size:
        img_b = img_b.resize(img_a.size)
    diff = ImageChops.difference(img_a, img_b)
    histogram = diff.histogram()
    diff_pixels = sum(histogram[1:]) // 3
    total = img_a.size[0] * img_a.size[1]
    return diff_pixels / total if total else 0.0


def assert_screenshot(
    page: Page,
    name: str,
    *,
    locator: Locator | None = None,
    max_diff_pixel_ratio: float = 0.03,
) -> None:
    """Compare element screenshot to committed baseline (refresh with QUALITY_CHECK_UPDATE_SNAPSHOTS=1)."""
    SNAPSHOT_ROOT.mkdir(parents=True, exist_ok=True)
    target = SNAPSHOT_ROOT / name
    update = os.environ.get("QUALITY_CHECK_UPDATE_SNAPSHOTS") == "1"
    page.add_style_tag(
        content="*, *::before, *::after { animation: none !important; transition: none !important; }"
    )
    png = locator.screenshot() if locator is not None else page.screenshot(full_page=False)
    if update or not target.is_file():
        target.write_bytes(png)
        return
    expected = target.read_bytes()
    if png == expected:
        return
    ratio = _pixel_diff_ratio(png, expected)
    assert ratio <= max_diff_pixel_ratio, (
        f"Snapshot mismatch for {name} (diff {ratio:.4f} > {max_diff_pixel_ratio}). "
        "Set QUALITY_CHECK_UPDATE_SNAPSHOTS=1 to refresh baselines."
    )
