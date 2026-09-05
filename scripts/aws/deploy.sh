#!/usr/bin/env bash
# End-to-end: Cloudflare tunnel + EC2 t4g.small provision.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
bash "$ROOT/scripts/aws/setup-tunnel.sh"
bash "$ROOT/scripts/aws/provision-ec2.sh"
