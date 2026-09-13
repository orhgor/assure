#!/usr/bin/env bash
# Lean code sync + restart over SSM (use when SSH port 22 is closed).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
INSTANCE_ID="${ASSURE_INSTANCE_ID:-i-03e39eccc57572191}"
REGION="${AWS_REGION:-us-east-1}"
SERVICE_NAME="${ASSURE_SERVICE_NAME:-assure}"

# Reuse sync batches from bootstrap (source the helper by running bootstrap in deploy-only mode)
ASSURE_LEAN_DEPLOY_ONLY=1 exec bash "$ROOT/scripts/aws/ssm-lean-bootstrap.sh"
