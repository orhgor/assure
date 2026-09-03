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


if __name__ == "__main__":
    unittest.main()
