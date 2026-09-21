"""Guards: OpenUser stays out of CI, MCP, and package scripts."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_ci_has_playwright_job_not_openuser():
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "openuser" not in ci.lower()
    assert "playwright-tests" in ci
    assert "tests/playwright/" in ci
    assert "playwright install chromium" in ci
    assert not (ROOT / ".github/workflows/ux-tests.yml").exists()
    assert not (ROOT / "openuser").exists()
    mcp = (ROOT / ".cursor/mcp.json").read_text(encoding="utf-8")
    assert "openuser" not in mcp.lower()
    pkg = (ROOT / "package.json").read_text(encoding="utf-8")
    assert "openuser" not in pkg.lower()
