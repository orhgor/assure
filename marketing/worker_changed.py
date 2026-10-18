#!/usr/bin/env python3
"""Check or record marketing Worker source hash (skip redundant deploys)."""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CF = ROOT / "scripts" / "cloudflare"
WORKER_FILES = (
    CF / "marketing-r2-worker.js",
    CF / "wrangler-marketing-proxy.jsonc",
)
STATE = ROOT / ".cache" / "marketing-worker-manifest.json"


def combined_hash() -> str:
    h = hashlib.sha256()
    for path in WORKER_FILES:
        h.update(path.name.encode())
        h.update(path.read_bytes())
    return h.hexdigest()


def load_hash() -> str:
    if not STATE.is_file():
        return ""
    try:
        return json.loads(STATE.read_text(encoding="utf-8")).get("hash", "")
    except (json.JSONDecodeError, OSError):
        return ""


def save_hash(digest: str) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(
        json.dumps({"hash": digest, "updated_at": datetime.now(UTC).isoformat()}, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    if "--record" in sys.argv:
        save_hash(combined_hash())
        return 0

    current = combined_hash()
    if current != load_hash():
        print("worker-changed")
        return 0
    print("worker-unchanged")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
