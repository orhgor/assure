#!/usr/bin/env bash
# Push a local Python file to the staging box over SSM and run it in the app venv.
# usage: scripts/aws/_boxpy.sh path/to/probe.py [args...]
# The file lands at /home/ubuntu/probes/<basename>; its stdout is returned.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SRC="${1:?python file required}"
shift || true
NAME="$(basename "$SRC")"
ARGS="$(printf '%q ' "$@")"

{
  printf 'mkdir -p /home/ubuntu/probes\n'
  printf 'cat > /home/ubuntu/probes/%s <<%s\n' "$NAME" "'PYEOF'"
  cat "$SRC"
  printf '\nPYEOF\n'
  printf 'cd /home/ubuntu/assure-prototype && PYTHONPATH=/home/ubuntu/assure-prototype .venv/bin/python /home/ubuntu/probes/_runner.py /home/ubuntu/probes/%s %s\n' "$NAME" "$ARGS"
} > /tmp/_boxpy_cmd.txt

exec "$ROOT/scripts/aws/_box.sh" "$(cat /tmp/_boxpy_cmd.txt)"
