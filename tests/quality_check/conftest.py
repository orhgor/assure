"""Pytest fixtures for tests/quality_check — shared base URL and Flask server."""

from __future__ import annotations

import os

import pytest

from tests.e2e.conftest import base_url, live_assure_server  # noqa: F401

pytestmark = pytest.mark.quality_check


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "quality_check: unified pre-promotion quality gate (API, backend, UI, design, visual)",
    )


@pytest.fixture(scope="session")
def quality_base_url(live_assure_server) -> str:
    """HTTP root for requests-based smoke tests."""
    return live_assure_server.rstrip("/")


@pytest.fixture(scope="session")
def founder_project_id(quality_base_url: str) -> str:
    """Project id used by founder workbench smoke tests."""
    return os.environ.get("QUALITY_CHECK_PROJECT_ID", "founder")
