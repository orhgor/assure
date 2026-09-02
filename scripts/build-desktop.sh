#!/usr/bin/env bash
# Build a desktop binary with PyInstaller. Output: dist/Assure/ (and Assure.app on macOS).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [ ! -d prompt_matrix/.venv ]; then
  echo "Run ./scripts/install.sh first." >&2
  exit 1
fi
# shellcheck disable=SC1091
source prompt_matrix/.venv/bin/activate
python -m pip install -U pyinstaller
pyinstaller --noconfirm --clean "$ROOT/packaging/assure.spec"
echo
echo "Built. Double-click the app, then open http://127.0.0.1:8765"
echo "macOS: dist/Assure.app"
echo "Linux:  dist/Assure/Assure"
echo "Windows: use scripts/build-desktop.ps1"
echo "There is no public download URL yet. Do not publish this from the webpage GitHub repo."
