"""Unit tests for the Assure development swarm. No live API calls."""

from __future__ import annotations

import io
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from prompt_matrix.pipelines import PipelineResult
from prompt_matrix.swarm import (
    TestResults,
    bind_model,
    compute_confidence_score,
    extract_files,
    file_diffs,
    parse_review,
    resolve_role_targets,
    run_swarm,
)


def _pipe(reply: str, target: str = "gemini", intent: str = "design") -> PipelineResult:
    return PipelineResult(
        workflow="single",
        prompt="compiled",
        reply=reply,
        target_ai=target,
        intent=intent,
        input_tokens=10,
        output_tokens=20,
        estimated_cost=0.01,
    )


def _skip_pytest(*_a, **_k) -> TestResults:
    return TestResults(skipped=True, skip_reason="mocked")


class ScriptedWorkflow:
    """Thread-safe run_workflow stand-in so tester+documenter can run in parallel."""

    def __init__(
        self,
        *,
        architect: list[PipelineResult],
        developer: list[PipelineResult],
        reviewer: list[PipelineResult],
        tester: list[PipelineResult],
        documenter: list[PipelineResult],
        fixer: list[PipelineResult] | None = None,
    ):
        self.lock = threading.Lock()
        self.architect = list(architect)
        self.developer = list(developer)
        self.reviewer = list(reviewer)
        self.tester = list(tester)
        self.documenter = list(documenter)
        self.fixer = list(fixer or [])
        self.calls: list[tuple] = []

    def __call__(self, target, intent, task, *_a, **kwargs):
        workflow = kwargs.get("workflow") or "single"
        with self.lock:
            self.calls.append((intent, workflow, task[:40]))
            if intent == "design":
                return self.architect.pop(0)
            if intent == "analysis":
                return self.reviewer.pop(0)
            if intent == "research":
                return self.documenter.pop(0)
            if intent == "debug":
                if "Write pytest tests" in task:
                    return self.tester.pop(0)
                if "Pytest failed" in task:
                    return self.fixer.pop(0)
                return self.developer.pop(0)
            raise AssertionError(f"unexpected intent {intent}")


class BindModelTests(unittest.TestCase):
    def test_pem_target(self):
        self.assertEqual(bind_model("claude"), ("claude", None))

    def test_pricing_key(self):
        target, model = bind_model("gemini-1.5-pro")
        self.assertEqual(target, "gemini")
        self.assertEqual(model, "gemini/gemini-3.5-flash")

    def test_flash_send_id(self):
        target, model = bind_model("gemini-1.5-flash")
        self.assertEqual(target, "gemini")
        self.assertEqual(model, "gemini/gemini-3.5-flash-lite")

    def test_litellm_id(self):
        target, model = bind_model("anthropic/claude-3-haiku-20240307")
        self.assertEqual(target, "claude")
        self.assertTrue(model)


class ReviewParseTests(unittest.TestCase):
    def test_explicit_verdict_and_fraction(self):
        verdict, conf = parse_review("Looks solid.\nVerdict: Keep\nConfidence: 0.82\n")
        self.assertEqual(verdict, "Keep")
        self.assertAlmostEqual(conf or 0, 0.82, places=2)

    def test_percent_and_revise(self):
        verdict, conf = parse_review("Verdict: Revise\nConfidence: 70%\n")
        self.assertEqual(verdict, "Revise")
        self.assertAlmostEqual(conf or 0, 0.70, places=2)


class FileExtractTests(unittest.TestCase):
    def test_headed_fence(self):
        blob = "### web.py\n```\nprint(1)\n```\n"
        files = extract_files(blob)
        self.assertEqual(files["web.py"].strip(), "print(1)")

    def test_diff_existing(self):
        diffs = file_diffs({"web.py": "a\n"}, {"web.py": "b\n"})
        self.assertIn("web.py", diffs)
        self.assertIn("-a", diffs["web.py"])

    def test_new_file_diff(self):
        diffs = file_diffs({}, {"docs/feature.md": "# Feature\n"})
        self.assertIn("docs/feature.md", diffs)
        self.assertIn("/dev/null", diffs["docs/feature.md"])


