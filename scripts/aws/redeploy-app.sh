#!/usr/bin/env bash
# Safe production redeploy: sync git, pull GHCR image, restart (no on-box build).
# Does NOT remove volumes (data/history.sqlite stays intact).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.prod.yml)
COMPOSE_GHCR=(docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.ghcr.yml)
COMPOSE_FILES=(-f docker-compose.yml -f docker-compose.prod.yml)
BRANCH="${ASSURE_DEPLOY_BRANCH:-p4-account-wallet}"
IMAGE_REPO="${ASSURE_IMAGE_REPO:-ghcr.io/orhgor/assure-app}"
LOCK_FILE="${ASSURE_REDEPLOY_LOCK:-/tmp/assure-redeploy.lock}"
MIN_DISK_GB_FOR_BUILD="${ASSURE_MIN_DISK_GB_FOR_BUILD:-3}"
GHCR_PULL_RETRIES="${ASSURE_GHCR_PULL_RETRIES:-5}"
GHCR_PULL_WAIT_SEC="${ASSURE_GHCR_PULL_WAIT_SEC:-30}"
STATE_FILE="${ASSURE_DEPLOY_STATE:-/tmp/assure-last-deploy.txt}"
HEALTH_URL="${ASSURE_HEALTH_URL:-http://127.0.0.1:8765/health}"
DEPLOY_LOG="${ASSURE_DEPLOY_LOG:-/var/log/assure-deploy.log}"
SSM_BACKGROUND="${ASSURE_SSM_BACKGROUND:-0}"
DEPLOY_TAG=""

usage() {
  echo "Usage: $0 [--tag TAG]" >&2
  echo "  --tag TAG   Pull ghcr.io/orhgor/assure-app:TAG instead of commit SHA (e.g. staging)" >&2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tag)
      DEPLOY_TAG="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ "${ASSURE_ENVIRONMENT:-}" == "staging" ]]; then
  COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.staging.yml)
  COMPOSE_GHCR=(docker compose -f docker-compose.yml -f docker-compose.staging.yml -f docker-compose.ghcr.yml)
  COMPOSE_FILES=(-f docker-compose.yml -f docker-compose.staging.yml)
  BRANCH="${ASSURE_DEPLOY_BRANCH:-staging}"
fi

remove_stale_app_container() {
  echo "==> Remove stale assure-app containers (keep volumes)"
  "${COMPOSE[@]}" rm -f -s assure-app 2>/dev/null || true
  "${COMPOSE_GHCR[@]}" rm -f -s assure-app 2>/dev/null || true
  # Orphaned compose-prefixed names (e.g. 1e307ee48e8f_assure-assure-app-1) block recreate.
  while IFS= read -r cid; do
    [[ -n "$cid" ]] || continue
    docker rm -f "$cid" 2>/dev/null || true
  done < <(docker ps -aq --filter "name=assure-assure-app" 2>/dev/null || true)
}

image_is_cached() {
  docker image inspect "$1" >/dev/null 2>&1
}

# Only skip pull for immutable 40-char SHA tags. Moving tags like "staging" must always pull.
image_tag_is_immutable_sha() {
  local tag="${1##*:}"
  [[ "$tag" =~ ^[0-9a-f]{40}$ ]]
}

valid_image_ref() {
  local ref="${1:-}"
  [[ -n "$ref" && "$ref" == "${IMAGE_REPO}:"* ]] || return 1
  local tag="${ref##*:}"
  [[ -n "$tag" && "$tag" != "assure-assure-app" && "$tag" != "unknown" ]] || return 1
}

running_app_image() {
  local img
  img="$(docker inspect --format '{{.Config.Image}}' assure-assure-app-1 2>/dev/null || true)"
  if valid_image_ref "$img"; then
    printf '%s' "$img"
  fi
}

