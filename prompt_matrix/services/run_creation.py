"""Synchronous evidence run creation — delegates to Auto-Compiler pipeline."""

from __future__ import annotations

from typing import Any

try:
    from ..services.auto_compiler import create_run_from_directive as _auto_create_run
except ImportError:
    from services.auto_compiler import create_run_from_directive as _auto_create_run


def create_run_from_directive(
    directive: str,
    *,
    workspace_id: str | None = None,
    source_ids: list[str] | None = None,
    model: str = "gemini",
) -> dict[str, Any]:
    """Parse directive via Auto-Compiler and persist run."""
    return _auto_create_run(
        directive,
        workspace_id=workspace_id,
        source_ids=source_ids,
        model=model,
    )
