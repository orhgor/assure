#!/usr/bin/env bash
# The one-command local transcript of the customer plan V4 proof (Part 5 / Part 6
# runbook, 2026-09-28): hard-bug gate, pure behaviour tests, then the
# PostgreSQL-backed proof suite with the existing end-to-end proof, and the
# proof_diff.json the last proof test writes. Exit status is non-zero on any red
# step; nothing here is skipped or tolerated.
#
#   DATABASE_URL=postgresql://assure:assure@localhost:5432/assure scripts/run_proof_suite.sh
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"
if [ -x .venv/bin/python ]; then PY=.venv/bin/python; fi
export DATABASE_URL="${DATABASE_URL:-postgresql://assure:assure@localhost:5432/assure}"
export PROOF_DIFF_PATH="${PROOF_DIFF_PATH:-$PWD/proof_diff.json}"
export PYTHONPATH="${PYTHONPATH:-$PWD}"

echo "== 1. hard-bug gate: ruff F821 over prompt_matrix/"
"$PY" -m ruff check prompt_matrix/ --select F821 --no-cache

echo "== 2. pure behaviour: raw candidates, precedence, provenance, gates"
"$PY" -m pytest tests/test_raw_candidates.py tests/test_prompt_gates.py tests/test_lint_undefined_names.py -q -p no:cacheprovider

echo "== 3./4. proof suite on PostgreSQL ($DATABASE_URL)"
"$PY" -m pytest tests/test_proof_suite_v1.py tests/test_end_to_end_proof.py -q -p no:cacheprovider

echo "== proof_diff.json -> $PROOF_DIFF_PATH"
"$PY" - <<'EOF'
import json, os
d = json.load(open(os.environ["PROOF_DIFF_PATH"], encoding="utf-8"))
print("behaviour_changed:", d["behaviour_changed"])
print(json.dumps(d["summary"], indent=1, ensure_ascii=False))
raise SystemExit(0 if d["behaviour_changed"] else 1)
EOF
