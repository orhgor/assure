"""Answer Evolution helpers. Unittest only. No live DB."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prompt_matrix import history


class DiffRunsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "history.sqlite"
        self.patcher = patch.object(history, "DB_PATH", self.db)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def _store(self, digest: str, reply: str, prompt: str = "q") -> None:
        history.store_full_run(
            digest,
            prompt,
            reply,
            "gemini",
            "analysis",
            "single",
            1,
            2,
            force=True,
        )

    def test_same_text_is_empty_diff(self):
        self._store("aaaaaaaaaaaaaaaa", "same answer\n")
        self._store("bbbbbbbbbbbbbbbb", "same answer\n")
        self.assertEqual(history.diff_runs("aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb"), "")

    def test_insertion_shows_added_line(self):
        self._store("aaaaaaaaaaaaaaaa", "line one\n")
        self._store("bbbbbbbbbbbbbbbb", "line one\nline two\n")
        blob = history.diff_runs("aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb")
        self.assertIn("aaaaaaaaaaaaaaaa (reply)", blob)
        self.assertIn("bbbbbbbbbbbbbbbb (reply)", blob)
        self.assertIn("+line two", blob)

    def test_missing_hash_raises(self):
        self._store("aaaaaaaaaaaaaaaa", "kept\n")
        with self.assertRaises(ValueError) as ctx:
            history.diff_runs("aaaaaaaaaaaaaaaa", "missinghash00000")
        self.assertIn("missinghash00000", str(ctx.exception))

    def test_get_run_by_hash_returns_reply(self):
        self._store("aaaaaaaaaaaaaaaa", "hello\n")
        row = history.get_run_by_hash("aaaaaaaaaaaaaaaa")
        self.assertIsNotNone(row)
        self.assertEqual(row["reply"], "hello\n")
        self.assertEqual(row["run_hash"], "aaaaaaaaaaaaaaaa")
        self.assertIsNone(history.get_run_by_hash("not-a-hash"))


class HistoryDiffRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "history.sqlite"
        self.patcher = patch.object(history, "DB_PATH", self.db)
        self.patcher.start()
        self._clerk = patch.dict(
            "os.environ",
            {"CLERK_PUBLISHABLE_KEY": "", "CLERK_SECRET_KEY": ""},
            clear=False,
        )
        self._clerk.start()
        from prompt_matrix.web import create_app

        self.app = create_app(require_auth=False)
        self.client = self.app.test_client()

    def tearDown(self):
        self._clerk.stop()
        self.patcher.stop()
        self.tmp.cleanup()

    def _store(self, digest: str, reply: str) -> None:
        history.store_full_run(
            digest, "q", reply, "gemini", "analysis", "single", 1, 2, force=True
        )

    def test_missing_params_are_400(self):
        res = self.client.get("/api/history/diff")
        self.assertEqual(res.status_code, 400)
        self.assertIn("left", res.get_json()["error"])

    def test_unknown_hash_is_404(self):
        self._store("aaaaaaaaaaaaaaaa", "kept\n")
        res = self.client.get("/api/history/diff?left=aaaaaaaaaaaaaaaa&right=missinghash00000")
        self.assertEqual(res.status_code, 404)

    def test_diff_is_plain_text(self):
        self._store("aaaaaaaaaaaaaaaa", "line one\n")
        self._store("bbbbbbbbbbbbbbbb", "line one\nline two\n")
        res = self.client.get("/api/history/diff?left=aaaaaaaaaaaaaaaa&right=bbbbbbbbbbbbbbbb")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.mimetype.startswith("text/plain"))
        self.assertIn("+line two", res.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
