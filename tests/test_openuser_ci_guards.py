"""Guards for CI test layout (OpenUser is local-only; Playwright runs in ci.yml)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_ci_has_playwright_job_not_openuser():
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    ux = (ROOT / ".github/workflows/ux-tests.yml").read_text(encoding="utf-8")
    assert "openuser" not in ci
    assert "playwright-tests" in ci
    assert "tests/playwright/" in ci
    assert "playwright install chromium" in ci
    assert "workflow_dispatch:" in ux
    assert "if: false" in ux or "disabled" in ux


def test_budget_reset_clears_daily_compile_quota():
    script = (ROOT / "scripts/aws/reset-openuser-project-budget.sh").read_text(encoding="utf-8")
    assert "DELETE FROM daily_compile_limits" in script
    assert "project_budgets" in script


def test_runner_fail_fast_and_retries():
    runner = (ROOT / "openuser/runner.mjs").read_text(encoding="utf-8")
    assert "__openUserReadFailure" in runner
    assert "init_workbench retry" in runner
    assert "retry once after" in runner
    assert "HTTP 429" in runner
    assert "daily compile" in runner
