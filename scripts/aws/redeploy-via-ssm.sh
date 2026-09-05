#!/usr/bin/env bash
# Redeploy assure-app on EC2 via SSM (run from your Mac or after GitHub Actions build).
#
# EC2 pulls a pre-built image from GHCR (built by .github/workflows/app-docker.yml).
# Does NOT build on EC2. Does NOT remove Docker volumes.
#
# Usage:
#   bash scripts/aws/redeploy-via-ssm.sh [instance-id]
#
# Env:
#   AWS_REGION, ASSURE_GIT_REF, ASSURE_INSTANCE_ID
#   GITHUB_TOKEN / GH_TOKEN / gh auth (git fetch + GHCR pull on EC2)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
exec bash "$ROOT/scripts/aws/ssm-redeploy-and-wait.sh" "$@"
