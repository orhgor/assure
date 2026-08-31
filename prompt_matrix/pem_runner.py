"""Standalone PEM process boot. Announces no live search before any model call.

PEM has no built-in product or domain. The case is the uploaded file and the task.
"""

from __future__ import annotations

import sys
from typing import Any, TextIO

TOOL_AVAILABILITY: dict[str, bool] = {
    "web_search": False,
    "browser": False,
    "file_system_access": True,
}

_PREFLIGHT: dict[str, Any] | None = None


def preflight_check(stream: TextIO | None = None) -> dict[str, Any]:
    """Force standalone PEM to admit it has no live search before the first system message."""
    out = stream if stream is not None else sys.stderr
    print("PEM Standalone Mode: LIVE SEARCH = DISABLED", file=out, flush=True)
    print("Injecting hallucination firewall into LLM system prompt...", file=out, flush=True)
    return {
        "tool_availability": {
            "web_search": False,
            "browser": False,
            "file_system_access": True,
        }
    }


def ensure_preflight(*, announce: bool = True) -> dict[str, Any]:
    global _PREFLIGHT
    if _PREFLIGHT is None:
        _PREFLIGHT = preflight_check(stream=sys.stderr) if announce else {
            "tool_availability": dict(TOOL_AVAILABILITY)
        }
    return _PREFLIGHT


def tool_availability_lines() -> str:
    tools = ensure_preflight(announce=False)["tool_availability"]
    return (
        "- tool_availability: "
        f"web_search={str(tools['web_search']).lower()}, "
        f"browser={str(tools['browser']).lower()}, "
        f"file_system_access={str(tools['file_system_access']).lower()} "
        "(uploaded file only).\n"
        "- First-message acknowledgment: you have no live web search and no browser in this process. "
        "If the user asks for recent Google results or current market data, your first sentence must be: "
        "Live search unavailable in standalone PEM."
    )
