"""Entailment misses are checked in parallel; verdicts and order are unchanged."""

from __future__ import annotations

import threading
import time

from prompt_matrix.services.entailment import attach_entailment_to_tree


def _doc(n: int) -> dict:
    children = []
    for i in range(n):
        children.append(
            {
                "id": f"p{i}",
                "type": "paragraph",
                "content": f"Claim number {i}.",
                "provenance": [{"extracted_quote": f"Source sentence {i}."}],
            }
        )
    return {"document_id": "d", "body": [{"id": "s", "type": "section", "title": "S", "children": children}]}


def test_misses_run_concurrently_and_every_node_gets_its_verdict(monkeypatch):
    monkeypatch.setenv("ASSURE_ENTAILMENT_WORKERS", "4")
    active = {"now": 0, "max": 0}
    lock = threading.Lock()
    calls: list[tuple[str, str]] = []

    def slow_checker(claim: str, source: str) -> dict:
        with lock:
            active["now"] += 1
            active["max"] = max(active["max"], active["now"])
            calls.append((claim, source))
        time.sleep(0.05)
        with lock:
            active["now"] -= 1
        return {"verdict": "yes", "reasoning": "stated", "model": "stub", "checked_at": "2026-09-24T00:00:00+00:00"}

    doc = _doc(8)
    t0 = time.monotonic()
    out = attach_entailment_to_tree(doc, checker=slow_checker)
    elapsed = time.monotonic() - t0
    assert len(calls) == 8, "one call per (claim, source), no duplicates"
    assert active["max"] > 1, "checks overlapped"
    assert elapsed < 8 * 0.05 * 0.9, f"not parallel: {elapsed:.2f}s"
    nodes = out["body"][0]["children"]
    assert [n["id"] for n in nodes] == [f"p{i}" for i in range(8)]
    assert all(n["meta"]["provenance"]["entailment"]["verdict"] == "yes" for n in nodes)


def test_checker_error_becomes_unverified_not_a_crash():
    def boom(claim: str, source: str) -> dict:
        raise RuntimeError("provider down")

    out = attach_entailment_to_tree(_doc(3), checker=boom)
    nodes = out["body"][0]["children"]
    assert len(nodes) == 3
