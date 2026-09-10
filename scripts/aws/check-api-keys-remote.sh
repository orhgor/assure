#!/usr/bin/env bash
# Run on EC2 via SSM — key presence + live probes inside Docker (no secret output).
set -uo pipefail

echo "=== DOCKER ENV PRESENCE ==="
docker exec assure-assure-app-1 sh -c '
for k in ANTHROPIC_API_KEY CLAUDE_API_KEY GEMINI_API_KEY GOOGLE_API_KEY DEEPSEEK_API_KEY GROQ_API_KEY OPENROUTER_API_KEY MOONSHOT_API_KEY KIMI_API_KEY PERPLEXITY_API_KEY CLERK_SECRET_KEY CLERK_PUBLISHABLE_KEY STRIPE_SECRET_KEY STRIPE_WEBHOOK_SECRET SUPABASE_URL SUPABASE_SECRET_KEY ENCRYPTION_KEY PLAUSIBLE_API_KEY SENTRY_DSN AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY R2_ACCOUNT_ID SUBSTRATE_INGEST_SECRET; do
  v=$(printenv "$k" 2>/dev/null || true)
  if [ -n "$v" ]; then echo "$k=set"; else echo "$k=missing"; fi
done
'

echo "=== LIVE PROBES ==="
docker exec assure-assure-app-1 sh -c '
probe() {
  name="$1"; shift
  code=$(curl -sS -m 15 -o /tmp/p.out -w "%{http_code}" "$@" 2>/dev/null || echo 000)
  case "$code" in
    200|201|202) echo "$name: ACTIVE HTTP=$code" ;;
    401|403) echo "$name: INVALID HTTP=$code" ;;
    000) echo "$name: ERROR unreachable" ;;
    *) echo "$name: ACTIVE HTTP=$code" ;;
  esac
}

k="${ANTHROPIC_API_KEY:-${CLAUDE_API_KEY:-}}"
if [ -n "$k" ]; then
  probe anthropic -X POST https://api.anthropic.com/v1/messages \
    -H "x-api-key: $k" -H "anthropic-version: 2023-06-01" -H "content-type: application/json" \
    -d "{\"model\":\"claude-3-5-haiku-latest\",\"max_tokens\":1,\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}"
else echo "anthropic: SKIP missing"; fi

k="${GEMINI_API_KEY:-${GOOGLE_API_KEY:-}}"
if [ -n "$k" ]; then
  probe gemini "https://generativelanguage.googleapis.com/v1/models?key=$k"
else echo "gemini: SKIP missing"; fi

if [ -n "${DEEPSEEK_API_KEY:-}" ]; then
  probe deepseek -X POST https://api.deepseek.com/chat/completions \
    -H "Authorization: Bearer $DEEPSEEK_API_KEY" -H "content-type: application/json" \
    -d "{\"model\":\"deepseek-chat\",\"max_tokens\":1,\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}"
else echo "deepseek: SKIP missing"; fi

k="${MOONSHOT_API_KEY:-${KIMI_API_KEY:-}}"
if [ -n "$k" ]; then
  probe kimi -X POST https://api.moonshot.ai/v1/chat/completions \
    -H "Authorization: Bearer $k" -H "content-type: application/json" \
    -d "{\"model\":\"moonshot-v1-8k\",\"max_tokens\":1,\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}"
else echo "kimi: SKIP missing"; fi

if [ -n "${CLERK_SECRET_KEY:-}" ]; then
  probe clerk -H "Authorization: Bearer $CLERK_SECRET_KEY" "https://api.clerk.com/v1/users?limit=1"
else echo "clerk: SKIP missing"; fi

if [ -n "${STRIPE_SECRET_KEY:-}" ]; then
  probe stripe -u "$STRIPE_SECRET_KEY:" https://api.stripe.com/v1/balance
else echo "stripe: SKIP missing"; fi

if [ -n "${PLAUSIBLE_API_KEY:-}" ]; then
  probe plausible -H "Authorization: Bearer $PLAUSIBLE_API_KEY" -H "content-type: application/json" \
    -d "{\"site_id\":\"app.getassureai.com\",\"metrics\":[\"visitors\"],\"date_range\":\"7d\"}" \
    https://plausible.io/api/v2/query
else echo "plausible: SKIP missing"; fi
'
