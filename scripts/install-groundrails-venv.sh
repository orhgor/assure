#!/usr/bin/env bash
# One-time: Python 3.12 venv with groundrails for subprocess verification.
# Run on EC2 host at /home/ubuntu/assure (venv lives beside docker-compose).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="${GROUNDRAILS_VENV:-$ROOT/.venv312}"
REQ="$ROOT/requirements-groundrails.txt"

if [[ ! -f "$REQ" ]]; then
  echo "ERROR: missing $REQ" >&2
  exit 1
fi

install_with_uv() {
  command -v uv >/dev/null 2>&1 || return 1
  uv venv "$VENV" --python 3.12
  uv pip install -r "$REQ" --python "$VENV/bin/python"
}

install_with_pip() {
  local py=""
  for candidate in python3.12 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      ver="$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
      if [[ "$ver" == "3.12" ]]; then
        py="$candidate"
        break
      fi
    fi
  done
  if [[ -z "$py" ]]; then
    echo "ERROR: Python 3.12 required for groundrails (install python3.12 or uv)." >&2
    return 1
  fi
  if ! "$py" -m venv /tmp/assure-venv-probe 2>/dev/null; then
    if command -v apt-get >/dev/null 2>&1; then
      echo "==> Installing python3.12-venv (ensurepip)"
      sudo apt-get update -qq
      sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3.12-venv
    fi
  fi
  rm -rf /tmp/assure-venv-probe
  rm -rf "$VENV"
  "$py" -m venv "$VENV"
  "$VENV/bin/python" -m ensurepip --upgrade 2>/dev/null || true
  "$VENV/bin/python" -m pip install --upgrade pip
  "$VENV/bin/python" -m pip install -r "$REQ"
}

echo "==> Groundrails venv → $VENV"
if install_with_uv; then
  echo "    installed via uv"
elif install_with_pip; then
  echo "    installed via pip + python3.12"
else
  exit 1
fi

"$VENV/bin/python" -c "import groundrails; print('groundrails', getattr(groundrails, '__version__', 'ok'))"
echo "Done. Enable with USE_GROUNDRAILS_SERVICE=1 and mount .venv312 into the app container."
