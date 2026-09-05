"""Write canonical JDF trees to disk on every PUT / revision save."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

try:
    from ..paths import user_data_dir
except ImportError:
    from paths import user_data_dir


def jdf_projects_root() -> Path:
    override = (os.environ.get("ASSURE_JDF_DIR") or "").strip()
    if override:
        root = Path(override)
    else:
        root = user_data_dir() / "projects"
    root.mkdir(parents=True, exist_ok=True)
    return root


def jdf_disk_path(project_id: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in (project_id or "default"))
    safe = safe.strip("-") or "default"
    project_dir = jdf_projects_root() / safe
    project_dir.mkdir(parents=True, exist_ok=True)
    return project_dir / "document.jdf"


def write_jdf_disk(project_id: str, tree: dict[str, Any]) -> Path:
    path = jdf_disk_path(project_id)
    path.write_text(json.dumps(tree, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def read_jdf_disk(project_id: str) -> dict[str, Any] | None:
    path = jdf_disk_path(project_id)
    if not path.is_file():
        return None
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None
