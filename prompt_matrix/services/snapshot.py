"""Canonical run snapshot and artifact hash gate for Parsure intake reports.

Why (customer engineering handoff, 2026-09-27, "State Drift Across
Artifacts"): the JSON API, the record page, the CSV, the dossier PDF and a
replay all read the same ``parsure_reports.report_json`` row, but nothing
proved it. A row edited by hand, a partial write, or a report re-serialised
by an older build could ship four artifacts that disagree with each other and
with the row. The stored JSON is now *the* canonical snapshot: every write
(``parsure_repository.save_report`` / ``update_report``) stamps
``report["snapshot"]`` with a SHA-256 over the canonical form, and every
export recomputes it and refuses (HTTP 409, nothing partial) when it differs.

What is hashed: the report with :data:`VOLATILE_KEYS` removed, serialised as
``json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False,
default=str)``. That is exactly the shape ``parsure_repository._dumps``
writes and ``json.loads`` gives back — tuples become lists, non-JSON values
become their ``str`` — so the hash of the in-memory dict at save time equals
the hash of the row read back (checked by ``tests/test_parsure_integrity.py``).

What is excluded and why:

* ``snapshot`` — cannot contain its own hash.
* ``updated_at`` — rewritten by every ``update_report`` and by ``_load`` from
  the column; ``created_at`` likewise comes back from a ``DATETIME`` column via
  ``_ts`` (seconds precision, ``%Y-%m-%d %H:%M:%S``) and a seed that wrote an
  ISO string would round-trip to a different text. Both are row metadata, not
  the finding.
* ``_page_texts``, ``_page_quality``, ``_layout`` — private working keys
  ``public_report`` strips from every artifact; they are evidence for a
  re-read, not part of the exported contract, and a replay must compare the
  *contract* it produced, not the text it read.
* ``timings_ms`` — wall-clock measurements of the run; two identical results
  produced in 812 ms and 830 ms are the same artifact.

Policy versions travel with the hash so a mismatch can be told apart from a
legitimate re-stamp under a new policy: ``schema_version``, ``policy_version``,
``node_id_policy``, ``redhat_policy`` (``report["redhat"]["policy"]``),
``parser_version``, ``verification_version``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

ALGORITHM = "sha256/canonical-json-v1"

#: Keys removed before hashing (module docstring says why for each).
VOLATILE_KEYS: frozenset[str] = frozenset({
    "snapshot",
    "updated_at",
    "created_at",
    "_page_texts",
    "_page_quality",
    "_layout",
    "timings_ms",
})

#: Report keys copied into ``snapshot.policy_versions``; ``redhat_policy`` is
#: read from ``report["redhat"]["policy"]``.
POLICY_KEYS = ("schema_version", "policy_version", "node_id_policy", "parser_version", "verification_version")


class SnapshotMismatch(Exception):
    """The stored report's ``snapshot.content_hash`` does not match a recompute.

    Raised by callers that must export nothing on a mismatch; ``payload()`` is
    the 409 body every export route answers with.
    """

    error = "artifact integrity: report snapshot mismatch"

    def __init__(self, report_id: Any, expected: Any, actual: Any) -> None:
        self.report_id = report_id
        self.expected = expected
        self.actual = actual
        super().__init__(f"{self.error} ({report_id}: stored {expected}, recomputed {actual})")

    def payload(self) -> dict[str, Any]:
        return {"ok": False, "error": self.error, "report_id": self.report_id, "expected": self.expected, "actual": self.actual}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def canonical_json(report: dict[str, Any]) -> str:
    """The hashed text: volatile keys removed, keys sorted, compact separators,
    ``default=str`` — the serialisation the repository row uses."""
    body = {k: v for k, v in (report or {}).items() if k not in VOLATILE_KEYS}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def content_hash(report: dict[str, Any]) -> str:
    """SHA-256 hex of :func:`canonical_json`."""
    return hashlib.sha256(canonical_json(report).encode("utf-8")).hexdigest()


def policy_versions(report: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {k: report.get(k) for k in POLICY_KEYS}
    redhat = report.get("redhat") if isinstance(report.get("redhat"), dict) else {}
    out["redhat_policy"] = redhat.get("policy")
    return out


def stamp(report: dict[str, Any]) -> dict[str, Any]:
    """Set ``report["snapshot"]`` from the report's current content and return the block.

    Called by the repository on every write; the block is
    ``{algorithm, content_hash, stamped_at, policy_versions}``.
    """
    block = {
        "algorithm": ALGORITHM,
        "content_hash": content_hash(report),
        "stamped_at": _now(),
        "policy_versions": policy_versions(report),
    }
    report["snapshot"] = block
    return block


def verify(report: dict[str, Any]) -> dict[str, Any]:
    """``{"ok", "expected", "actual"}`` — ``expected`` is the stored hash (``None``
    for a report saved before stamping existed, which is *not* ok: an unstamped
    row cannot prove anything and is refused like a tampered one)."""
    stored = report.get("snapshot") if isinstance(report.get("snapshot"), dict) else {}
    expected = stored.get("content_hash")
    actual = content_hash(report)
    return {"ok": bool(expected) and expected == actual, "expected": expected, "actual": actual}


#: Plan Part 2.6 (2026-09-27): an export is also refused when the field graph
#: is not whole — ``graph_integrity.integrity_score`` (anchored / fields)
#: under this, i.e. a field the report cannot point at in the document.
GRAPH_INTEGRITY_MIN = 0.9


class GraphIntegrityRefused(SnapshotMismatch):
    """The report's fields are not all anchored to the document graph."""

    error = "artifact integrity: field graph below the integrity floor"

    def __init__(self, report_id: Any, score: Any, orphans: Any) -> None:
        self.report_id = report_id
        self.expected = f">= {GRAPH_INTEGRITY_MIN}"
        self.actual = score
        self.orphans = orphans
        Exception.__init__(self, f"{self.error} ({report_id}: integrity_score {score}, orphans {orphans})")

    def payload(self) -> dict[str, Any]:
        return {"ok": False, "error": self.error, "report_id": self.report_id, "expected": self.expected, "actual": self.actual,
                "orphans": self.orphans}


def graph_check(report: dict[str, Any]) -> dict[str, Any]:
    """``{"ok", "score", "orphans"}`` from ``graph_integrity``; a report without
    the block (saved before it existed) is ok — nothing is known against it."""
    gi = report.get("graph_integrity") if isinstance(report.get("graph_integrity"), dict) else None
    if not gi:
        return {"ok": True, "score": None, "orphans": None}
    score = gi.get("integrity_score")
    if score is None:
        total, anchored = int(gi.get("fields") or 0), int(gi.get("anchored") or 0)
        score = round(anchored / total, 4) if total else 1.0
    return {"ok": float(score) >= GRAPH_INTEGRITY_MIN, "score": score, "orphans": gi.get("orphan_list") or gi.get("orphans")}


def require_intact(report: dict[str, Any]) -> dict[str, Any]:
    """:func:`verify`, raising :class:`SnapshotMismatch` when not ok, then
    :func:`graph_check`, raising :class:`GraphIntegrityRefused`."""
    check = verify(report)
    if not check["ok"]:
        raise SnapshotMismatch(report.get("report_id"), check["expected"], check["actual"])
    graph = graph_check(report)
    if not graph["ok"]:
        raise GraphIntegrityRefused(report.get("report_id"), graph["score"], graph["orphans"])
    return {**check, "graph": graph}
