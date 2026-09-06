"""Guards that keep OpenUser UX specs from racing deploys or hanging on 429s."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_ci_waits_for_staging_before_openuser():
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    ux = (ROOT / ".github/workflows/ux-tests.yml").read_text(encoding="utf-8")
    assert "wait-staging-ready.sh" in ci
    assert "wait-staging-ready.sh" in ux
    assert "STAGING_EXPECT_SHA" in ci
    assert "STAGING_EXPECT_SHA" in ux


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
