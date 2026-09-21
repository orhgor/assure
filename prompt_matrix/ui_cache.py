"""Cache-bust query strings. Change these, not scattered HTML literals."""

from __future__ import annotations

import os


def _app_prefix() -> str:
    edition = (os.getenv("ASSURE_EDITION") or os.getenv("PEM_EDITION") or "").strip().lower()
    if edition in {"founder_workbench", "founder-workbench", "workbench"}:
        return "founder_workbench"
    return "assure"


APP_CSS = f"{_app_prefix()}-98"
APP_JS = f"{_app_prefix()}-98"
LANDING_CSS = "54"
LANDING_JS = "41"
