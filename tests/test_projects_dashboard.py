"""Active Works dashboard — projects cards, API summaries, i18n."""

from __future__ import annotations

import json
from pathlib import Path

from prompt_matrix.i18n import CATALOGS, LOCALES
from prompt_matrix.routers.project_routes import (
    _count_annotations,
    _project_status,
)

ROOT = Path(__file__).resolve().parents[1]

DASHBOARD_KEYS = (
    "projects.dashboard.title",
    "projects.dashboard.lead",
    "projects.status.drafting",
    "projects.status.verifying",
    "projects.status.audited",
    "projects.status.ready",
    "projects.vitals.locks",
    "projects.vitals.redhat",
    "projects.vitals.edited",
    "projects.vitals.nodes",
    "projects.action.compile",
    "projects.action.verify",
    "projects.action.review",
    "projects.action.export",
    "projects.default_workspace",
    "projects.active_badge",
)


def test_projects_dashboard_i18n() -> None:
    for locale in LOCALES:
        cat = CATALOGS[locale]
        for key in DASHBOARD_KEYS:
            assert key in cat, f"missing {locale} {key}"
            assert str(cat[key]).strip(), f"empty {locale} {key}"


def test_projects_dashboard_markup_and_js() -> None:
    html = (ROOT / "prompt_matrix" / "templates" / "index.html").read_text(encoding="utf-8")
    assert 'id="projects-dashboard"' in html
    assert 'id="view-projects"' in html
    assert 'data-i18n="projects.dashboard.title"' in html
    assert 'class="project-work-card"' not in html  # rendered client-side
    js = (ROOT / "prompt_matrix" / "static" / "projects.js").read_text(encoding="utf-8")
    assert "renderDashboard" in js
    assert "project-work-card" in js
    assert "statusMeta" in js
    assert "runSuggestedAction" in js
    assert "displayTitle" in js
    css = (ROOT / "prompt_matrix" / "static" / "style.css").read_text(encoding="utf-8")
    assert ".project-work-card" in css
    assert ".project-status-pill" in css


def test_project_status_helpers() -> None:
    assert (
        _project_status(
            node_count=0,
            lock_count=0,
            redhat_count=0,
            z3_violations=0,
            current_version=1,
        )
        == "drafting"
    )
    assert (
        _project_status(
            node_count=3,
            lock_count=1,
            redhat_count=0,
            z3_violations=1,
            current_version=2,
        )
        == "verifying"
    )
    assert (
        _project_status(
            node_count=3,
            lock_count=1,
            redhat_count=2,
            z3_violations=0,
            current_version=2,
        )
        == "audited"
    )
    assert (
        _project_status(
            node_count=3,
            lock_count=1,
            redhat_count=0,
            z3_violations=0,
            current_version=2,
        )
        == "ready_to_export"
    )


def test_count_annotations_from_tree() -> None:
    tree = {
        "document_id": "doc-1",
        "body": [
            {
                "type": "section",
                "id": "s1",
                "title": "A",
                "children": [
                    {
                        "type": "paragraph",
                        "id": "p1",
                        "content": "x",
                        "annotations": {
                            "redhat": [{"id": "r1", "text": "q", "status": "open"}],
                            "z3": [{"id": "z1", "message": "bad", "status": "violation"}],
                        },
                    }
                ],
            }
        ],
    }
    counts = _count_annotations(json.dumps(tree))
    assert counts["node_count"] == 1
    assert counts["redhat_count"] == 1
    assert counts["z3_violations"] == 1
