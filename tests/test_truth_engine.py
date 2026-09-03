"""Z3 truth ledger tests."""

from __future__ import annotations

import unittest

from prompt_matrix.ledger.truth_engine import TruthLedgerEngine


class TruthEngineTests(unittest.TestCase):
    def test_contradictory_revenue_12m_vs_15m(self):
        engine = TruthLedgerEngine()
        engine.lock_metric("revenue", 12_000_000, "==")
        ok, msg = engine.verify_metric("revenue", 15_000_000)
        self.assertFalse(ok)
        self.assertIsNotNone(msg)
        self.assertIn("revenue", msg or "")

    def test_budget_boundary_inequality_breach(self):
        engine = TruthLedgerEngine()
        engine.lock_metric("spend", 250_000, "<=")
        ok, msg = engine.verify_metric("spend", 260_000)
        self.assertFalse(ok)
        self.assertIsNotNone(msg)

    def test_validate_entities_batch(self):
        engine = TruthLedgerEngine()
        engine.lock_metric("headcount", 42, "==")
        ok, violations = engine.validate_entities([("headcount", 42), ("headcount", 43)])
        self.assertFalse(ok)
        self.assertTrue(violations)

    def test_verify_metric_memoization(self):
        engine = TruthLedgerEngine()
        engine.lock_metric("revenue", 12_000_000, "==")
        first = engine.verify_metric("revenue", 15_000_000)
        second = engine.verify_metric("revenue", 15_000_000)
        self.assertEqual(first, second)
        self.assertIn(("revenue", 15_000_000.0, engine._ledger_epoch), engine._metric_cache)

    def test_verify_node_cache_per_node(self):
        engine = TruthLedgerEngine()
        engine.lock_metric("revenue", 12_000_000, "==")
        node_a = {
            "id": "p-1",
            "type": "paragraph",
            "content": "A",
            "entities_referenced": ["revenue"],
        }
        node_b = {
            "id": "p-2",
            "type": "paragraph",
            "content": "B",
            "entities_referenced": ["revenue"],
        }
        ok_a, _ = engine.verify_node(node_a, {"revenue": 12_000_000})
        ok_b, _ = engine.verify_node(node_b, {"revenue": 12_000_000})
        self.assertTrue(ok_a)
        self.assertTrue(ok_b)
        self.assertIn("p-1", engine._node_cache)
        self.assertIn("p-2", engine._node_cache)

        ok_a_again, _ = engine.verify_node(node_a, {"revenue": 12_000_000})
        self.assertTrue(ok_a_again)


if __name__ == "__main__":
    unittest.main()
