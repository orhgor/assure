"""Epsilon-greedy pick among stored prompt format variations.

Default epsilon is 0.2: 80% the current best, 20% a random other row.
This is not reinforcement learning. It is a bandit over local SQLite rows.
"""

from __future__ import annotations

import os
import random


def epsilon_from_env(default: float = 0.2) -> float:
    raw = os.environ.get("PEM_IMPROVE_EPSILON", "").strip()
    if raw:
        try:
            value = float(raw)
            return min(0.95, max(0.0, value))
        except ValueError:
            pass
    return min(0.95, max(0.0, float(default)))


def pick(rows: list[dict], *, epsilon: float = 0.2, rng: random.Random | None = None) -> dict | None:
    if not rows:
        return None
    rng = rng or random.Random()
    if len(rows) == 1:
        return rows[0]
    explore = rng.random() < epsilon
    ranked = sorted(
        rows,
        key=lambda row: (
            float(row.get("performance_score") or 0.0),
            int(row.get("usage_count") or 0),
        ),
        reverse=True,
    )
    if not explore:
        return ranked[0]
    rest = ranked[1:] or ranked
    return rng.choice(rest)
