"""Unit tests for PEM MCP tools. Swarm is mocked. No live Send."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prompt_matrix.engine import MatrixError
from prompt_matrix.mcp_server import (
    REPO_ROOT,
    TOOLS,
    _call_tool,
    _format_swarm_result,
    _handle,
    _truncation_warnings,
)
from prompt_matrix.swarm import SwarmResult, TestResults


def _tool_names() -> list[str]:
    return [item["name"] for item in TOOLS]


class ToolRegistryTests(unittest.TestCase):
    def test_swarm_develop_is_listed_like_pem_compile(self):
        names = _tool_names()
        self.assertIn("pem_compile", names)
        self.assertIn("pem_combine", names)
        self.assertIn("swarm_develop", names)
        self.assertIn("apply_patch", names)
        schema = next(item for item in TOOLS if item["name"] == "swarm_develop")
        self.assertIn("task", schema["inputSchema"]["required"])
        props = schema["inputSchema"]["properties"]
        for key in (
            "task",
            "context_files",
            "target_models",
            "edition",
            "max_redhat_iterations",
            "min_confidence",
            "skip_tests",
            "skip_docs",
        ):
            self.assertIn(key, props)

    def test_tools_list_handler(self):
        reply = _handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        names = [item["name"] for item in reply["result"]["tools"]]
        self.assertIn("swarm_develop", names)

    def test_initialize_mentions_swarm(self):
        reply = _handle({"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {}})
        text = reply["result"]["instructions"]
        self.assertIn("swarm_develop", text)
        self.assertIn("apply_patch", text)
        self.assertIn("stdio-only", text)

    def test_unknown_tool_lists_swarm(self):
        with self.assertRaises(MatrixError) as ctx:
            _call_tool("not_a_tool", {})
        self.assertIn("swarm_develop", str(ctx.exception))
        self.assertIn("apply_patch", str(ctx.exception))


class SwarmDevelopToolTests(unittest.TestCase):
    def test_needs_task(self):
        with self.assertRaises(MatrixError) as ctx:
            _call_tool("swarm_develop", {})
        self.assertIn("task", str(ctx.exception))

    def test_calls_run_swarm_and_formats_report(self):
        fake = SwarmResult(
            task="Add a button",
            summary="# Swarm quality report\n\n## Task\nAdd a button\n",
            review_verdict="Keep",
            review_confidence=0.9,
            confidence=0.8,
            run_hash="abc123",
            files={"app.py": "print(1)\n"},
            patch_path="/tmp/swarm.patch",
            test_results=TestResults(passed=True, stdout="ok"),
            documentation="# Feature\n",
            targets={"developer": "deepseek"},
        )
        with patch("prompt_matrix.swarm.run_swarm", return_value=fake) as mocked:
            text = _call_tool(
                "swarm_develop",
                {
                    "task": "Add a button",
                    "context_files": ["web.py", "templates/index.html"],
                    "target_models": {"developer": "deepseek-chat"},
                    "max_redhat_iterations": 1,
                    "min_confidence": 0.5,
                    "skip_tests": False,
                    "skip_docs": False,
                    "create_pr": False,
                    "direct": True,
                },
            )
        mocked.assert_called_once()
        kwargs = mocked.call_args.kwargs
        self.assertEqual(kwargs["task"], "Add a button")
        self.assertEqual(kwargs["context_files"], ["web.py", "templates/index.html"])
        self.assertEqual(kwargs["target_models"], {"developer": "deepseek-chat"})
        self.assertEqual(kwargs["max_redhat_iterations"], 1)
        self.assertEqual(kwargs["min_confidence"], 0.5)
        self.assertFalse(kwargs["skip_tests"])
        self.assertFalse(kwargs["create_pr"])
        self.assertTrue(kwargs["direct"])
        self.assertIn("Add a button", text)
        self.assertIn("self-test: Pass", text)
        self.assertIn("===== QUALITY REPORT =====", text)
        self.assertIn("Apply complete diffs", text)
        header = text.split("===== QUALITY REPORT =====")[0]
        self.assertNotIn("WARNING:", header)

    def test_skip_tests_does_not_claim_pass(self):
        fake = SwarmResult(
            task="x",
            summary="Self-test: Skipped (skipped by --skip-tests)\n",
            test_results=TestResults(skipped=True, skip_reason="skipped by --skip-tests"),
            documentation="Skipped",
        )
        with patch("prompt_matrix.swarm.run_swarm", return_value=fake) as mocked:
            text = _call_tool(
                "swarm_develop",
                {"task": "x", "skip_tests": True, "skip_docs": True},
            )
        kwargs = mocked.call_args.kwargs
        self.assertTrue(kwargs["skip_tests"])
        self.assertTrue(kwargs["skip_docs"])
        header = text.split("===== QUALITY REPORT =====")[0]
        self.assertIn("self-test: skipped", header)
        self.assertNotIn("self-test: Pass", header)
        self.assertIn("docs: skipped", header)

    def test_target_models_role_equals_list(self):
        fake = SwarmResult(task="x", summary="ok\n")
        with patch("prompt_matrix.swarm.run_swarm", return_value=fake) as mocked:
            _call_tool(
                "swarm_develop",
                {"task": "x", "target_models": ["reviewer=claude", "tester=gemini"]},
            )
        self.assertEqual(
            mocked.call_args.kwargs["target_models"],
            {"reviewer": "claude", "tester": "gemini"},
        )

    def test_edition_sets_env_for_the_call_then_restores(self):
        fake = SwarmResult(task="x", summary="ok\n")
        seen: dict[str, str | None] = {}

        def capture(**_kwargs):
            seen["edition"] = os.environ.get("ASSURE_EDITION")
            return fake

        previous = os.environ.get("ASSURE_EDITION")
        os.environ.pop("ASSURE_EDITION", None)
        try:
            with patch("prompt_matrix.swarm.run_swarm", side_effect=capture):
                _call_tool("swarm_develop", {"task": "x", "edition": "pro"})
            self.assertEqual(seen["edition"], "pro")
            self.assertIsNone(os.environ.get("ASSURE_EDITION"))
        finally:
            if previous is None:
                os.environ.pop("ASSURE_EDITION", None)
            else:
                os.environ["ASSURE_EDITION"] = previous

    def test_bad_edition(self):
        with self.assertRaises(MatrixError):
            _call_tool("swarm_develop", {"task": "x", "edition": "enterprise"})

    def test_tools_call_is_error_on_failure(self):
        with patch("prompt_matrix.swarm.run_swarm", side_effect=MatrixError("boom")):
            reply = _handle(
                {
                    "jsonrpc": "2.0",
                    "id": 9,
                    "method": "tools/call",
                    "params": {"name": "swarm_develop", "arguments": {"task": "x"}},
                }
            )
        self.assertTrue(reply["result"]["isError"])
        self.assertIn("boom", reply["result"]["content"][0]["text"])


class TruncationTests(unittest.TestCase):
    def test_cut_html_warns(self):
        result = SwarmResult(
            task="x",
            summary="ok\n",
            files={"templates/index.html": "<!DOCTYPE html>\n<html>\n<body>\n<div"},
        )
        warnings = _truncation_warnings(result)
        self.assertTrue(any("index.html" in item for item in warnings))
        text = _format_swarm_result(result)
        self.assertIn("WARNING:", text)
        self.assertIn("Apply complete diffs", text)
        self.assertLess(text.index("WARNING:"), text.index("===== QUALITY REPORT ====="))

    def test_truncated_marker_warns(self):
        result = SwarmResult(
            task="x",
            summary="body\n... [truncated]\n",
            files={"web.py": "print(1)\n... [truncated]\n"},
        )
        warnings = _truncation_warnings(result)
        self.assertTrue(any("web.py" in item for item in warnings))
        self.assertTrue(any("quality report" in item for item in warnings))

    def test_complete_html_no_warning(self):
        result = SwarmResult(
            task="x",
            summary="ok\n",
            files={"templates/index.html": "<!DOCTYPE html><html><body></body></html>\n"},
        )
        self.assertEqual(_truncation_warnings(result), [])


class RepoRootTests(unittest.TestCase):
    def test_cwd_is_repo_root_so_relative_context_files_exist(self):
        from pathlib import Path

        self.assertTrue((REPO_ROOT / "prompt_matrix" / "history.py").is_file())
        self.assertTrue((REPO_ROOT / "prompt_matrix" / "templates" / "index.html").is_file())
        self.assertEqual(Path.cwd().resolve(), REPO_ROOT)


class StdioFramingTests(unittest.TestCase):
    def test_write_is_newline_json_not_content_length(self):
        import io

        from prompt_matrix.mcp_server import _write_message

        buf = io.BytesIO()
        _write_message(buf, {"jsonrpc": "2.0", "id": 1, "result": {}})
        raw = buf.getvalue()
        self.assertTrue(raw.startswith(b"{"))
        self.assertTrue(raw.endswith(b"\n"))
        self.assertNotIn(b"Content-Length", raw)


class ApplyPatchToolTests(unittest.TestCase):
    def test_needs_diff(self):
        with self.assertRaises(MatrixError) as ctx:
            _call_tool("apply_patch", {})
        self.assertIn("diff", str(ctx.exception))

    def test_applies_fixture_diff(self):
        import difflib

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "hello.txt").write_text("hello\n", encoding="utf-8")
            diff = "".join(
                difflib.unified_diff(
                    ["hello\n"],
                    ["hello world\n"],
                    fromfile="a/hello.txt",
                    tofile="b/hello.txt",
                )
            )
            with patch("prompt_matrix.mcp_server.REPO_ROOT", root):
                text = _call_tool("apply_patch", {"diff": diff})
            self.assertIn("hello.txt", text)
            self.assertEqual((root / "hello.txt").read_text(encoding="utf-8"), "hello world\n")

    def test_rejects_truncated(self):
        with self.assertRaises(MatrixError):
            _call_tool("apply_patch", {"diff": "*** Begin Patch\n*** Update File: x.py\n"})

    def test_schema_lists_diff(self):
        schema = next(item for item in TOOLS if item["name"] == "apply_patch")
        self.assertIn("diff", schema["inputSchema"]["required"])


if __name__ == "__main__":
    unittest.main()
