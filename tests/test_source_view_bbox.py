"""``SourceView.bboxToPx`` — the overlay math, run as shipped.

The "Show in source" box (prototype/source-view.js, 2026-09-28) is placed from
an anchor's relative bbox [x0, y0, x1, y1] (0–1 of the page) and the rendered
page size: ``left = x0·w, top = y0·h, width = (x1−x0)·w, height = (y1−y0)·h``.
The function is extracted brace-balanced from the shipped file (the pattern
tests/test_client_counters_parity.py uses for shell.js) and executed in node,
so a change to the maths fails here before it reaches a page.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

SOURCE_VIEW_JS = Path(__file__).resolve().parents[1] / "prototype" / "source-view.js"


def _extract_function(source: str, name: str) -> str:
    start = source.index(f"function {name}(")
    depth = 0
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"unbalanced braces after function {name}")


def _run(cases: list[dict]) -> list:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    harness = "\n".join(
        [
            _extract_function(SOURCE_VIEW_JS.read_text(encoding="utf-8"), "bboxToPx"),
            f"var cases = {json.dumps(cases)};",
            "process.stdout.write(JSON.stringify(cases.map(function (c) { return bboxToPx(c.bbox, c.rect); })));",
        ]
    )
    result = subprocess.run([node, "-e", harness], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_bbox_to_px_scales_by_the_rendered_page() -> None:
    out = _run(
        [
            {"bbox": [0.1, 0.2, 0.5, 0.25], "rect": {"width": 794, "height": 1123}},
            {"bbox": {"x0": 0, "y0": 0, "x1": 1, "y1": 1}, "rect": {"width": 400, "height": 600}},
        ]
    )
    box = out[0]
    assert box["left"] == pytest.approx(79.4) and box["top"] == pytest.approx(224.6)
    assert box["width"] == pytest.approx(317.6) and box["height"] == pytest.approx(56.15)
    assert out[1] == {"left": 0, "top": 0, "width": 400, "height": 600}


def test_bbox_to_px_re_lays_out_with_zoom() -> None:
    """The same bbox at zoom 1 and 1.5: every edge scales by 1.5 (the ResizeObserver path)."""
    a, b = _run(
        [
            {"bbox": [0.25, 0.5, 0.75, 0.6], "rect": {"width": 794, "height": 1123}},
            {"bbox": [0.25, 0.5, 0.75, 0.6], "rect": {"width": 794 * 1.5, "height": 1123 * 1.5}},
        ]
    )
    for key in ("left", "top", "width", "height"):
        assert b[key] == pytest.approx(a[key] * 1.5)


def test_bbox_to_px_clamps_orders_and_refuses_garbage() -> None:
    out = _run(
        [
            {"bbox": [0.9, 0.9, 0.1, 0.1], "rect": {"width": 100, "height": 200}},   # corners swapped
            {"bbox": [-0.5, 0.5, 1.5, 0.75], "rect": {"width": 100, "height": 200}},  # outside the page
            {"bbox": [0.1, 0.2, 0.3], "rect": {"width": 100, "height": 200}},         # three numbers
            {"bbox": [0.1, "x", 0.3, 0.4], "rect": {"width": 100, "height": 200}},    # not a number
            {"bbox": [0.1, 0.2, 0.3, 0.4], "rect": {"width": 0, "height": 200}},      # nothing rendered yet
            {"bbox": None, "rect": {"width": 100, "height": 200}},
        ]
    )
    assert out[0] == {"left": 10, "top": 20, "width": 80, "height": 160}
    assert out[1] == {"left": 0, "top": 100, "width": 100, "height": 50}
    assert out[2:] == [None, None, None, None]
