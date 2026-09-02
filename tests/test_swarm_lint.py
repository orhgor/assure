"""Local lint for swarm dumps. No live APIs."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from prompt_matrix.swarm_lint import local_lint


class LocalLintTests(unittest.TestCase):
    def test_py_compile_catches_truncated_function(self):
        report = local_lint({"web.py": "def waitlist(\n"})
        self.assertFalse(report.ok)
        self.assertTrue(any("py_compile" in item for item in report.errors))

    def test_valid_python_passes(self):
        report = local_lint({"web.py": "def waitlist():\n    return {'status': 'ok'}\n"})
        self.assertTrue(report.ok, report.errors)

    def test_sql_mismatched_parens(self):
        report = local_lint({"waitlist.sql": "CREATE TABLE waitlist (\n  id UUID\n"})
        self.assertFalse(report.ok)
        self.assertTrue(any("SQL" in item for item in report.errors))

    def test_sql_create_table_ok(self):
        body = (
            "CREATE TABLE waitlist (\n"
            "  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),\n"
            "  email TEXT UNIQUE NOT NULL\n"
            ");\n"
        )
        report = local_lint({"waitlist.sql": body})
        self.assertTrue(report.ok, report.errors)

    def test_undeclared_supabase_sdk_import(self):
        with patch("prompt_matrix.swarm_lint.importlib.util.find_spec", return_value=None):
            report = local_lint({"cloud.py": "from supabase import create_client\n"})
        self.assertFalse(report.ok)
        self.assertTrue(any("supabase" in item for item in report.errors))

    def test_flask_import_is_declared(self):
        report = local_lint({"web.py": "from flask import Flask, jsonify\n"})
        self.assertTrue(report.ok, report.errors)

    def test_empty_patch_when_planned_files_missing(self):
        spec = "## Files to write\n- prompt_matrix/web.py\n"
        report = local_lint({}, spec=spec, diffs={})
        self.assertFalse(report.ok)
        self.assertTrue(any("empty patch" in item for item in report.errors))

    def test_unchanged_files_are_empty_patch(self):
        report = local_lint({"a.py": "x = 1\n"}, diffs={})
        self.assertFalse(report.ok)
        self.assertTrue(any("did not change" in item for item in report.errors))

    def test_does_not_touch_the_network(self):
        with patch("urllib.request.urlopen", side_effect=AssertionError("network")):
            with patch("urllib.request.Request", side_effect=AssertionError("network")):
                report = local_lint({"web.py": "print(1)\n"})
        self.assertTrue(report.ok, report.errors)


if __name__ == "__main__":
    unittest.main()
