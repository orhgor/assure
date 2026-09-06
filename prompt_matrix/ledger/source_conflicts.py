"""Naive keyword/number conflict scan across included vault files.

Limitation: this is basic token matching (years, durations, isolated numbers).
Semantic contradiction detection is deferred to v2.0.
"""

from __future__ import annotations

import re
from typing import Any

try:
    from ..db.source_conflict_repository import list_conflicts, replace_project_conflicts
    from ..db.substrate_repository import list_included_vault_text
    from ..models.jdf import flatten_nodes
except ImportError:
    from db.source_conflict_repository import list_conflicts, replace_project_conflicts
    from db.substrate_repository import list_included_vault_text
    from models.jdf import flatten_nodes

_YEAR = re.compile(r"\b((?:19|20)\d{2})\b")
_DURATION = re.compile(r"\b(\d+)\s+(years?|months?|days?|weeks?)\b", re.I)
_NUMBER = re.compile(r"\b(\d+(?:\.\d+)?)\b")


def _facts(text: str) -> dict[str, set[str]]:
    blob = text or ""
    facts: dict[str, set[str]] = {"year": set(), "duration": set(), "number": set()}
    facts["year"].update(_YEAR.findall(blob))
    for count, unit in _DURATION.findall(blob):
        facts["duration"].add(f"{count} {unit.lower()}")
    facts["number"].update(_NUMBER.findall(blob))
    return facts


def _node_text(node: dict[str, Any]) -> str:
    return str(node.get("content") or node.get("title") or "")


def detect_source_conflicts(
    project_id: str, document: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Flag numeric/date/term disagreements between included vault files.

    Keyword-level only. Does not understand paraphrases or negation.
    """
    files = list_included_vault_text(project_id)
    found: list[dict[str, Any]] = []
    nodes = flatten_nodes(document or {}) if document else []
    claim_nodes = [n for n in nodes if isinstance(n, dict) and n.get("id")]

    for i, a in enumerate(files):
        fa = _facts(str(a.get("extracted_text") or ""))
        for b in files[i + 1 :]:
            fb = _facts(str(b.get("extracted_text") or ""))
            mismatches: list[tuple[str, set[str], set[str]]] = []
            if fa["year"] and fb["year"] and fa["year"] != fb["year"]:
                mismatches.append(("year", fa["year"], fb["year"]))
            if fa["duration"] and fb["duration"] and fa["duration"] != fb["duration"]:
                mismatches.append(("duration", fa["duration"], fb["duration"]))
            for kind, left, right in mismatches:
                only_a = sorted(left - right)
                only_b = sorted(right - left)
                if not only_a or not only_b:
                    continue
                desc = (
                    f"Keyword-level disagreement ({kind}): "
                    f"{a.get('filename')} has {', '.join(only_a[:4])}; "
                    f"{b.get('filename')} has {', '.join(only_b[:4])}. "
                    "Naive scan only — semantic NLI is not applied."
                )
                claim_id = ""
                tokens = [t.lower() for t in only_a[:3] + only_b[:3]]
                for node in claim_nodes:
                    text = _node_text(node).lower()
                    if text and any(tok in text for tok in tokens):
                        claim_id = str(node.get("id") or "")
                        break
                if not claim_id and claim_nodes:
                    claim_id = str(claim_nodes[0].get("id") or "")
                found.append(
                    {
                        "claim_id": claim_id,
                        "source_a_id": str(a.get("id") or ""),
                        "source_b_id": str(b.get("id") or ""),
                        "conflict_description": desc,
                    }
                )
    return replace_project_conflicts(project_id, found)


def conflicts_for_project(project_id: str) -> list[dict[str, Any]]:
    return list_conflicts(project_id)
