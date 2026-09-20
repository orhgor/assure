#!/usr/bin/env bash
# Copy a local file to the staging box over SSM.
# usage: scripts/aws/_boxpush.sh <local-path> <remote-absolute-path>
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
LOCAL="${1:?local path required}"
REMOTE="${2:?remote path required}"
{
  printf 'mkdir -p "$(dirname %s)"\n' "'$REMOTE'"
  printf 'cat > %s <<%s\n' "'$REMOTE'" "'BOXEOF'"
  cat "$LOCAL"
  printf '\nBOXEOF\n'
  printf 'wc -c %s\n' "'$REMOTE'"
} > /tmp/_boxpush_cmd.txt
exec "$ROOT/scripts/aws/_box.sh" "$(cat /tmp/_boxpush_cmd.txt)"
