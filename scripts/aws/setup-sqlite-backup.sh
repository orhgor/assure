#!/usr/bin/env bash
# Install SQLite → R2 backup on the prod EC2 instance via SSM.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ENV_FILE="$ROOT/.env.production"
INSTANCE_ID="${1:-i-09d0ad0b561113abe}"
REGION="${AWS_REGION:-us-east-1}"
CRON_SCHEDULE="${BACKUP_CRON:-0 2 * * *}"

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing $ENV_FILE" >&2
  exit 1
fi

# shellcheck disable=SC1090
source "$ENV_FILE"

missing=()
for var in R2_ACCOUNT_ID R2_BUCKET AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY; do
  if [ -z "${!var:-}" ]; then
    missing+=("$var")
  fi
done
if [ "${#missing[@]}" -gt 0 ]; then
  echo "Set these in $ENV_FILE before running:" >&2
  printf '  %s\n' "${missing[@]}" >&2
  echo "" >&2
  echo "Create an R2 bucket + API token in Cloudflare dashboard, then add:" >&2
  echo "  R2_ACCOUNT_ID, R2_BUCKET, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY" >&2
  exit 1
fi

BACKUP_SCRIPT_B64="$(base64 < "$ROOT/scripts/aws/backup-sqlite.sh" | tr -d '\n')"

INNER="$(mktemp)"
trap 'rm -f "$INNER"' EXIT

cat > "$INNER" <<SCRIPT
#!/usr/bin/env bash
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y sqlite3 curl unzip
ARCH="\$(uname -m)"
case "\$ARCH" in
  aarch64|arm64) AWS_ARCH=aarch64 ;;
  x86_64|amd64) AWS_ARCH=x86_64 ;;
  *) echo "Unsupported arch: \$ARCH" >&2; exit 1 ;;
esac
if ! command -v aws >/dev/null 2>&1; then
  curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-\${AWS_ARCH}.zip" -o /tmp/awscliv2.zip
  unzip -q /tmp/awscliv2.zip -d /tmp
  /tmp/aws/install --update
  rm -rf /tmp/aws /tmp/awscliv2.zip
fi

install -d -m 700 /etc/assure
cat > /etc/assure/backup.env <<'ENVEOF'
R2_ACCOUNT_ID=${R2_ACCOUNT_ID}
R2_BUCKET=${R2_BUCKET}
R2_BACKUP_PREFIX=${R2_BACKUP_PREFIX:-backups}
R2_BACKUP_RETAIN_DAYS=${R2_BACKUP_RETAIN_DAYS:-30}
R2_BACKUP_MIN_KEEP=${R2_BACKUP_MIN_KEEP:-3}
R2_STORAGE_CAP_GB=${R2_STORAGE_CAP_GB:-8}
AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID}
AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}
AWS_DEFAULT_REGION=${AWS_DEFAULT_REGION:-auto}
ASSURE_SQLITE_PATH=${ASSURE_SQLITE_PATH:-/home/ubuntu/assure/data/history.sqlite}
ENVEOF
chmod 600 /etc/assure/backup.env

echo ${BACKUP_SCRIPT_B64} | base64 -d > /home/ubuntu/backup_sqlite.sh
chmod 750 /home/ubuntu/backup_sqlite.sh

( crontab -l 2>/dev/null | grep -v backup_sqlite.sh || true
  echo "${CRON_SCHEDULE} /home/ubuntu/backup_sqlite.sh >> /var/log/assure-backup.log 2>&1"
) | crontab -

/home/ubuntu/backup_sqlite.sh
echo BACKUP_SETUP_OK
SCRIPT

B64="$(base64 < "$INNER" | tr -d '\n')"
CMD="echo ${B64} | base64 -d | bash"

echo "Installing backup on $INSTANCE_ID..."
CMD_ID="$(aws ssm send-command \
  --region "$REGION" \
  --instance-ids "$INSTANCE_ID" \
  --document-name AWS-RunShellScript \
  --comment "Assure SQLite R2 backup setup" \
  --timeout-seconds 600 \
  --parameters "commands=$CMD" \
  --query 'Command.CommandId' \
  --output text)"

echo "Command ID: $CMD_ID"
for i in $(seq 1 30); do
  STATUS="$(aws ssm get-command-invocation \
    --region "$REGION" \
    --command-id "$CMD_ID" \
    --instance-id "$INSTANCE_ID" \
    --query 'Status' \
    --output text 2>/dev/null || echo Pending)"
  echo "  [$i] status=$STATUS"
  case "$STATUS" in
    Success|Failed|Cancelled|TimedOut)
      aws ssm get-command-invocation \
        --region "$REGION" \
        --command-id "$CMD_ID" \
        --instance-id "$INSTANCE_ID" \
        --query '{Status:Status,Stdout:StandardOutputContent,Stderr:StandardErrorContent}' \
        --output text
      exit 0
      ;;
  esac
  sleep 10
done
