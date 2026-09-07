"""Shared pytest configuration for unit and integration tests."""

from __future__ import annotations

import os

import pytest

# Blank before dotenv (override=False). Live Resend must never run under pytest.
os.environ["RESEND_API_KEY"] = ""


def pytest_configure(config):
    os.environ["RESEND_API_KEY"] = ""
    os.environ["SQLITE_USE_POOL"] = "0"
    os.environ.setdefault("BRAVE_API_KEY", "")
    config.addinivalue_line(
        "markers",
        "playwright: browser-driven Assure workbench simulation (requires pytest-playwright)",
    )
    if os.environ.get("CI"):
        try:
            import z3

            z3.set_param("parallel.enable", False)
        except Exception:
            pass


@pytest.fixture(autouse=True)
def _block_live_resend(monkeypatch):
    """Strip Resend credentials so pytest never sends live mail."""
    monkeypatch.setenv("RESEND_API_KEY", "")
    monkeypatch.setenv("SQLITE_USE_POOL", "0")
