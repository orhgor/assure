#!/usr/bin/env bash
# Bootstrap native Assure on EC2 when SSH port 22 is closed (SSM-only access).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
INSTANCE_ID="${ASSURE_INSTANCE_ID:-i-03e39eccc57572191}"
REGION="${AWS_REGION:-us-east-1}"
REMOTE_DIR="/home/ubuntu/assure"
SERVICE_NAME="${ASSURE_SERVICE_NAME:-assure}"

FILES=(
  scripts/aws/assure.service
  scripts/aws/install-lean-service.sh
  scripts/aws/install-auto-heal-cron-lean.sh
  scripts/lean-deploy.sh
  prompt_matrix/app.py
  prompt_matrix/config/system_prompt.py
  prompt_matrix/keys.py
  prompt_matrix/pem_runner.py
  prompt_matrix/services/auto_compiler.py
  prompt_matrix/services/compare_models.py
  prompt_matrix/services/perplexity_agent.py
  prompt_matrix/static/orchestrator.js
  prompt_matrix/static/command_bar.js
  prompt_matrix/ui_cache.py
)

_send_ssm() {
  local label="$1"
  local body_file="$2"
  local b64
  b64="$(base64 < "$body_file" | tr -d '\n')"
  local b64_len="${#b64}"
  if (( b64_len > 90000 )); then
    echo "SSM payload too large for $label (${b64_len} bytes b64)." >&2
    exit 1
  fi
  echo "==> SSM: $label (${b64_len} bytes b64)"
  local cmd_id
  cmd_id="$(aws ssm send-command \
    --region "$REGION" \
    --instance-ids "$INSTANCE_ID" \
    --document-name AWS-RunShellScript \
    --comment "Assure lean bootstrap: $label" \
    --timeout-seconds 900 \
    --parameters "commands=[\"echo ${b64} | base64 -d | bash\"]" \
    --query 'Command.CommandId' \
    --output text)"
  echo "Command ID: $cmd_id"
  local status="Pending"
  for i in $(seq 1 120); do
    status="$(aws ssm get-command-invocation \
      --region "$REGION" \
      --command-id "$cmd_id" \
      --instance-id "$INSTANCE_ID" \
      --query 'Status' \
      --output text 2>/dev/null || echo Pending)"
    echo "  [$i] $status"
    case "$status" in
      Success|Failed|Cancelled|TimedOut) break ;;
    esac
    sleep 5
  done
  aws ssm get-command-invocation \
    --region "$REGION" \
    --command-id "$cmd_id" \
    --instance-id "$INSTANCE_ID" \
    --query '[Status,StandardOutputContent,StandardErrorContent]' \
    --output text
  if [[ "$status" != Success ]]; then
    return 1
  fi
}

build_sync_script() {
  local -a batch_files=("$@")
  local tmp
  tmp="$(mktemp)"
  {
    echo '#!/usr/bin/env bash'
    echo 'set -euo pipefail'
    echo "cd \"$REMOTE_DIR\""
    for rel in "${batch_files[@]}"; do
      abs="$ROOT/$rel"
      [[ -f "$abs" ]] || { echo "missing $rel" >&2; exit 1; }
      b64="$(base64 < "$abs" | tr -d '\n')"
      echo "mkdir -p \"\$(dirname \"$rel\")\""
      echo "echo '$b64' | base64 -d > \"$rel\""
    done
    echo 'chown -R ubuntu:ubuntu '"$REMOTE_DIR"
  } > "$tmp"
  echo "$tmp"
}

