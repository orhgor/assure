"""Backend route smoke tests — status codes and response shape."""

from __future__ import annotations

import pytest
import requests

pytestmark = pytest.mark.quality_check


def test_health(quality_base_url: str):
    response = requests.get(f"{quality_base_url}/health", timeout=10)
    assert response.status_code == 200
    data = response.json()
    assert data.get("ok") is True
    assert data.get("status") == "healthy"
    assert "checks" in data
    if data.get("build_sha"):
        assert isinstance(data["build_sha"], str)
    ui = data.get("ui") or {}
    assert "js_version" in ui
    assert "css_version" in ui


def test_projects_list(quality_base_url: str):
    response = requests.get(f"{quality_base_url}/api/projects", timeout=10)
    assert response.status_code in (200, 401)


def test_founder_substrate(quality_base_url: str, founder_project_id: str):
    response = requests.get(
        f"{quality_base_url}/api/projects/{founder_project_id}/substrate",
        timeout=10,
    )
    assert response.status_code in (200, 401)
    if response.status_code == 200:
        payload = response.json()
        assert payload.get("ok") is True


def test_founder_history(quality_base_url: str, founder_project_id: str):
    response = requests.get(
        f"{quality_base_url}/api/projects/{founder_project_id}/history",
        timeout=10,
    )
    assert response.status_code in (200, 401)
    if response.status_code == 200:
        assert "history" in response.json()


def test_runs_list(quality_base_url: str):
    response = requests.get(f"{quality_base_url}/api/runs?workspace=founder", timeout=10)
    assert response.status_code in (200, 401)


def test_draft_get(quality_base_url: str):
    response = requests.get(f"{quality_base_url}/api/drafts?workspace_id=founder", timeout=10)
    assert response.status_code == 200
    data = response.json()
    assert data.get("ok") is True
    assert "draft" in data


def test_jdf_get(quality_base_url: str, founder_project_id: str):
    response = requests.get(
        f"{quality_base_url}/api/projects/{founder_project_id}/jdf",
        timeout=10,
    )
    assert response.status_code in (200, 404)


def test_redhat_status_route(quality_base_url: str, founder_project_id: str):
    response = requests.get(
        f"{quality_base_url}/api/projects/{founder_project_id}/redhat/status",
        timeout=10,
    )
    assert response.status_code in (200, 401, 404)


def test_lock_evidence_route_shape(quality_base_url: str):
    response = requests.get(f"{quality_base_url}/api/locks/nonexistent/evidence", timeout=10)
    assert response.status_code in (404, 401, 200)
