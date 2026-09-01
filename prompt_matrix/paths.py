"""Package vs user-data paths. PyInstaller sets sys.frozen and sys._MEIPASS."""

from __future__ import annotations

import sys
from pathlib import Path


def resource_dir() -> Path:
    """Read-only files: templates, static, config.json."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        bundled = Path(sys._MEIPASS) / "prompt_matrix"
        if bundled.is_dir():
            return bundled
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def user_data_dir() -> Path:
    """Writable .env, history, and library. ~/.assure when frozen."""
    if getattr(sys, "frozen", False):
        home = Path.home() / ".assure"
        home.mkdir(parents=True, exist_ok=True)
        return home
    return Path(__file__).resolve().parent
