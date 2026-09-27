"""Export file names that say what was parsed (plan Part 8.5, 2026-09-27).

``parsure-pr-3f9a….json`` told a reader nothing; the customer asked for names
that carry the document type, the document id and the key facts, so an export
is identifiable without opening it::

    auto-policy_doc-abc123_policy-AP-2025-0001_insured-John_Q_Sample_eff-2025-01-15_20260927T150000Z.json
    repair-estimate_doc-9c1e2f_claim-CLM-2025-093311_vin-1HGCM82633A004352_20260927T150501Z.csv
    property-unknown_doc-77ab01_20260927T150900Z.json           # nothing found: no placeholder parts

Rules: only key data points **actually found** appear (value not None and
not ``rejected``; never a placeholder such as ``policy-unknown``); every part
is slugified to ``[A-Za-z0-9._-]`` (whitespace → ``_``, NFKD-folded, no
path or shell characters); the whole name is capped at ``MAX_FILENAME``
characters by shortening the insured name first and then dropping data
points from the right — the type, the document id, the timestamp and the
extension are never dropped. The timestamp is UTC ``YYYYMMDDTHHMMSSZ`` so
names sort by time.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Any

MAX_FILENAME = 150
MAX_POINT_LEN = 40
MAX_NAME_LEN = 30
DOC_ID_LEN = 12
#: Key data points in name order: ``(prefix, field names tried in order)``.
KEY_POINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("policy", ("policy_number",)),
    ("claim", ("claim_number",)),
    ("insured", ("insured_name", "claimant_name", "patient_name", "borrower_name", "buyer_name", "grantee", "owner_name", "proposed_insured")),
    ("eff", ("effective_date", "closing_date", "issue_date", "estimate_date", "date_of_service")),
    ("loss", ("date_of_loss",)),
    ("vin", ("vin",)),
)
_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")
_REPEAT_RE = re.compile(r"[_-]{2,}")


def slug(value: Any, *, max_len: int = MAX_POINT_LEN, drop_dots: bool = False) -> str:
    """ASCII-only, filesystem-safe fragment of ``value``; ``""`` when nothing survives."""
    text = unicodedata.normalize("NFKD", str(value if value is not None else "")).encode("ascii", "ignore").decode("ascii")
    text = text.replace("'", "").replace('"', "")  # O'Brien → OBrien, not O-Brien
    if drop_dots:
        text = text.replace(".", "")
    text = re.sub(r"\s+", "_", text.strip())
    text = _SAFE_RE.sub("-", text)
    text = _REPEAT_RE.sub(lambda m: m.group(0)[0], text).strip("._-")
    return text[:max_len].rstrip("._-")


def type_slug(report: dict[str, Any]) -> str:
    cls = report.get("classification") if isinstance(report.get("classification"), dict) else {}
    doc_type = cls.get("document_type") or report.get("document_type") or "document"
    return slug(str(doc_type).replace("_", "-"), max_len=64).lower() or "document"


def _first_found(fields: list[dict[str, Any]], names: tuple[str, ...]) -> Any:
    for name in names:
        for f in fields:
            if not isinstance(f, dict) or f.get("name") != name:
                continue
            value = f.get("value")
            if value in (None, "") or f.get("field_state") == "rejected" or f.get("evidence_state") == "schema_mismatch":
                continue
            if isinstance(value, (list, dict)):
                continue
            return value
    return None


def key_points(report: dict[str, Any]) -> list[tuple[str, str]]:
    """``[(prefix, slug), …]`` for the data points the report actually found."""
    fields = [f for f in (report.get("fields") or []) if isinstance(f, dict)]
    points: list[tuple[str, str]] = []
    for prefix, names in KEY_POINTS:
        value = _first_found(fields, names)
        if value is None:
            continue
        if prefix == "insured":
            part = slug(value, max_len=MAX_NAME_LEN, drop_dots=True)
        elif prefix in ("eff", "loss"):
            part = slug(str(value)[:10], max_len=10)
        else:
            part = slug(value)
        if part:
            points.append((prefix, part))
    return points


def timestamp(now: datetime | None = None) -> str:
    dt = now or datetime.now(timezone.utc)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y%m%dT%H%M%SZ")


def build_export_filename(report: dict[str, Any], ext: str, *, suffix: str | None = None, now: datetime | None = None) -> str:
    """``<type>_doc-<id>_<points…>[_<suffix>]_<timestamp>.<ext>`` within ``MAX_FILENAME``."""
    ext = slug(ext, max_len=12).lstrip(".").lower() or "bin"
    head = [type_slug(report)]
    doc_id = slug(report.get("document_id"), max_len=DOC_ID_LEN)
    if doc_id:
        head.append(f"doc-{doc_id}")
    points = key_points(report)
    tail = ([slug(suffix, max_len=40)] if suffix and slug(suffix, max_len=40) else []) + [timestamp(now)]

    def assemble(pts: list[tuple[str, str]]) -> str:
        return "_".join(head + [f"{p}-{v}" for p, v in pts] + tail) + f".{ext}"

    name = assemble(points)
    if len(name) > MAX_FILENAME:
        shortened = [(p, v[:12].rstrip("._-")) if p == "insured" else (p, v) for p, v in points]
        name = assemble(shortened)
        points = shortened
    while len(name) > MAX_FILENAME and points:
        points = points[:-1]
        name = assemble(points)
    if len(name) > MAX_FILENAME:
        fixed = "_" + "_".join(tail) + f".{ext}"
        name = "_".join(head)[: max(1, MAX_FILENAME - len(fixed))].rstrip("._-") + fixed
    return name


def build_project_export_filename(project_id: Any, documents: list[dict[str, Any]], ext: str, *, document_type: str | None = None,
                                  state: str | None = None, now: datetime | None = None) -> str:
    """``parsure_<project>_<n>-docs[_<type>][_<state>]_<timestamp>.<ext>`` — the
    project-wide export names what it holds: how many documents, and the type
    and state filters when they were applied."""
    ext = slug(ext, max_len=12).lstrip(".").lower() or "bin"
    parts = ["parsure", slug(project_id, max_len=40) or "project", f"{len(documents)}-docs"]
    if document_type:
        parts.append(slug(str(document_type).replace("_", "-"), max_len=30).lower())
    if state:
        parts.append(slug(state, max_len=20).lower())
    name = "_".join(p for p in parts if p) + f"_{timestamp(now)}.{ext}"
    return name if len(name) <= MAX_FILENAME else name[-MAX_FILENAME:]