class ResolveTests(unittest.TestCase):
    def test_fallback_when_down(self):
        bindings = resolve_role_targets(
            {"architect": "claude"},
            live=["gemini", "deepseek"],
        )
        target, _model, note = bindings["architect"]
        self.assertEqual(target, "gemini")
        self.assertIn("claude was down", note or "")

    def test_local_forces_ollama(self):
        bindings = resolve_role_targets(None, live=["gemini", "ollama"], local=True)
        self.assertEqual(bindings["developer"][0], "ollama")


class ConfidenceTests(unittest.TestCase):
    def test_formula_does_not_inflate_to_gate(self):
        score = compute_confidence_score("Keep", 1.0, True, {"a.py": "x" * 80})
        self.assertAlmostEqual(score, 0.3 + 0.1 + 0.4 + 0.2, places=3)

    def test_reject_without_tests_stays_low(self):
        score = compute_confidence_score("Reject", 0.9, False, {"a.py": "x" * 80})
        self.assertLess(score, 0.8)
        self.assertAlmostEqual(score, 0.2, places=3)


class RunSwarmTests(unittest.TestCase):
    def test_keep_path_and_task_alias(self):
        script = ScriptedWorkflow(
            architect=[_pipe("goals and a spec", "gemini", "design")],
            developer=[_pipe("### app.py\n```\nprint(1)\n```\n", "deepseek", "debug")],
            reviewer=[_pipe("Verdict: Keep\nConfidence: 0.9\n", "claude", "analysis")],
            tester=[_pipe("### tests/test_app.py\n```\ndef test_ok():\n    assert True\n```\n", "gemini", "debug")],
            documenter=[_pipe("### docs/feature.md\n```\n# Feature\n```\n", "claude", "research")],
        )
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "app.py"
            src.write_text("print(0)\n", encoding="utf-8")
            with patch("prompt_matrix.swarm.live_targets", return_value=["gemini", "deepseek", "claude"]):
                with patch("prompt_matrix.swarm.run_workflow", side_effect=script):
                    with patch("prompt_matrix.swarm.run_pytest", side_effect=_skip_pytest):
                        result = run_swarm(
                            task="Add a button",
                            context_files=[str(src)],
                        )
        self.assertEqual(result.review_verdict, "Keep")
        self.assertEqual(result.reviewer_verdict, "Keep")
        self.assertEqual(result.redhat_rounds, 0)
        self.assertIn("app.py", result.files)
        self.assertIn("app.py", result.code)
        self.assertIn("app.py", result.diffs)
        self.assertIn("Add a button", result.summary)
        self.assertIn("Add a button", result.quality_report)
        self.assertEqual(result.quality_report, result.summary)
        self.assertTrue(result.run_hash)
        self.assertEqual(len(result.steps), 5)

    def test_redhat_loop_caps_at_three(self):
        code = _pipe("### a.py\n```\nx=1\n```\n", "deepseek", "debug")
        revise = _pipe("Verdict: Revise\nConfidence: 0.4\n1. fix it\n", "claude", "analysis")
        keep = _pipe("Verdict: Keep\nConfidence: 0.8\n", "claude", "analysis")
        script = ScriptedWorkflow(
            architect=[_pipe("spec", "gemini", "design")],
            developer=[code, code, code, code],
            reviewer=[revise, revise, revise, keep],
            tester=[_pipe("tests", "gemini", "debug")],
            documenter=[_pipe("docs", "claude", "research")],
        )
        with patch("prompt_matrix.swarm.live_targets", return_value=["gemini", "deepseek", "claude"]):
            with patch("prompt_matrix.swarm.run_workflow", side_effect=script) as mocked:
                with patch("prompt_matrix.swarm.run_pytest", side_effect=_skip_pytest):
                    result = run_swarm("Implement x")
        self.assertEqual(result.redhat_rounds, 3)
        self.assertEqual(result.review_verdict, "Keep")
        redhat_calls = [call for call in mocked.call_args_list if call.kwargs.get("workflow") == "redhat"]
        self.assertEqual(len(redhat_calls), 3)
        self.assertEqual(redhat_calls[0].kwargs.get("persona"), "security")

    def test_reject_after_three_is_not_approved(self):
        code = _pipe("### a.py\n```\nx=1\n```\n")
        reject = _pipe("Verdict: Reject\nConfidence: 0.2\n")
        script = ScriptedWorkflow(
            architect=[_pipe("spec")],
            developer=[code, code, code, code],
            reviewer=[reject, reject, reject, reject],
            tester=[_pipe("### tests/test_a.py\n```\ndef test_ok():\n    assert True\n```\n")],
            documenter=[_pipe("### docs/a.md\n```\n# A\n```\n")],
        )
        with patch("prompt_matrix.swarm.live_targets", return_value=["gemini", "deepseek", "claude"]):
            with patch("prompt_matrix.swarm.run_workflow", side_effect=script):
                with patch("prompt_matrix.swarm.run_pytest", side_effect=_skip_pytest):
                    result = run_swarm("Implement x")
        self.assertEqual(result.review_verdict, "Reject")
        self.assertEqual(result.redhat_rounds, 3)
        self.assertIn("not approved", result.quality_report)
        self.assertNotIn("approved (Keep)", result.quality_report)
        self.assertLess(result.confidence, 0.8)

    def test_tests_and_docs_land_in_files(self):
        script = ScriptedWorkflow(
            architect=[_pipe("spec", "gemini", "design")],
            developer=[_pipe("### app.py\n```\nprint(1)\n```\n", "deepseek", "debug")],
            reviewer=[_pipe("Verdict: Keep\nConfidence: 1\n", "claude", "analysis")],
            tester=[_pipe("### tests/test_app.py\n```\ndef test_ok():\n    assert True\n```\n", "gemini", "debug")],
            documenter=[_pipe("### docs/feature.md\n```\n# Feature\n```\n", "claude", "research")],
        )
        with patch("prompt_matrix.swarm.live_targets", return_value=["gemini", "deepseek", "claude"]):
            with patch("prompt_matrix.swarm.run_workflow", side_effect=script):
                with patch("prompt_matrix.swarm.run_pytest", side_effect=_skip_pytest):
                    result = run_swarm("Add a button")
        self.assertIn("tests/test_app.py", result.files)
        self.assertIn("docs/feature.md", result.files)
        self.assertIn("tests/test_app.py", result.diffs)
        self.assertIn("docs/feature.md", result.diffs)
        self.assertIn("## Tests", result.summary)
        self.assertIn("## Documentation", result.summary)

    def test_self_test_fail_retries_developer_cap_three(self):
        script = ScriptedWorkflow(
            architect=[_pipe("spec")],
            developer=[_pipe("### a.py\n```\nx=1\n```\n")],
            reviewer=[_pipe("Verdict: Keep\nConfidence: 0.9\n")],
            tester=[_pipe("### tests/test_a.py\n```\ndef test_ok():\n    assert True\n```\n")],
            documenter=[_pipe("### docs/a.md\n```\n# A\n```\n")],
            fixer=[
                _pipe("### a.py\n```\nx=2\n```\n"),
                _pipe("### a.py\n```\nx=2\n```\n"),
                _pipe("### a.py\n```\nx=2\n```\n"),
            ],
        )
        fail = TestResults(passed=False, stdout="FAILED", stderr="boom")
        with patch("prompt_matrix.swarm.live_targets", return_value=["gemini", "deepseek", "claude"]):
            with patch("prompt_matrix.swarm.run_workflow", side_effect=script) as mocked:
                with patch("prompt_matrix.swarm.run_pytest", side_effect=[fail, fail, fail, fail]) as pytest_mock:
                    result = run_swarm("Implement x")
        self.assertEqual(result.test_results.passed, False)
        self.assertEqual(result.test_results.fix_rounds, 3)
        self.assertEqual(pytest_mock.call_count, 4)
        self.assertEqual(len(mocked.call_args_list), 8)
        self.assertIn("Fail", result.quality_report)

    def test_pytest_overlaps_documenter(self):
        order: list[str] = []
        pytest_started = threading.Event()

        class GatedDocs(ScriptedWorkflow):
            def __call__(self, target, intent, task, *_a, **kwargs):
                if intent == "research":
                    if not pytest_started.wait(timeout=2):
                        raise AssertionError(
                            "self-test did not start while the documenter was still running"
                        )
                    order.append("docs")
                return super().__call__(target, intent, task, *_a, **kwargs)

        script = GatedDocs(
            architect=[_pipe("spec")],
            developer=[_pipe("### a.py\n```\nx=1\n```\n")],
            reviewer=[_pipe("Verdict: Keep\nConfidence: 0.9\n")],
            tester=[_pipe("### tests/test_a.py\n```\ndef test_ok():\n    assert True\n```\n")],
            documenter=[_pipe("### docs/a.md\n```\n# A\n```\n")],
        )

        def pytest_mark(*_a, **_k):
            order.append("pytest")
            pytest_started.set()
            return TestResults(skipped=True, skip_reason="mocked")

        with patch("prompt_matrix.swarm.live_targets", return_value=["gemini", "deepseek", "claude"]):
            with patch("prompt_matrix.swarm.run_workflow", side_effect=script):
                with patch("prompt_matrix.swarm.run_pytest", side_effect=pytest_mark):
                    result = run_swarm("Implement x")
        self.assertIn("tests/test_a.py", result.files)
        self.assertIn("docs/a.md", result.files)
        self.assertEqual(order, ["pytest", "docs"])

    def test_skip_tests_and_docs(self):
        script = ScriptedWorkflow(
            architect=[_pipe("spec")],
            developer=[_pipe("### a.py\n```\nx=1\n```\n")],
            reviewer=[_pipe("Verdict: Keep\nConfidence: 0.9\n")],
            tester=[],
            documenter=[],
        )
        with patch("prompt_matrix.swarm.live_targets", return_value=["gemini"]):
            with patch("prompt_matrix.swarm.run_workflow", side_effect=script) as mocked:
                result = run_swarm("x", skip_tests=True, skip_docs=True)
        self.assertEqual(result.test_results.skip_reason, "skipped by --skip-tests")
        self.assertEqual(result.documentation, "Skipped")
        self.assertEqual(len(mocked.call_args_list), 3)

    def test_create_pr_false_still_has_diffs_and_patch(self):
        script = ScriptedWorkflow(
            architect=[_pipe("spec")],
            developer=[_pipe("### app.py\n```\nprint(1)\n```\n")],
            reviewer=[_pipe("Verdict: Keep\nConfidence: 1\n")],
            tester=[_pipe("### tests/test_app.py\n```\ndef test_ok():\n    assert True\n```\n")],
            documenter=[_pipe("### docs/feature.md\n```\n# Feature\n```\n")],
        )
        with patch("prompt_matrix.swarm.live_targets", return_value=["gemini"]):
            with patch("prompt_matrix.swarm.run_workflow", side_effect=script):
                with patch("prompt_matrix.swarm.run_pytest", side_effect=_skip_pytest):
                    result = run_swarm("Add a button", create_pr=False)
        self.assertTrue(result.diffs)
        self.assertTrue(result.patch_path)
        self.assertTrue(Path(result.patch_path).is_file())

    def test_create_pr_true_mocks_gh(self):
        script = ScriptedWorkflow(
            architect=[_pipe("spec")],
            developer=[_pipe("### app.py\n```\nprint(1)\n```\n")],
            reviewer=[_pipe("Verdict: Keep\nConfidence: 1\n")],
            tester=[_pipe("### tests/test_app.py\n```\ndef test_ok():\n    assert True\n```\n")],
            documenter=[_pipe("### docs/feature.md\n```\n# Feature\n```\n")],
        )
        gh = MagicMock(returncode=0, stdout="https://example.invalid/pr/1\n", stderr="")
        with patch("prompt_matrix.swarm.live_targets", return_value=["gemini"]):
            with patch("prompt_matrix.swarm.run_workflow", side_effect=script):
                with patch("prompt_matrix.swarm.run_pytest", return_value=TestResults(passed=True, stdout="ok")):
                    with patch("prompt_matrix.swarm.subprocess.run", return_value=gh) as proc:
                        result = run_swarm("Add a button", create_pr=True, min_confidence=0.0)
        self.assertTrue(
            any(call.args and call.args[0] and call.args[0][0] == "gh" for call in proc.call_args_list)
        )
        self.assertTrue(result.patch_path)


