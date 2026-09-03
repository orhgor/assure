#!/bin/bash
set -euo pipefail
cd /home/ubuntu/assure
export PYTHONPATH="/home/ubuntu/assure:${PYTHONPATH:-}"
PYTHON="$(command -v python3 || command -v python)"
"$PYTHON" -c "from prompt_matrix.lib.telemetry import prune_old_logs; prune_old_logs('/home/ubuntu/assure/data/history.sqlite')"
