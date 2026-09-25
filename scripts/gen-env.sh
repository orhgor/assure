#!/usr/bin/env bash
# Write the repo-root .env for docker compose up -d. That is all this script
# does: it asks for the values it cannot know, generates the secrets, writes one
# file. It never installs, pulls, downloads or starts anything.
#
#   ./scripts/gen-env.sh            # asks: target, then the questions below
#   ./scripts/gen-env.sh ec2        # server defaults (shell on 0.0.0.0:80, production flags)
#   ./scripts/gen-env.sh local      # laptop defaults (shell on 127.0.0.1:80, development flags)
#   ./scripts/gen-env.sh ec2 --yes  # no questions, defaults everywhere (CI / cloud-init)
#   printf 'AKIA…\nSECRET\n\n\n\n\n\n\n\n' | ./scripts/gen-env.sh ec2 --ask   # answers from stdin
#
# Questions (Enter keeps the default shown in brackets):
#   AWS access key id + secret (hidden; empty = instance IAM role or no AWS at
#   all), AWS region, S3 bucket (empty = files stay on the instance), app port
#   and bind, models local/bedrock (bedrock = Sonnet 5 drafts, Opus 5 analyses,
#   needs the AWS credentials or an instance role with bedrock:InvokeModel), NVIDIA GPU yes/no (auto-detected; yes = docker-compose.gpu.yml
#   joins every compose command and the 7B/8B models become the default), the
#   two Ollama model tags, Textract monthly cap.
# Generated, never asked: POSTGRES_PASSWORD, PEM_SECRET_KEY, ENCRYPTION_KEY,
# SHELL_ACCESS_KEY (printed at the end — that is what the browser asks for).
# Refuses to overwrite an existing .env unless FORCE=1. Needs only bash + openssl.
set -euo pipefail

TARGET=""
YES=0
ASK=0
OUT=".env"
for arg in "$@"; do
  case "$arg" in
    ec2|local) TARGET="$arg" ;;
    --yes|-y) YES=1 ;;
    --ask) ASK=1 ;;
    -h|--help) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) OUT="$arg" ;;
  esac
done
cd "$(dirname "$0")/.."

if [[ ! -t 0 && "$ASK" != "1" ]]; then YES=1; fi   # no terminal: defaults, no questions (--ask reads answers from stdin anyway)

ask() {  # ask VAR "question" "default"  → sets VAR
  local var="$1" q="$2" def="$3" ans=""
  if [[ "$YES" == "1" ]]; then printf -v "$var" '%s' "$def"; return; fi
  # bash prints a -p prompt only when stdin is a terminal; write it ourselves so
  # answers piped in (--ask) still show which question they answered.
  printf '%s [%s]: ' "$q" "${def:-empty}" >&2
  read -r ans || ans=""
  [[ -t 0 ]] || echo >&2
  printf -v "$var" '%s' "${ans:-$def}"
}
ask_secret() {  # hidden input, empty allowed
  local var="$1" q="$2" ans=""
  if [[ "$YES" == "1" ]]; then printf -v "$var" '%s' ""; return; fi
  printf '%s [empty]: ' "$q" >&2
  read -r -s ans || ans=""
  echo >&2
  printf -v "$var" '%s' "$ans"
}
rand() { openssl rand -hex "$1"; }
# Fernet key: 32 random bytes, urlsafe base64 (44 chars, '=' padded). openssl only.
fernet() { openssl rand -base64 32 | tr '+/' '-_'; }

if [[ -z "$TARGET" ]]; then
  ask TARGET "Target: ec2 (server) or local (laptop)" "ec2"
fi
case "$TARGET" in ec2|local) ;; *) echo "target must be ec2 or local" >&2; exit 2 ;; esac

if [[ -e "$OUT" && "${FORCE:-0}" != "1" ]]; then
  echo "$OUT exists; re-run with FORCE=1 to overwrite" >&2; exit 1
fi

if [[ "$TARGET" == "ec2" ]]; then
  DEF_BIND="0.0.0.0"; DEF_ENV="production"
else
  DEF_BIND="127.0.0.1"; DEF_ENV="development"
fi

echo "Writing $OUT for target '$TARGET'. Enter keeps the value in brackets."
ask AWS_KEY_ID       "AWS access key id (empty = instance IAM role, or no AWS)" ""
ask_secret AWS_SECRET "AWS secret access key (hidden)"
ask AWS_REGION       "AWS region" "eu-central-1"
ask S3_BUCKET        "S3 bucket for documents (empty = keep files on this machine)" ""
ask SHELL_PORT       "Port for the app (shell) on this host" "80"
ask SHELL_BIND       "Bind address for the app (0.0.0.0 = reachable from outside)" "$DEF_BIND"
ask MODELS_WHERE     "Models: local Ollama on this machine (local) or Amazon Bedrock Sonnet 5 + Opus 5 (bedrock)" "local"
case "$MODELS_WHERE" in b|bedrock|B|BEDROCK) MODELS_WHERE="bedrock" ;; *) MODELS_WHERE="local" ;; esac
if [[ "$MODELS_WHERE" == "bedrock" ]]; then
  ask BEDROCK_DRAFT    "Bedrock model for drafting (compile, edit, summarise)" "anthropic.claude-sonnet-5"
  ask BEDROCK_ANALYSIS "Bedrock model for analysis (entailment, Red-Hat, field extraction)" "anthropic.claude-opus-5"
