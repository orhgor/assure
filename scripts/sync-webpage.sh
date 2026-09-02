#!/usr/bin/env bash
# Copy landing/ onto a webpage-branch checkout (site at repo root). Does not push.
# Usage: ./scripts/sync-webpage.sh /path/to/webpage-checkout
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${1:-}"

if [ -z "$DEST" ]; then
  echo "Usage: ./scripts/sync-webpage.sh /path/to/webpage-checkout" >&2
  echo "That checkout is the GitHub webpage branch. Site files live at the repo root." >&2
  echo "Do not copy the workbench onto that remote. Do not push main." >&2
  exit 1
fi

if [ ! -d "$DEST" ]; then
  echo "Destination does not exist: $DEST" >&2
  exit 1
fi

rsync -a \
  --exclude '.wrangler' \
  --exclude '.git' \
  --exclude '.DS_Store' \
  "$ROOT/landing/" "$DEST/"
echo "Copied landing/ to $DEST"
echo "Review, then commit and push on the webpage branch only."
