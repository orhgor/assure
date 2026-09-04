"""Deterministic Z3 truth ledger for metric consistency checks."""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from threading import Lock
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


_Z3_POOL_MAX = 5
_z3_pool: list[Solver] = []
_z3_pool_lock = Lock()


def _borrow_z3_solver() -> Solver:
    with _z3_pool_lock:
        if _z3_pool:
            solver = _z3_pool.pop()
            solver.reset()
            return solver
    return Solver()


def _return_z3_solver(solver: Solver) -> None:
    with _z3_pool_lock:
        try:
            solver.reset()
        except Exception:
            return
        if len(_z3_pool) < _Z3_POOL_MAX:
            _z3_pool.append(solver)


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
    _MAX_NODE_CACHE = 100
    _MAX_METRIC_CACHE = 200

    def __init__(self) -> None:
        self._solver = _borrow_z3_solver()
        self._released = False
        self._symbols: dict[str, Any] = {}
        self._locks: dict[str, tuple[float, str]] = {}
        self._ledger_epoch = 0
        # node_id -> (node_hash, ledger_epoch, (ok, msg))
        self._node_cache: OrderedDict[str, tuple[str, int, tuple[bool, str | None]]] = OrderedDict()
        # (canonical_key, incoming_value, ledger_epoch) -> (ok, msg)
        self._metric_cache: OrderedDict[tuple[str, float, int], tuple[bool, str | None]] = OrderedDict()

    def _bump_ledger_epoch(self) -> None:
        self._ledger_epoch += 1
        self._metric_cache.clear()

    @staticmethod
    def _compute_node_hash(node: dict[str, Any]) -> str:
        canonical = json.dumps(node, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _cache_node_result(
        self,
        node_id: str,
        node_hash: str,
        result: tuple[bool, str | None],
    ) -> tuple[bool, str | None]:
        self._node_cache[node_id] = (node_hash, self._ledger_epoch, result)
        self._node_cache.move_to_end(node_id)
        while len(self._node_cache) > self._MAX_NODE_CACHE:
            self._node_cache.popitem(last=False)
        return result

    def _cache_metric_result(
        self,
        cache_key: tuple[str, float, int],
        result: tuple[bool, str | None],
    ) -> tuple[bool, str | None]:
        self._metric_cache[cache_key] = result
        self._metric_cache.move_to_end(cache_key)
        while len(self._metric_cache) > self._MAX_METRIC_CACHE:
            self._metric_cache.popitem(last=False)
        return result

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
        self._bump_ledger_epoch()

    def verify_metric(self, canonical_key: str, incoming_value: float) -> tuple[bool, str | None]:
        cache_key = (canonical_key, float(incoming_value), self._ledger_epoch)
        cached = self._metric_cache.get(cache_key)
        if cached is not None:
            self._metric_cache.move_to_end(cache_key)
            return cached

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
                return self._cache_metric_result(cache_key, (False, msg))
            return self._cache_metric_result(cache_key, (True, None))
        finally:
            self._solver.pop()

    def verify_node(
        self,
        node: dict[str, Any],
        metric_values: dict[str, float] | None = None,
    ) -> tuple[bool, str | None]:
        """Verify a single AST node; memoized per node id + content hash."""
        node_id = str(node.get("id") or "").strip()
        if not node_id:
            return True, None

        node_hash = self._compute_node_hash(node)
        cached = self._node_cache.get(node_id)
        if cached and cached[0] == node_hash and cached[1] == self._ledger_epoch:
            self._node_cache.move_to_end(node_id)
            return cached[2]

        values = metric_values or {}
        for key in node.get("entities_referenced") or []:
            if key not in values:
                continue
            ok, msg = self.verify_metric(str(key), float(values[key]))
            if not ok:
                return self._cache_node_result(node_id, node_hash, (False, msg))

        return self._cache_node_result(node_id, node_hash, (True, None))

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
        if not ledger:
            return
        for key, raw in ledger.items():
            try:
                op = self._OPS.get("==")
                sym = self._sym(str(key))
                val = float(raw)
                self._solver.add(op(sym, val))
                self._locks[str(key)] = (val, "==")
            except (TypeError, ValueError):
                continue
        self._bump_ledger_epoch()

    def snapshot(self) -> dict[str, tuple[float, str]]:
        return dict(self._locks)

    def close(self) -> None:
        if self._released:
            return
        _return_z3_solver(self._solver)
        self._released = True

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
