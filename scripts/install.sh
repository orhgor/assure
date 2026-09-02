#!/usr/bin/env bash
# Install Assure from the product source (this repo). Not the webpage GitHub repo.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

pick_python() {
  local cmd ver major minor
  for cmd in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$cmd" >/dev/null 2>&1; then
      ver="$("$cmd" -c 'import sys; print("%d.%d" % (sys.version_info[0], sys.version_info[1]))')"
      major="${ver%%.*}"
      minor="${ver#*.}"
      if [ "$major" -gt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -ge 10 ]; }; then
        echo "$cmd"
        return 0
      fi
    fi
  done
  echo "Need Python 3.10 or newer. On many Macs python3 is still 3.9. Install 3.13 and retry." >&2
  exit 1
}

PY="$(pick_python)"
VENV="$ROOT/prompt_matrix/.venv"
if [ ! -d "$VENV" ]; then
  echo "Creating venv with $PY"
  "$PY" -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
python -m pip install -U pip
pip install -e "$ROOT"

echo
echo "Installed. Run:"
echo "  source prompt_matrix/.venv/bin/activate"
echo "  assure --web"
echo
echo "Your browser should open. First run: paste a provider key, then write a question."
echo "If the browser does not open, go to http://127.0.0.1:8765"
echo "pip install prompt-matrix is not on PyPI yet."
echo "github.com/orhgor/assure is the public site only. Do not clone it for the app."
