"""Deterministic Z3 truth ledger for metric consistency checks."""

from __future__ import annotations

from typing import Any, Iterable

from pydantic import BaseModel, ValidationError
from z3 import Real, Solver, unsat

try:
    from ..models.jdf import JDFDocumentTree
except ImportError:
    from models.jdf import JDFDocumentTree


class EntityMetric(BaseModel):
    canonical_key: str
    value: float


class TruthLedgerEngine:
    """In-memory Z3 solver for locked metrics and fast contradiction detection."""

    _OPS = {
        "==": lambda a, b: a == b,
        "!=": lambda a, b: a != b,
        "<=": lambda a, b: a <= b,
        ">=": lambda a, b: a >= b,
        "<": lambda a, b: a < b,
        ">": lambda a, b: a > b,
    }

    def __init__(self) -> None:
        self._solver = Solver()
        self._symbols: dict[str, Any] = {}
        self._locks: dict[str, tuple[float, str]] = {}

    def _sym(self, canonical_key: str):
        key = (canonical_key or "").strip()
        if not key:
            raise ValueError("canonical_key required")
        if key not in self._symbols:
            self._symbols[key] = Real(key)
        return self._symbols[key]

    def lock_metric(self, canonical_key: str, value: float, constraint_type: str = "==") -> None:
        op = self._OPS.get(constraint_type)
        if op is None:
            raise ValueError(f"unsupported constraint_type: {constraint_type}")
        sym = self._sym(canonical_key)
        self._solver.add(op(sym, value))
        self._locks[canonical_key] = (float(value), constraint_type)

    def verify_metric(self, canonical_key: str, incoming_value: float) -> tuple[bool, str | None]:
        sym = self._sym(canonical_key)
        self._solver.push()
        try:
            self._solver.add(sym == float(incoming_value))
            if self._solver.check() == unsat:
                locked = self._locks.get(canonical_key)
                if locked:
                    locked_val, ctype = locked
                    msg = (
                        f"Metric '{canonical_key}'={incoming_value} contradicts "
                        f"locked {ctype} {locked_val}"
                    )
                else:
                    msg = f"Metric '{canonical_key}'={incoming_value} contradicts existing constraints"
                return False, msg
            return True, None
        finally:
            self._solver.pop()

    def validate_entities(self, entities: Iterable[tuple[str, float] | EntityMetric]) -> tuple[bool, list[str]]:
        """Fail fast on the first contradictory entity tuple."""
        violations: list[str] = []
        for item in entities:
            try:
                if isinstance(item, EntityMetric):
                    key, val = item.canonical_key, item.value
                else:
                    key, val = item
                ok, msg = self.verify_metric(key, float(val))
                if not ok and msg:
                    violations.append(msg)
                    return False, violations
            except (ValidationError, TypeError, ValueError) as exc:
                violations.append(str(exc))
                return False, violations
        return True, violations

    def load_from_document(self, tree: JDFDocumentTree | dict[str, Any]) -> None:
        ledger = tree.truth_ledger if isinstance(tree, JDFDocumentTree) else (tree.get("truth_ledger") or {})
        for key, raw in ledger.items():
            try:
                self.lock_metric(str(key), float(raw), "==")
            except (TypeError, ValueError):
                continue

    def snapshot(self) -> dict[str, tuple[float, str]]:
        return dict(self._locks)