pull_image_with_retry() {
  local image="$1"
  local attempt=1
  local wait_sec="$GHCR_PULL_WAIT_SEC"
  if image_tag_is_immutable_sha "$image" && image_is_cached "$image"; then
    echo "    cached: ${image} (skip pull)"
    return 0
  fi
  while [[ "$attempt" -le "$GHCR_PULL_RETRIES" ]]; do
    if docker pull "$image"; then
      return 0
    fi
    if docker compose version >/dev/null 2>&1 \
      && docker compose pull --help 2>/dev/null | grep -q -- '--parallel' \
      && ASSURE_IMAGE_TAG="$IMAGE_TAG" "${COMPOSE_GHCR[@]}" pull --parallel assure-app; then
      return 0
    fi
    if [[ "$attempt" -ge "$GHCR_PULL_RETRIES" ]]; then
      return 1
    fi
    echo "WARN: GHCR pull attempt ${attempt}/${GHCR_PULL_RETRIES} failed; retry in ${wait_sec}s..."
    sleep "$wait_sec"
    wait_sec=$((wait_sec * 2))
    attempt=$((attempt + 1))
  done
  return 1
}

write_deploy_state() {
  local previous="$1"
  local current="$2"
  # A stale /tmp state file owned by another user (e.g. a root SSM run) must
  # not fail the whole deploy — state bookkeeping is best-effort. The write
  # runs inside a subshell on purpose: a failed redirection on a compound
  # command can exit the parent script under `set -e`, but a subshell's
  # failure is catchable with `if !`. On failure, fall back to a fresh
  # mktemp file and re-point STATE_FILE (rollback reads it via
  # ASSURE_DEPLOY_STATE); if even that is impossible, warn and move on.
  if (
    { echo "PREVIOUS=${previous}"; echo "CURRENT=${current}"; }
  ) > "$STATE_FILE" 2>/dev/null; then
    echo "    deploy state → ${STATE_FILE}"
    return 0
  fi
  echo "WARN: cannot write ${STATE_FILE} (permission denied); using a temp state file" >&2
  local fallback
  fallback="$(mktemp "${STATE_FILE%.txt}.XXXXXX.txt" 2>/dev/null)" || fallback=""
  if [[ -z "$fallback" ]]; then
    echo "WARN: no writable state file — rollback bookkeeping will be unavailable" >&2
    return 0
  fi
  STATE_FILE="$fallback"
  {
    echo "PREVIOUS=${previous}"
    echo "CURRENT=${current}"
  } > "$STATE_FILE"
  echo "    deploy state → ${STATE_FILE} (fallback)"
}

rollback_on_failure() {
  if [[ "${ASSURE_SKIP_ROLLBACK:-}" == "1" ]]; then
    echo "ASSURE_SKIP_ROLLBACK=1 — not rolling back." >&2
    return 1
  fi
  if [[ ! -f "$ROOT/scripts/aws/rollback.sh" ]]; then
    echo "ERROR: rollback.sh missing" >&2
    return 1
  fi
  echo "Health check failed — rolling back..."
  ASSURE_SKIP_ROLLBACK=1 ASSURE_ENVIRONMENT="${ASSURE_ENVIRONMENT:-}" \
    ASSURE_DEPLOY_STATE="$STATE_FILE" ASSURE_HEALTH_URL="$HEALTH_URL" \
    bash "$ROOT/scripts/aws/rollback.sh"
}

disk_free_gb() {
  df -BG / 2>/dev/null | awk 'NR==2 {gsub(/G/, "", $4); print $4}'
}

acquire_redeploy_lock() {
  # Create lock file as ubuntu user to avoid permission issues
  sudo -u ubuntu touch "$LOCK_FILE"
  sudo -u ubuntu chmod 644 "$LOCK_FILE"
  exec 9>"$LOCK_FILE"
  if ! flock -n 9; then
    echo "ERROR: another redeploy holds ${LOCK_FILE} — aborting." >&2
    exit 1
  fi
}
if [[ ! -d /home/ubuntu/assure/.git ]]; then
  echo "    Initial clone..."
  sudo -u ubuntu git clone "https://github.com/orhgor/assure.git" /home/ubuntu/assure
