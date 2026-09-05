#!/usr/bin/env bash
# Merge model provider keys from prompt_matrix/.env into EC2 .env.production and restart.
# Never prints key values. Requires AWS SSM + local prompt_matrix/.env.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ENV_FILE="${ASSURE_KEYS_FILE:-$ROOT/prompt_matrix/.env}"
INSTANCE_ID="${ASSURE_INSTANCE_ID:-i-09d0ad0b561113abe}"
REGION="${AWS_REGION:-us-east-1}"

PROVIDER_KEYS=(
  GEMINI_API_KEY
  GOOGLE_API_KEY
  ANTHROPIC_API_KEY
  CLAUDE_API_KEY
  DEEPSEEK_API_KEY
  MOONSHOT_API_KEY
  KIMI_API_KEY
  ANTHROPIC_WORKSPACE_ID
)

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE — paste provider keys there first." >&2
  exit 1
fi

BODY="$(mktemp)"
REMOTE="$(mktemp)"
trap 'rm -f "$BODY" "$REMOTE"' EXIT

python3 - "$ENV_FILE" "$REMOTE" "${PROVIDER_KEYS[@]}" <<'PY'
import base64
import json
import pathlib
import sys

env_path = pathlib.Path(sys.argv[1])
out_path = pathlib.Path(sys.argv[2])
key_names = sys.argv[3:]

values: dict[str, str] = {}
for line in env_path.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    name, val = line.split("=", 1)
    name = name.strip()
    val = val.strip().strip('"').strip("'")
    if name in key_names and val:
        values[name] = val

if not values:
    raise SystemExit(f"No provider keys found in {env_path}")

payload = {k: base64.b64encode(v.encode()).decode() for k, v in values.items()}
payload_json = json.dumps(payload)
script = '''#!/usr/bin/env bash
set -euo pipefail
python3 - <<'INNER'
import base64, json, os, pathlib
payload = json.loads(''' + repr(payload_json) + ''')
path = pathlib.Path("/home/ubuntu/assure/.env.production")
lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
for name, b64 in payload.items():
    val = base64.b64decode(b64).decode()
    lines = [ln for ln in lines if not ln.startswith(name + "=")]
    lines.append(name + "=" + val)
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text("\\n".join(lines) + "\\n", encoding="utf-8")
os.chmod(path, 0o600)
INNER
chown ubuntu:ubuntu /home/ubuntu/assure/.env.production
cd /home/ubuntu/assure
sudo -u ubuntu docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --force-recreate assure-app
for i in $(seq 1 30); do
  curl -sf http://127.0.0.1:8765/health >/dev/null 2>&1 && break
  sleep 2
done
echo "---STATUS---"
curl -sf http://127.0.0.1:8765/api/status | python3 -c "import json,sys; p=json.load(sys.stdin)['providers']; print({k:v['connected'] for k,v in p.items() if k in ('gemini','claude','deepseek','kimi')})"
'''
out_path.write_text(script, encoding="utf-8")
print(f"Prepared {len(values)} key(s):", ", ".join(sorted(values)))
PY

B64="$(base64 < "$REMOTE" | tr -d '\n')"
echo "Sending provider key merge to $INSTANCE_ID ($REGION)..."
CMD_ID="$(aws ssm send-command \
  --region "$REGION" \
  --instance-ids "$INSTANCE_ID" \
  --document-name AWS-RunShellScript \
  --comment "Assure: wire provider keys into .env.production" \
  --timeout-seconds 300 \
  --parameters "commands=[\"echo ${B64} | base64 -d | bash\"]" \
  --query 'Command.CommandId' \
  --output text)"

echo "Command ID: $CMD_ID"
echo "Polling..."
for i in $(seq 1 24); do
  STATUS="$(aws ssm get-command-invocation \
    --region "$REGION" \
    --command-id "$CMD_ID" \
    --instance-id "$INSTANCE_ID" \
    --query 'Status' \
    --output text 2>/dev/null || echo Pending)"
  echo "  [$i/24] status=$STATUS"
  case "$STATUS" in
    Success|Failed|Cancelled|TimedOut) break ;;
  esac
  sleep 10
done

aws ssm get-command-invocation \
  --region "$REGION" \
  --command-id "$CMD_ID" \
  --instance-id "$INSTANCE_ID" \
  --query '[Status,StandardOutputContent,StandardErrorContent]' \
  --output text | python3 - <<'PY'
import sys
status, out, err = sys.stdin.read().split("\t", 2)
print("SSM status:", status)
if "---STATUS---" in out:
    print(out.split("---STATUS---", 1)[1].strip())
else:
    for line in out.splitlines()[-8:]:
        print(line)
if err.strip():
    print("stderr (tail):")
    print("\n".join(err.splitlines()[-8:]))
if status != "Success":
    raise SystemExit(1)
PY

curl -sf "https://getassureai.com/api/status" | python3 -c "
import json, sys
p = json.load(sys.stdin)['providers']
for name in ('gemini', 'claude', 'deepseek', 'kimi'):
    print(f\"public {name}: connected={p[name]['connected']}\")
"
