"""Z3 metric-ledger accuracy harness (not an LLM fact checker)."""

from __future__ import annotations

import json
from pathlib import Path

from prompt_matrix.ledger.truth_engine import run_z3_verification

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "tests" / "data" / "z3_benchmark.jsonl"
ACCURACY_FLOOR = 0.8


def _label(score: float) -> str:
    if score > 0.6:
        return "true"
    if score < 0.4:
        return "false"
    return "uncertain"


def test_z3_accuracy() -> None:
    rows = []
    for line in DATASET.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    assert 20 <= len(rows) <= 40
    tp = fp = fn = 0
    correct = 0
    for row in rows:
        result = run_z3_verification(
            row["claim"],
            context=str(row.get("context") or ""),
            ledger=row.get("ledger"),
        )
        predicted = _label(float(result["score"]))
        expected = row["expected"]
        if predicted == expected:
            correct += 1
            tp += 1
        else:
            fp += 1
            fn += 1
    accuracy = correct / len(rows)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    assert accuracy >= ACCURACY_FLOOR, (
        f"Z3 accuracy {accuracy:.2%} below {ACCURACY_FLOOR:.0%} "
        f"(precision={precision:.2%} recall={recall:.2%})"
    )
