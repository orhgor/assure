#!/usr/bin/env bash
# Pre-commit "unittest (quick)" gate.
#
# Runs the fast unit suite (excludes e2e / playwright / quality-check / the
# slow async & benchmark modules) and surfaces a targeted diagnosis when the
# full-suite-only compare_models pollution test fails, so the failure is
# actionable instead of a bare test name.
set -o pipefail

LOG=$(mktemp)
trap 'rm -f "$LOG"' EXIT

# -m "not e2e": exclude end-to-end Playwright tests from the local quick gate.
.venv/bin/pytest tests/ \
  --ignore=tests/e2e \
  --ignore=tests/playwright \
  --ignore=tests/quality_check \
  --ignore=tests/test_sanitization.py \
  --ignore=tests/test_confidence_overlay.py \
  --ignore=tests/test_full_audit.py \
  --ignore=tests/test_z3_benchmark.py \
  --ignore=tests/test_z3_explanation.py \
  --ignore=tests/test_inquire_stream.py \
  -q --tb=no -m "not e2e" 2>&1 | tee "$LOG"
rc=${PIPESTATUS[0]}

if [ "$rc" -ne 0 ] && grep -q "test_run_compare_pair_failed_model_has_empty_text" "$LOG"; then
  printf '%s\n' \
    "" \
    "NOTE: tests/test_compare_models.py::test_run_compare_pair_failed_model_has_empty_text" \
    "fails only in the full-suite run, not in isolation. If it regressed, the likely" \
    "cause is ASSURE_USE_FREE_MODELS being left set in os.environ (e.g. load_keys() /" \
    "create_app() loading .env.local), which routes run_compare_pair() through" \
    "free_pairs_different_families() instead of the mocked get_compare_pair(). The test" \
    "pins ASSURE_USE_FREE_MODELS=0, so it must pass in the full suite. Confirm with:" \
    "  .venv/bin/python -m pytest tests/test_compare_models.py -v" >&2
fi

exit "$rc"
