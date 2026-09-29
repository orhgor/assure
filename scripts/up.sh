#!/usr/bin/env bash
# docker compose up -d --build with the checkout's commit and branch passed as
# build args, so every Parsure report's ``execution.build`` names the build it
# came from (plan V5 review protocol V1, 2026-09-29). Compose cannot run git
# itself and ``.git`` is not in the image, so the values travel this way.
#   ./scripts/up.sh            # same as docker compose up -d --build
#   ./scripts/up.sh assure-app # any extra args go to compose
set -euo pipefail
cd "$(dirname "$0")/.."
export BUILD_SHA="${BUILD_SHA:-$(git rev-parse --short=12 HEAD 2>/dev/null || echo local)}"
export BUILD_BRANCH="${BUILD_BRANCH:-$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)}"
echo "build: $BUILD_SHA ($BUILD_BRANCH)"
exec docker compose up -d --build "$@"
