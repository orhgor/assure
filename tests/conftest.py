"""Shared pytest configuration for unit and integration tests."""

from __future__ import annotations

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "playwright: browser-driven Assure workbench simulation (requires pytest-playwright)",
    )
