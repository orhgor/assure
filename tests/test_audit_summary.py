"""Tests for unified audit summary helper."""

from __future__ import annotations

from prompt_matrix.services.audit_summary import (
    build_audit_summary,
    compute_gate_status,
    normalize_audit_payload,
)


def test_compute_gate_status_blocked():
    assert compute_gate_status("VIOLATION", 0) == "blocked"


def test_compute_gate_status_review():
    assert compute_gate_status("PASS", 2) == "review"


def test_compute_gate_status_pass():
    assert compute_gate_status("PASS", 0) == "pass"


def test_build_audit_summary_shape():
    z3 = {"status": "PASS", "violations": [], "lock_results": [], "locks_verified": 1}
    redhat = [{"title": "Red-hat review", "content": "ok", "model": "deepseek/deepseek-reasoner"}]
    summary = build_audit_summary(
        z3_results=z3,
        redhat_critiques=redhat,
        nodes=[{"id": "p-1", "type": "paragraph", "content": "Hi"}],
        locks=[{"canonical_key": "Revenue", "value": 100}],
    )
    assert summary["ok"] is True
    assert summary["gate_status"] == "review"
    assert summary["z3_status"] == "PASS"
    assert summary["redhat_count"] == 1
    assert summary["redhat_critiques"] == redhat
    assert summary["redhat_results"] == redhat
    assert summary["node_count"] == 1
    assert summary["lock_count"] == 1


def test_normalize_audit_payload_legacy_redhat_results():
    payload = normalize_audit_payload(
        {
            "z3_results": {"status": "VIOLATION", "violations": ["x"]},
            "redhat_results": [{"title": "t", "content": "c"}],
        }
    )
    assert payload["gate_status"] == "blocked"
    assert payload["ok"] is False
    assert payload["redhat_count"] == 1
    assert payload["redhat_critiques"][0]["title"] == "t"
