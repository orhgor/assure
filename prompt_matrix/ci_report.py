"""JSON payload for CI (--ci). No extra model call."""

from __future__ import annotations

import json
import sys
from typing import Any


def from_pipeline(result: Any, *, lint: dict | None = None, redteam: dict | None = None) -> dict:
    quality = getattr(result, "quality", None)
    return {
        "ok": not (lint or {}).get("errors") and (redteam or {}).get("ok", True),
        "target": getattr(result, "target_ai", None),
        "intent": getattr(result, "intent", None),
        "workflow": getattr(result, "workflow", None),
        "prompt": getattr(result, "prompt", None),
        "reply": getattr(result, "reply", None),
        "note": getattr(result, "note", None),
        "quality": quality,
        "tokens": getattr(result, "tokens", None)
        or {
            "input": getattr(result, "input_tokens", 0),
            "output": getattr(result, "output_tokens", 0),
            "total": getattr(result, "total_tokens", 0),
        },
        "estimated_cost": getattr(result, "estimated_cost", None),
        "run_hash": getattr(result, "run_hash", None),
        "lint": lint,
        "redteam": redteam,
        "direct": bool(getattr(result, "direct", False)),
    }


def from_execution(result: Any, *, lint: dict | None = None, redteam: dict | None = None) -> dict:
    rendered = getattr(result, "rendered", None)
    return {
        "ok": not (lint or {}).get("errors") and (redteam or {}).get("ok", True),
        "target": getattr(rendered, "target_ai", None) if rendered else None,
        "intent": getattr(rendered, "intent", None) if rendered else None,
        "workflow": "single",
        "prompt": getattr(rendered, "prompt", None) if rendered else None,
        "reply": getattr(result, "reply", None),
        "note": getattr(result, "note", None),
        "quality": None,
        "tokens": None,
        "estimated_cost": None,
        "run_hash": None,
        "lint": lint,
        "redteam": redteam,
        "direct": bool(getattr(result, "direct", False)),
    }


def emit(payload: dict, *, stream=None) -> None:
    (stream or sys.stdout).write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def failed(payload: dict) -> bool:
    lint = payload.get("lint") or {}
    redteam = payload.get("redteam") or {}
    if lint.get("errors"):
        return True
    if redteam and redteam.get("ok") is False:
        return True
    return False
