# Red-Hat Log

Every prompt that ships is red-hatted before delivery. This log
records the pass.

## Pre-commit exception policy

`--no-verify` is banned by default. A one-time exception is
allowed only when all four conditions hold:
  1. The failing hook is unrelated to the change (verified by
     scoping: the change touches no file the hook depends on).
  2. The failing test passes in isolation on clean HEAD.
  3. The failing test is listed in .pytest_cache/lastfailed from
     before this session.
  4. The commit message documents the exception and names the
     failing test.

## Pre-commit exception log

### 2026-09-16 · b43cb2b

**Exception:** --no-verify
**Failing test:** tests/test_compare_models.py::
  test_run_compare_pair_failed_model_has_empty_text
**Conditions 1-4:** all met
**Follow-up:** b9e974f addressed pollution via env pinning;
  real fix landed when override=True was removed from
  keys.load_keys().
