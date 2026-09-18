"""Cost governance and retry budget tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prompt_matrix.cost_governance import (
    BudgetExhaustedError,
    CostGovernor,
    MAX_RETRIES,
    ProjectBudgetStore,
    TaskType,
    TokenLimitExceededError,
)


class CostGovernanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "history.sqlite"
        self.patcher = patch("prompt_matrix.cost_governance.get_db")
        self.mock_get_db = self.patcher.start()
        import sqlite3

        self.conn = sqlite3.connect(str(self.db))
        self.conn.row_factory = sqlite3.Row
        self.mock_get_db.return_value = self.conn
        self.store = ProjectBudgetStore(self.conn)
        self.store.ensure_tables()
        # project_budgets declares the FK to projects, so the project a budget row
        # names has to exist before the row can be written.
        self.conn.execute("INSERT INTO projects (id, title) VALUES ('proj-a', 'proj-a')")
        self.conn.commit()
        self.store.ensure_project("proj-a", token_limit=1000)
        self.governor = CostGovernor(budget_store=self.store)

    def tearDown(self):
        self.conn.close()
        self.patcher.stop()
        self.tmp.cleanup()

    def test_preflight_token_limit_exceeded(self):
        messages = [{"role": "user", "content": "x" * 50_000}]
        with self.assertRaises(TokenLimitExceededError):
            self.governor.preflight("proj-a", TaskType.SURGICAL_EDIT, messages)

    def test_budget_exhaustion_raises(self):
        self.store.record_usage(
            "proj-a",
            task_type="surgical_edit",
            model_id="haiku",
            input_tokens=990,
            output_tokens=0,
        )
        with self.assertRaises(BudgetExhaustedError):
            self.store.check_budget_available("proj-a", estimated_in=20)

    def test_preflight_budget_before_hard_cap_message(self):
        self.store.record_usage(
            "proj-a",
            task_type="surgical_edit",
            model_id="haiku",
            input_tokens=999,
            output_tokens=0,
        )
        messages = [{"role": "user", "content": "short edit"}]
        with self.assertRaises(BudgetExhaustedError) as ctx:
            self.governor.preflight("proj-a", TaskType.SURGICAL_EDIT, messages)
        self.assertIn("Insufficient project budget", str(ctx.exception))

    def test_redhat_hard_cap_lower_than_synthesis(self):
        from prompt_matrix.cost_governance import MAX_INPUT_TOKENS

        self.assertEqual(MAX_INPUT_TOKENS[TaskType.REDHAT], 8000)
        self.assertEqual(MAX_INPUT_TOKENS[TaskType.DEEP_SYNTHESIS], 30000)

    def test_governor_record_usage(self):
        self.governor.record_usage(
            "proj-a",
            input_tokens=100,
            output_tokens=25,
            model_id="anthropic.claude-3-5-haiku-20241022-v1:0",
            task_type=TaskType.SURGICAL_EDIT,
        )
        _limit, used = self.store.get_usage("proj-a")
        self.assertEqual(used, 125)

    def test_retry_ceiling_one_retry(self):
        attempts = {"count": 0}

        def fake_executor(model, messages, max_output, use_cache):
            attempts["count"] += 1
            return "revenue=15000000", 10, 5

        self.governor.executor = fake_executor
        engine_calls = []

        def validate(text):
            engine_calls.append(text)
            return False, "revenue mismatch"

        result = self.governor.execute_with_retry_budget(
            "proj-a",
            TaskType.SURGICAL_EDIT,
            [{"role": "user", "content": "edit revenue line"}],
            validate_fn=validate,
            build_node_fn=lambda text: {
                "type": "paragraph",
                "id": "p-1",
                "content": text,
                "entities_referenced": [],
                "meta": {},
            },
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "VALIDATION_FAILED")
        self.assertEqual(attempts["count"], MAX_RETRIES + 1)
        self.assertEqual(result.retries, MAX_RETRIES)
        self.assertIsNotNone(result.node)
        self.assertEqual(result.node.get("status"), "VALIDATION_FAILED")


if __name__ == "__main__":
    unittest.main()
