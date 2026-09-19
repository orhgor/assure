#!/usr/bin/env python3
"""PROBE — what the numeral audit does with a memo that states figures. Runs ON the box.

Not product code. It loads the *deployed* modules, reads the two stored drafts of
project ``walk-underwriting-2026-09-19-68e596`` out of the app's SQLite read-only,
and prints, per draft: the figures the lock inference extracted (with the answer's
``finish_reason``), and the Math Check gate's status and checked-by counters.

Nothing is written: the translation cache is stubbed out, so this probe cannot add
a ``pipeline_cache`` row or move the project's revision. It also does not touch the
running service — no restart, no deploy, no route.

The measured defect it re-checks (fixed in the commit that adds this file): the
renewal memo (revision 9, 3,780 chars, 240 numerals) asked for ~30 candidates while
lock inference capped the answer at 1024 tokens, so the reply was cut off
(``finish_reason=length``, "Unterminated string", 3372 chars ending `"unit": "USD`),
``_parse_model_json`` returned ``[]``, the compile recorded ``lock_count: 0`` and
``verify_locks`` reported SKIPPED with 0 checked on a memo full of figures.

Expected after the fix: revision 9 extracts locks and the gate reports figures
checked; a draft that states no figures still reports 0 locks and SKIPPED.

usage (on the box)::

    cd /home/ubuntu/assure-prototype && ASSURE_ENV=staging \\
        .venv/bin/python scripts/aws/_probe_lock_budget.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
os.chdir(REPO)
sys.path.insert(0, str(REPO))
os.environ.setdefault("ASSURE_ENV", "staging")

from prompt_matrix.keys import load_keys  # noqa: E402

load_keys()

from prompt_matrix import litellm_runner  # noqa: E402
from prompt_matrix.routers.draft import _tier2_candidates, verify_locks  # noqa: E402
from prompt_matrix.routers.inquire_stream import _parse_metrics  # noqa: E402
from prompt_matrix.services import lock_inference  # noqa: E402
from prompt_matrix.services import relational_translate as rt  # noqa: E402

# The measurement must not write: no cache read (every claim gets a real call, so
# the verdicts are this run's) and no cache write.
rt.load_translation = lambda key: None
rt.store_translation = lambda key, project_id, parsed: None

PROJECT = "walk-underwriting-2026-09-19-68e596"
DB = Path("prompt_matrix/history.sqlite")

# brim-cp-media43.pdf (50,200 ch) and brim-cp-media371.pdf (57,759 ch) are both
# attached to this project's compiles. v9 answers the coverage-limits ask on the
# renewal policy, v10 the coverage-limits and deductibles comparison across both.
FIXTURES = {
    9: "coverage limits / deductibles / exclusions in the renewal policy",
    10: "coverage limits + deductibles comparison between the two policies",
}

# A draft that states no figure at all: the page promises a skipped check, not a
# pass, and that must survive the fix.
NO_FIGURES = (
    "The renewal policy excludes flood and earth movement. Coverage follows the "
    "controlling underlying policy wording. Deductibles are as per that policy."
)


def flatten(node: object, out: list[str]) -> list[str]:
    if isinstance(node, dict):
        text = node.get("text") if isinstance(node.get("text"), str) else node.get("content")
        if isinstance(text, str) and text.strip():
            out.append(text)
        for key, value in node.items():
            if key in ("text", "content"):
                continue
            flatten(value, out)
    elif isinstance(node, list):
        for value in node:
            flatten(value, out)
    return out


def read_drafts() -> dict[int, tuple[str, list[dict]]]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        drafts: dict[int, tuple[str, list[dict]]] = {}
        for version in FIXTURES:
            row = con.execute(
                "select jdf_tree, truth_ledger from jdf_revisions "
                "where project_id=? and version=?",
                (PROJECT, version),
            ).fetchone()
            if row is None:
                raise SystemExit(f"no revision {version} for {PROJECT} in {DB}")
            text = "\n\n".join(flatten(json.loads(row["jdf_tree"]), []))
            ledger = json.loads(row["truth_ledger"] or "{}")
            drafts[version] = (
                text,
                [{"canonical_key": key, "value": value} for key, value in ledger.items()],
            )
        return drafts
    finally:
        con.close()


def translate(claim: str, facts: dict[str, float]) -> dict:
    return rt.translate_claim(claim, facts, project_id=PROJECT)


def gate(text: str, locks: list[dict], label: str) -> dict:
    result = verify_locks(locks, text, translate=translate)
    print(
        f"    gate {label}: status={result['status']} "
        f"checked_by_value={result['checked_by_value']} "
        f"checked_by_relational={result['checked_by_relational']} "
        f"metrics_checked={result['metrics_checked']} verified={result['verified']} "
        f"violated={result['violated']} unverified={result['unverified']}"
    )
    if result["skip_reason"]:
        print(f"    gate {label}: skip_reason={result['skip_reason']}")
    for item in result["claim_results"][:10]:
        print(
            f"      [{item.get('tier')}/{item.get('verdict')}] "
            f"{str(item.get('claim'))[:64]!r} :: {str(item.get('reason'))[:80]}"
        )
    return result


def extract(text: str, label: str) -> list[dict]:
    result = lock_inference.infer_lock_candidates(text)
    meta = litellm_runner.last_completion_meta()
    print(
        f"    extract {label}: locks={len(result.candidates)} "
        f"model={result.model} finish={meta.finish_reason} budget={meta.max_tokens}"
    )
    return result.candidates


def main() -> int:
    failures: list[str] = []
    drafts = read_drafts()

    for version, (text, stored_locks) in sorted(drafts.items()):
        print(
            f"\n[revision {version}] {FIXTURES[version]}\n"
            f"    draft: {len(text)} chars, numerals={sum(c.isdigit() for c in text)}, "
            f"tier1 key:value metrics={len(_parse_metrics(text))}, "
            f"tier2 candidates={len(_tier2_candidates(text))}, "
            f"locks recorded by the compile={len(stored_locks)}"
        )
        locks = extract(text, f"v{version}")
        result = gate(text, locks, f"v{version}")
        if version == 9:
            # The renewal memo: figures must be checked once extraction survives.
            if not locks:
                failures.append("revision 9 still extracted no locks")
            if result["checked_by_value"] + result["checked_by_relational"] <= 0:
                failures.append("revision 9 still checked no figure")
            if result["status"] == "SKIPPED":
                failures.append("revision 9 still reports SKIPPED")

    print(f"\n[control] a draft with no figure: {len(NO_FIGURES)} chars, numerals=0")
    control_locks = extract(NO_FIGURES, "no-figures")
    control = gate(NO_FIGURES, control_locks, "no-figures")
    if control_locks:
        failures.append(f"the no-figure control extracted {len(control_locks)} lock(s)")
    if control["status"] != "SKIPPED":
        failures.append(f"the no-figure control reports {control['status']}, not SKIPPED")

    print("\nVERDICT: " + ("PASS" if not failures else "FAIL — " + "; ".join(failures)))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