fi
GPU_DEFAULT="n"; command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1 && GPU_DEFAULT="y"
ask GPU              "NVIDIA GPU on this machine? models run on it (y/n)" "$GPU_DEFAULT"
case "$GPU" in y|Y|yes|YES) GPU="y" ;; *) GPU="n" ;; esac
if [[ "$GPU" == "y" ]]; then DEF_MODEL_A="qwen2.5:7b"; DEF_MODEL_B="llama3.1:8b"; else DEF_MODEL_A="qwen2.5:1.5b"; DEF_MODEL_B="llama3.2:1b"; fi
ask MODEL_A          "Ollama model for drafting and checks" "$DEF_MODEL_A"
ask MODEL_B          "Ollama model for Compare's second column" "$DEF_MODEL_B"
ask TEXTRACT_CAP     "Textract monthly cap in USD (fallback OCR; 0 = never call Textract)" "100"

POSTGRES_PASSWORD="$(rand 24)"
PEM_SECRET_KEY="$(rand 32)"
ENCRYPTION_KEY="$(fernet)"
if [[ "$TARGET" == "ec2" ]]; then SHELL_ACCESS_KEY="$(rand 16)"; else SHELL_ACCESS_KEY="assure-local-shell-key"; fi

# NOTE: unquoted heredoc (variables must expand) — never put a backtick in the
# text below: `docker compose up` in a comment RAN docker compose up (2026-09-25,
# this is how "gen-env downloads ollama" happened). tests/test_gen_env.py guards it.
cat > "$OUT" <<ENV
# Generated by scripts/gen-env.sh $TARGET on $(date -u +%Y-%m-%dT%H:%MZ). Do not commit.
# Run:  docker compose up -d      → app http://<host>:${SHELL_PORT}/ (key: SHELL_ACCESS_KEY below), API 127.0.0.1:8765
# Change any value here and run docker compose up -d again.

$([[ "$GPU" == "y" ]] && printf '%s\n' "# GPU: the gpu overlay is part of every docker compose command on this machine" "COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml" || printf '%s' "# CPU only (no COMPOSE_FILE): models run on the CPU; expect minutes per draft")

# ---- the app (shell gate) -----------------------------------------------------
SHELL_BIND=${SHELL_BIND}
SHELL_PORT=${SHELL_PORT}
SHELL_ACCESS_KEY=${SHELL_ACCESS_KEY}
APP_BIND=127.0.0.1
PORT=8765

# ---- models --------------------------------------------------------------------
# ASSURE_LLM_BACKEND=ollama  → every model call on this machine's Ollama (models below;
#                              ollama-pull downloads them on the first docker compose up)
# ASSURE_LLM_BACKEND=bedrock → Amazon Bedrock with the AWS credentials/role above:
#                              drafting on ASSURE_BEDROCK_MODEL_DRAFT, analysis (entailment,
#                              Red-Hat, field extraction, locks) on ASSURE_BEDROCK_MODEL_ANALYSIS.
#                              Bare anthropic.* ids get the region's eu./us. inference-profile
#                              prefix automatically. Flip this line and run docker compose up -d.
ASSURE_LLM_BACKEND=$([[ "$MODELS_WHERE" == "bedrock" ]] && echo bedrock || echo ollama)
ASSURE_BEDROCK_MODEL_DRAFT=${BEDROCK_DRAFT:-anthropic.claude-sonnet-5}
ASSURE_BEDROCK_MODEL_ANALYSIS=${BEDROCK_ANALYSIS:-anthropic.claude-opus-5}
ASSURE_BEDROCK_MODEL_B=anthropic.claude-opus-5
ASSURE_OLLAMA_MODEL=${MODEL_A}
ASSURE_OLLAMA_MODEL_B=${MODEL_B}
OLLAMA_CONTEXT_LENGTH=8192

# ---- AWS (optional: documents in S3, Textract as OCR fallback) -----------------
ASSURE_S3_BUCKET=${S3_BUCKET}
ASSURE_S3_PREFIX=assure/
AWS_DEFAULT_REGION=${AWS_REGION}
AWS_ACCESS_KEY_ID=${AWS_KEY_ID}
AWS_SECRET_ACCESS_KEY=${AWS_SECRET}
ASSURE_TEXTRACT_MONTHLY_USD_CAP=${TEXTRACT_CAP}
ASSURE_TEXTRACT_MODE=detect

# ---- generated secrets ----------------------------------------------------------
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
PEM_SECRET_KEY=${PEM_SECRET_KEY}
ENCRYPTION_KEY=${ENCRYPTION_KEY}

# ---- runtime flags --------------------------------------------------------------
ENVIRONMENT=${DEF_ENV}
ASSURE_ENV=${DEF_ENV}
PROXY_FIX_HOPS=1
ASSURE_REQUIRE_LOGIN=false
ASSURE_CLERK_ONLY=0
WTF_CSRF_ENABLED=0
PARSE_ASYNC=1
SUBSTRATE_ASYNC_UPLOAD=1
PARSER_SCAN_BACKEND=jdf-ocr
JDF_OCR=tesseract
ASSURE_MAX_PAGES=200
GUNICORN_WORKERS=2
GUNICORN_THREADS=8
WORKER_CONCURRENCY=$([[ "$TARGET" == "ec2" ]] && echo 2 || echo 1)
ENV
chmod 600 "$OUT"

echo
echo "wrote $OUT ($TARGET)"
echo "  app:        http://$([[ "$SHELL_BIND" == "0.0.0.0" ]] && echo '<this-host>' || echo "$SHELL_BIND")$([[ "$SHELL_PORT" == "80" ]] || echo ":$SHELL_PORT")/"
echo "  access key: $SHELL_ACCESS_KEY"
echo "  next:       docker compose up -d"
