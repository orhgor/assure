#!/usr/bin/env bash
# Reset token budget for the OpenUser CI project on staging (or production) EC2.
set -euo pipefail

INSTANCE_ID="${1:-${STAGING_INSTANCE_ID:-i-03e39eccc57572191}}"
PROJECT_ID="${OPENUSER_PROJECT_ID:-prj_YhR-5cBgb_7E}"
TOKEN_LIMIT="${OPENUSER_TOKEN_LIMIT:-5000000}"
REGION="${AWS_REGION:-us-east-1}"

PY=$(cat <<PY
import sqlite3
pid = "${PROJECT_ID}"
limit = int("${TOKEN_LIMIT}")
conn = sqlite3.connect("/app/data/history.sqlite")
before = conn.execute(
    "SELECT token_limit, tokens_used FROM project_budgets WHERE project_id=?",
    (pid,),
).fetchone()
conn.execute(
    "INSERT OR IGNORE INTO project_budgets (project_id, token_limit, tokens_used) VALUES (?, ?, 0)",
    (pid, limit),
)
conn.execute(
    "UPDATE project_budgets SET tokens_used=0, token_limit=? WHERE project_id=?",
    (limit, pid),
)
# Cold-compile UX specs miss the AST cache; a leftover daily cap 429s them
# and the runner used to hang until waitForFunction timed out.
try:
    daily_before = conn.execute(
        "SELECT date, count FROM daily_compile_limits WHERE project_id=?",
        (pid,),
    ).fetchall()
    conn.execute("DELETE FROM daily_compile_limits WHERE project_id=?", (pid,))
except sqlite3.OperationalError as exc:
    daily_before = str(exc)
conn.commit()
after = conn.execute(
    "SELECT token_limit, tokens_used FROM project_budgets WHERE project_id=?",
    (pid,),
).fetchone()
print("before", before)
print("after", after)
print("daily_compile_limits_before", daily_before)
PY
)

B64=$(printf '%s' "$PY" | base64 | tr -d '\n')
CMD_ID=$(aws ssm send-command \
  --region "$REGION" \
  --instance-ids "$INSTANCE_ID" \
  --document-name AWS-RunShellScript \
  --timeout-seconds 60 \
  --parameters "commands=[\"docker exec assure-assure-app-1 python3 -c \\\"import base64; exec(base64.b64decode('${B64}').decode())\\\"\"]" \
  --query 'Command.CommandId' --output text)

echo "SSM command: $CMD_ID (instance $INSTANCE_ID, project $PROJECT_ID)"
sleep 10
aws ssm get-command-invocation \
  --region "$REGION" \
  --command-id "$CMD_ID" \
  --instance-id "$INSTANCE_ID" \
  --query '[Status,StandardOutputContent]' \
  --output text