class CliTests(unittest.TestCase):
    def test_task_flag_prints_summary(self):
        from prompt_matrix.swarm import SwarmResult, main

        fake = SwarmResult(
            task="Add a button",
            spec="spec",
            implementation="print(1)",
            tests="def test_ok():\n    assert True\n",
            documentation="# Feature\n",
            summary="# Swarm quality report\n\n## Task\nAdd a button\n",
        )
        buf = io.StringIO()
        with patch("prompt_matrix.swarm.run_swarm", return_value=fake) as mocked:
            with patch("sys.stdout", buf):
                code = main(["--task", "Add a button", "--context", "web.py"])
        self.assertEqual(code, 0)
        mocked.assert_called_once()
        kwargs = mocked.call_args.kwargs
        self.assertEqual(kwargs["task"], "Add a button")
        self.assertEqual(kwargs["context_files"], ["web.py"])
        self.assertIn("Add a button", buf.getvalue())

    def test_context_files_and_pr_flags(self):
        from prompt_matrix.swarm import SwarmResult, main

        fake = SwarmResult(task="x", summary="ok\n")
        buf = io.StringIO()
        with patch("prompt_matrix.swarm.run_swarm", return_value=fake) as mocked:
            with patch("sys.stdout", buf):
                main(
                    [
                        "--task",
                        "x",
                        "--context-files",
                        "a.py",
                        "b.py",
                        "--pr",
                    ]
                )
        kwargs = mocked.call_args.kwargs
        self.assertEqual(kwargs["context_files"], ["a.py", "b.py"])
        self.assertTrue(kwargs["create_pr"])

    def test_model_override_and_copy_only(self):
        from prompt_matrix.swarm import SwarmResult, main

        fake = SwarmResult(task="x", summary="ok\n")
        buf = io.StringIO()
        with patch("prompt_matrix.swarm.run_swarm", return_value=fake) as mocked:
            with patch("sys.stdout", buf):
                main(
                    [
                        "--task",
                        "x",
                        "--model",
                        "developer=deepseek-chat",
                        "--copy-only",
                    ]
                )
        kwargs = mocked.call_args.kwargs
        self.assertEqual(kwargs["target_models"], {"developer": "deepseek-chat"})
        self.assertFalse(kwargs["direct"])

    def test_skip_flags(self):
        from prompt_matrix.swarm import SwarmResult, main

        fake = SwarmResult(task="x", summary="ok\n")
        buf = io.StringIO()
        with patch("prompt_matrix.swarm.run_swarm", return_value=fake) as mocked:
            with patch("sys.stdout", buf):
                main(["--task", "x", "--skip-tests", "--skip-docs"])
        kwargs = mocked.call_args.kwargs
        self.assertTrue(kwargs["skip_tests"])
        self.assertTrue(kwargs["skip_docs"])


if __name__ == "__main__":
    unittest.main()