BATCH1=(
  scripts/aws/assure.service
  scripts/aws/install-lean-service.sh
  scripts/aws/install-auto-heal-cron-lean.sh
  scripts/lean-deploy.sh
  prompt_matrix/app.py
  prompt_matrix/ui_cache.py
  prompt_matrix/celery_app.py
  prompt_matrix/signals.py
  prompt_matrix/routers/runs_routes.py
)
BATCH2=(
  prompt_matrix/config/system_prompt.py
  prompt_matrix/keys.py
  prompt_matrix/pem_runner.py
  prompt_matrix/services/auto_compiler.py
  prompt_matrix/services/compare_models.py
  prompt_matrix/services/perplexity_agent.py
)
BATCH3=(
  prompt_matrix/static/orchestrator.js
  prompt_matrix/static/command_bar.js
)
BATCH3B=(
  prompt_matrix/static/operator_prompt.js
  scripts/aws/patch-staging-canvas-empty.py
)
BATCH4=(
  prompt_matrix/static/founder_draft.js
  prompt_matrix/static/compare_pane.js
  prompt_matrix/static/compare_pane.css
)
BATCH4B=(
  prompt_matrix/static/founder_workbench.css
  prompt_matrix/static/founder_polish.js
  prompt_matrix/static/founder_scan.js
)
BATCH4C=(
  prompt_matrix/templates/base.html
  prompt_matrix/ui_cache.py
)

SYNC1="$(build_sync_script "${BATCH1[@]}")"
SYNC2="$(build_sync_script "${BATCH2[@]}")"
SYNC3="$(build_sync_script "${BATCH3[@]}")"
SYNC3B="$(build_sync_script "${BATCH3B[@]}")"
SYNC4="$(build_sync_script "${BATCH4[@]}")"
SYNC4B="$(build_sync_script "${BATCH4B[@]}")"
SYNC4C="$(build_sync_script "${BATCH4C[@]}")"
INSTALL="$(mktemp)"
cat > "$INSTALL" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
cd /home/ubuntu/assure
chmod +x scripts/aws/install-lean-service.sh scripts/lean-deploy.sh scripts/aws/install-auto-heal-cron-lean.sh 2>/dev/null || true
chown -R ubuntu:ubuntu /home/ubuntu/assure
sudo -u ubuntu env ASSURE_ENVIRONMENT=staging ASSURE_ROOT=/home/ubuntu/assure bash scripts/aws/install-lean-service.sh
EOF

trap 'rm -f "$SYNC1" "$SYNC2" "$SYNC3" "$SYNC3B" "$SYNC4" "$SYNC4B" "$SYNC4C" "$INSTALL"' EXIT

_send_ssm "sync batch 1" "$SYNC1"
_send_ssm "sync batch 2" "$SYNC2"
_send_ssm "sync orchestrator + command_bar" "$SYNC3"
_send_ssm "sync operator_prompt + staging patch" "$SYNC3B"
_send_ssm "sync founder draft + compare pane" "$SYNC4"
_send_ssm "sync founder workbench css + actions" "$SYNC4B"
_send_ssm "sync base template + ui cache" "$SYNC4C"
PATCH="$(mktemp)"
cat > "$PATCH" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
cd /home/ubuntu/assure
python3 scripts/aws/patch-staging-canvas-empty.py
chown ubuntu:ubuntu prompt_matrix/templates/index.html
EOF
_send_ssm "patch empty staging canvas" "$PATCH"
rm -f "$PATCH"

if [[ "${ASSURE_LEAN_DEPLOY_ONLY:-}" == "1" ]]; then
  RESTART="$(mktemp)"
  cat > "$RESTART" <<EOF
#!/usr/bin/env bash
set -euo pipefail
cd /home/ubuntu/assure
if [[ -f .env.staging ]]; then
  if grep -q '^PEM_TIMEOUT_SECONDS=' .env.staging; then
    sed -i 's/^PEM_TIMEOUT_SECONDS=.*/PEM_TIMEOUT_SECONDS=180/' .env.staging
  else
    echo 'PEM_TIMEOUT_SECONDS=180' >> .env.staging
  fi
fi
chown -R ubuntu:ubuntu /home/ubuntu/assure
sudo systemctl restart ${SERVICE_NAME}
for i in \$(seq 1 30); do
  curl -sf http://127.0.0.1:8765/health >/dev/null 2>&1 && exit 0
  sleep 1
done
exit 1
EOF
  trap 'rm -f "$SYNC1" "$SYNC2" "$SYNC3" "$SYNC3B" "$SYNC4" "$SYNC4B" "$SYNC4C" "$INSTALL" "$RESTART"' EXIT
  _send_ssm "restart native service" "$RESTART"
else
  _send_ssm "install native service" "$INSTALL"
fi

echo ""
echo "==> Public health check"
curl -sS --max-time 15 https://staging.getassureai.com/health | python3 -m json.tool | head -35
