"""Shared pytest configuration for unit and integration tests."""

from __future__ import annotations

import os

import pytest


def pytest_configure(config):
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