fi
sudo -u ubuntu bash -c "
  cd /home/ubuntu/assure
  git config remote.origin.fetch '+refs/heads/*:refs/remotes/origin/*'
  git fetch origin \"${BRANCH}\"
  git checkout -B \"${BRANCH}\" \"origin/${BRANCH}\"
  FULL_SHA=\"\$(git rev-parse HEAD)\"
  SHORT_SHA=\"\$(git rev-parse --short HEAD)\"
  echo \"    HEAD: \${SHORT_SHA} \$(git log -1 --oneline)\"
  echo \"FULL_SHA=\${FULL_SHA}\" > /tmp/assure-git-sha
  echo \"SHORT_SHA=\${SHORT_SHA}\" >> /tmp/assure-git-sha
"
source /tmp/assure-git-sha
IMAGE_TAG="${DEPLOY_TAG:-${ASSURE_IMAGE_TAG:-${FULL_SHA}}}"
export ASSURE_BUILD_SHA="${SHORT_SHA}"
export ASSURE_IMAGE_TAG="${IMAGE_TAG}"
export ASSURE_IMAGE="${IMAGE_REPO}:${IMAGE_TAG}"
# docker-compose.yml interpolates ${APP_IMAGE:?} for the assure-app service;
# compose runs on the EC2 box whose .env may not define it, so the deploy
# script always provides it — same image the GHCR path just pulled.
export APP_IMAGE="${ASSURE_IMAGE}"
PREVIOUS_IMAGE="$(running_app_image)"
if [[ -z "$PREVIOUS_IMAGE" && -f "$STATE_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$STATE_FILE"
  PREVIOUS_IMAGE="${CURRENT:-}"
fi
if [[ -n "$PREVIOUS_IMAGE" ]] && ! valid_image_ref "$PREVIOUS_IMAGE"; then
  echo "WARN: ignoring invalid PREVIOUS image ${PREVIOUS_IMAGE}" >&2
  PREVIOUS_IMAGE=""
fi
# Preflight: docker-compose.yml needs a runtime env file for assure-app.
# Production carries .env; staging carries .env.staging (the overlay's
# env_file, optional in compose). Fail only when NEITHER exists — that
# means compose would interpolate an empty runtime environment.
if [[ ! -f "$ROOT/.env" && ! -f "$ROOT/.env.staging" ]]; then
  echo "ERROR: no runtime env file on this instance — need $ROOT/.env (production)" >&2
  echo "or $ROOT/.env.staging (staging). docker-compose.yml mounts it as the" >&2
  echo "assure-app env_file (runtime keys: API keys, session keys, etc.)." >&2
  echo "Restore it from backup or re-create it, then re-run this deploy." >&2
  echo "The running container was not touched." >&2
  exit 1
fi
if [[ ! -f "$ROOT/.env" ]]; then
  echo "NOTE: $ROOT/.env absent (staging) — runtime env comes from $ROOT/.env.staging" >&2
fi
write_deploy_state "${PREVIOUS_IMAGE:-unknown}" "$ASSURE_IMAGE"

echo "==> GHCR login"
# shellcheck disable=SC1091
source "$ROOT/scripts/aws/ghcr-login.sh"

echo "==> Stop assure-app (keep volumes)"
"${COMPOSE[@]}" stop assure-app || true
"${COMPOSE_GHCR[@]}" stop assure-app || true
remove_stale_app_container

start_assure_app() {
  local mode="$1"
  if [[ "$SSM_BACKGROUND" == "1" ]]; then
    echo "==> Start assure-app in background (SSM mode, log → ${DEPLOY_LOG})"
    sudo mkdir -p "$(dirname "$DEPLOY_LOG")" 2>/dev/null || true
    sudo touch "$DEPLOY_LOG" 2>/dev/null || true
    sudo chmod 666 "$DEPLOY_LOG" 2>/dev/null || true
    {
      echo "=== $(date -Is) redeploy start ${ASSURE_IMAGE} (${mode}) ==="
      if [[ "$mode" == "ghcr" ]]; then
        "${COMPOSE_GHCR[@]}" up -d --no-build --pull never --force-recreate assure-app
      else
        "${COMPOSE[@]}" up -d --force-recreate assure-app
      fi
      echo "=== $(date -Is) compose up finished ==="
    } >>"$DEPLOY_LOG" 2>&1 &
    disown 2>/dev/null || true
    echo "    ASSURE_DEPLOY_BACKGROUND=1"
    return 0
  fi
  if [[ "$mode" == "ghcr" ]]; then
    echo "==> Start assure-app (pull-only, no build)"
    "${COMPOSE_GHCR[@]}" up -d --no-build --pull never --force-recreate assure-app
  else
    echo "==> Start assure-app (EC2 build)"
    "${COMPOSE[@]}" up -d --force-recreate assure-app
  fi
}

echo "==> Pull ${ASSURE_IMAGE}"
if pull_image_with_retry "$ASSURE_IMAGE"; then
  start_assure_app ghcr
else
  echo "WARN: GHCR pull failed after ${GHCR_PULL_RETRIES} attempts (add GHCR_TOKEN with read:packages to .env.production)."
  if [[ "${ASSURE_DEPLOY_PULL_ONLY:-}" == "1" ]]; then
    echo "ASSURE_DEPLOY_PULL_ONLY=1 — aborting." >&2
    exit 1
  fi
  free_gb="$(disk_free_gb || echo 0)"
  if [[ "${free_gb:-0}" -lt "$MIN_DISK_GB_FOR_BUILD" ]]; then
    echo "ERROR: only ${free_gb}GB free on / — need >= ${MIN_DISK_GB_FOR_BUILD}GB for EC2 fallback build." >&2
    exit 1
  fi
  if [[ "$SSM_BACKGROUND" == "1" ]]; then
    echo "ERROR: GHCR pull failed and SSM background mode forbids on-box build." >&2
    exit 1
  fi
  echo "==> Fallback: build on EC2 (slow, ARM64; ${free_gb}GB free)"
  export DOCKER_DEFAULT_PLATFORM=linux/arm64
  remove_stale_app_container
  DOCKER_BUILDKIT=1 "${COMPOSE[@]}" build --build-arg "ASSURE_BUILD_SHA=${FULL_SHA}" assure-app
  start_assure_app local
fi

if [[ "$SSM_BACKGROUND" == "1" ]]; then
  echo "==> SSM background mode — skipping inline health/exec checks"
  write_deploy_state "${PREVIOUS_IMAGE:-unknown}" "$ASSURE_IMAGE"
  echo ""
  echo "Done (background). Image: ${ASSURE_IMAGE}"
  echo "Tail: ${DEPLOY_LOG}"
  exit 0
fi

echo "==> Wait for health"
for i in $(seq 1 60); do
  if curl -sf "$HEALTH_URL" >/tmp/assure-health.json 2>/dev/null \
    && [[ -s /tmp/assure-health.json ]]; then
    break
  fi
  sleep 2
done

echo "==> Health / UI manifest"
if [[ ! -s /tmp/assure-health.json ]]; then
  echo "ERROR: health check failed — container did not respond on ${HEALTH_URL}" >&2
  rollback_on_failure || true
  exit 1
fi
python3 -c "
import json, sys
data = json.load(open('/tmp/assure-health.json'))
ui = data.get('ui') or {}
print('status:', data.get('status'))
print('build_sha:', data.get('build_sha', '(not set)'))
print('css_version:', ui.get('css_version'))
print('js_version:', ui.get('js_version'))
print('jdf_workbench:', ui.get('jdf_workbench'))
if not data.get('ok'):
    print('health ok=false', file=sys.stderr)
    sys.exit(1)
" || {
  echo "ERROR: health JSON invalid or ok=false" >&2
  rollback_on_failure || true
  exit 1
}

echo "==> Template check (workspace-shell in container)"
if "${COMPOSE_GHCR[@]}" exec -T assure-app grep -q 'workspace-shell' /app/prompt_matrix/templates/index.html 2>/dev/null \
  || "${COMPOSE[@]}" exec -T assure-app grep -q 'workspace-shell' /app/prompt_matrix/templates/index.html 2>/dev/null; then
  echo "    OK: JDF workspace-shell present in index.html"
else
  echo "    FAIL: workspace-shell missing — wrong image or old checkout"
  rollback_on_failure || true
  exit 1
fi

write_deploy_state "${PREVIOUS_IMAGE:-unknown}" "$ASSURE_IMAGE"

echo ""
echo "Done. Image: ${ASSURE_IMAGE}"
