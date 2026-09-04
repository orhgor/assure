"""Unit tests for the Assure development swarm. No live API calls."""

from __future__ import annotations

import io
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from prompt_matrix.pipelines import PipelineResult
from prompt_matrix.cost_router import cap_output_tokens, role_output_limits
from prompt_matrix.litellm_runner import completion_limits
from prompt_matrix.patch_apply import (
    PatchError,
    apply_unified_diff,
    dump_is_truncated,
    parse_planned_paths,
)
from prompt_matrix.swarm import (
    ROLE_MAX_TOKENS,
    TEST_COMMAND,
    TestResults,
    _tester_task,
    accepted_files,
    log_developer_attempt,
    bind_model,
    compute_confidence_score,
    extract_files,
    file_diffs,
    parse_review,
    resolve_role_targets,
    run_pytest,
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
                if "TEST_COMMAND" in task or "Write unittest" in task:
                    return self.tester.pop(0)
                if "Unittest failed" in task:
                    return self.fixer.pop(0)
                return self.developer.pop(0)
            raise AssertionError(f"unexpected intent {intent}")


class TesterPromptTests(unittest.TestCase):
    def test_tester_prompt_forces_unittest_command(self):
        text = _tester_task("Add a button", "print(1)")
        self.assertIn(f"TEST_COMMAND: {TEST_COMMAND}", text)
        self.assertIn("unittest.TestCase", text)
        self.assertNotIn("Use pytest", text)
        self.assertIn("discover -s tests", TEST_COMMAND)
        self.assertNotIn("prompt_matrix/tests", TEST_COMMAND)

    def test_reviewer_prompt_forbids_live_verify(self):
        from prompt_matrix.swarm import _reviewer_task
        from prompt_matrix.swarm_lint import LintReport

        text = _reviewer_task("Add waitlist", "spec", "print(1)", lint=LintReport(ok=True))
        self.assertIn("Local lint: PASS", text)
        self.assertIn("pem --direct", text)
        self.assertIn("/api/render", text)

    def test_self_test_runs_unittest_not_pytest(self):
        body = (
            "from unittest import TestCase\n"
            "class Tiny(TestCase):\n"
            "    def test_ok(self):\n"
            "        self.assertTrue(True)\n"
        )
        result = run_pytest({"tests/test_tiny.py": body})
        self.assertFalse(result.skipped, result.skip_reason)
        self.assertTrue(result.passed, (result.stdout or "") + (result.stderr or ""))


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
            tester=[
                _pipe(
                    "### tests/test_app.py\n```\ndef test_ok():\n    assert True\n```\n",
                    "gemini",
                    "debug",
                )
            ],
            documenter=[_pipe("### docs/feature.md\n```\n# Feature\n```\n", "claude", "research")],
        )
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "app.py"
            src.write_text("print(0)\n", encoding="utf-8")
            with patch(
                "prompt_matrix.swarm.live_targets", return_value=["gemini", "deepseek", "claude"]
            ):
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
        with patch(
            "prompt_matrix.swarm.live_targets", return_value=["gemini", "deepseek", "claude"]
        ):
            with patch("prompt_matrix.swarm.run_workflow", side_effect=script) as mocked:
                with patch("prompt_matrix.swarm.run_pytest", side_effect=_skip_pytest):
                    result = run_swarm("Implement x")
        self.assertEqual(result.redhat_rounds, 3)
        self.assertEqual(result.review_verdict, "Keep")
        redhat_calls = [
            call for call in mocked.call_args_list if call.kwargs.get("workflow") == "redhat"
        ]
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
        with patch(
            "prompt_matrix.swarm.live_targets", return_value=["gemini", "deepseek", "claude"]
        ):
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
            tester=[
                _pipe(
                    "### tests/test_app.py\n```\ndef test_ok():\n    assert True\n```\n",
                    "gemini",
                    "debug",
                )
            ],
            documenter=[_pipe("### docs/feature.md\n```\n# Feature\n```\n", "claude", "research")],
        )
        with patch(
            "prompt_matrix.swarm.live_targets", return_value=["gemini", "deepseek", "claude"]
        ):
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
        with patch(
            "prompt_matrix.swarm.live_targets", return_value=["gemini", "deepseek", "claude"]
        ):
            with patch("prompt_matrix.swarm.run_workflow", side_effect=script) as mocked:
                with patch(
                    "prompt_matrix.swarm.run_pytest", side_effect=[fail, fail, fail, fail]
                ) as pytest_mock:
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

        with patch(
            "prompt_matrix.swarm.live_targets", return_value=["gemini", "deepseek", "claude"]
        ):
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

    def test_broken_python_forces_revise_without_reviewer_send(self):
        broken = _pipe("### web.py\n```\ndef waitlist(\n```\n", "deepseek", "debug")
        script = ScriptedWorkflow(
            architect=[_pipe("## Files to write\n- web.py\n", "gemini", "design")],
            developer=[broken, broken, broken, broken],
            reviewer=[],
            tester=[_pipe("tests", "gemini", "debug")],
            documenter=[_pipe("docs", "claude", "research")],
        )
        with patch(
            "prompt_matrix.swarm.live_targets", return_value=["gemini", "deepseek", "claude"]
        ):
            with patch("prompt_matrix.swarm.run_workflow", side_effect=script):
                with patch("prompt_matrix.swarm.run_pytest", side_effect=_skip_pytest):
                    result = run_swarm("Add waitlist")
        self.assertEqual(result.review_verdict, "Revise")
        self.assertIs(result.lint_ok, False)
        self.assertIn("Local lint", result.quality_report)
        self.assertIn("FAIL", result.quality_report)
        self.assertFalse(any(intent == "analysis" for intent, _wf, _task in script.calls))

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
                with patch(
                    "prompt_matrix.swarm.run_pytest",
                    return_value=TestResults(passed=True, stdout="ok"),
                ):
                    with patch("prompt_matrix.swarm.subprocess.run", return_value=gh) as proc:
                        result = run_swarm("Add a button", create_pr=True, min_confidence=0.0)
        self.assertTrue(
            any(
                call.args and call.args[0] and call.args[0][0] == "gh"
                for call in proc.call_args_list
            )
        )
        self.assertTrue(result.patch_path)


class DeveloperLimitsTests(unittest.TestCase):
    def test_developer_max_tokens_at_least_16384(self):
        self.assertGreaterEqual(ROLE_MAX_TOKENS["developer"], 16384)
        self.assertEqual(cap_output_tokens("debug", "deepseek-chat"), 512)
        with role_output_limits(max_tokens=16384, timeout=180):
            self.assertGreaterEqual(cap_output_tokens("debug", "deepseek-chat"), 16384)
            tokens, seconds = completion_limits(intent="debug", model="deepseek-chat")
            self.assertGreaterEqual(tokens, 16384)
            self.assertGreaterEqual(seconds, 180)

    def test_developer_call_uses_token_floor(self):
        script = ScriptedWorkflow(
            architect=[_pipe("spec")],
            developer=[_pipe("### a.py\n```\nx=1\n```\n")],
            reviewer=[_pipe("Verdict: Keep\nConfidence: 0.9\n")],
            tester=[],
            documenter=[],
        )
        with patch("prompt_matrix.swarm.live_targets", return_value=["gemini"]):
            with patch("prompt_matrix.swarm.run_workflow", side_effect=script):
                with patch(
                    "prompt_matrix.swarm.role_output_limits", wraps=role_output_limits
                ) as mocked:
                    run_swarm("x", skip_tests=True, skip_docs=True)
        self.assertTrue(
            any((call.kwargs.get("max_tokens") or 0) >= 16384 for call in mocked.call_args_list)
        )


class PerFileAndTruncationTests(unittest.TestCase):
    def test_parse_planned_paths(self):
        spec = (
            "# Spec\n\n## Files to write\n- a.py\n- templates/index.html\n\n## Notes\nskip me.py\n"
        )
        self.assertEqual(parse_planned_paths(spec), ["a.py", "templates/index.html"])

    def test_multi_file_developer_is_per_path(self):
        spec = "plan\n\n## Files to write\n- a.py\n- b.py\n"
        script = ScriptedWorkflow(
            architect=[_pipe(spec, "gemini", "design")],
            developer=[
                _pipe("### a.py\n```\na=1\n```\n", "deepseek", "debug"),
                _pipe("### b.py\n```\nb=2\n```\n", "deepseek", "debug"),
            ],
            reviewer=[_pipe("Verdict: Keep\nConfidence: 0.9\n", "claude", "analysis")],
            tester=[],
            documenter=[],
        )
        with patch(
            "prompt_matrix.swarm.live_targets", return_value=["gemini", "deepseek", "claude"]
        ):
            with patch("prompt_matrix.swarm.run_workflow", side_effect=script) as mocked:
                result = run_swarm("two files", skip_tests=True, skip_docs=True)
        self.assertEqual(result.files["a.py"].strip(), "a=1")
        self.assertEqual(result.files["b.py"].strip(), "b=2")
        debug_calls = [call for call in mocked.call_args_list if call.args[1] == "debug"]
        self.assertEqual(len(debug_calls), 2)
        self.assertTrue(any("a.py" in (call.args[2] or "") for call in debug_calls))
        self.assertTrue(any("b.py" in (call.args[2] or "") for call in debug_calls))
        self.assertEqual(len(result.steps), 4)

    def test_unclosed_fence_continues_then_applies(self):
        script = ScriptedWorkflow(
            architect=[_pipe("spec")],
            developer=[
                _pipe("### page.html\n```\n<!DOCTYPE html>\n<html>\n<body>\n<p>hi</p>\n"),
                _pipe("</body>\n</html>\n```\n"),
            ],
            reviewer=[_pipe("Verdict: Keep\nConfidence: 0.9\n")],
            tester=[],
            documenter=[],
        )
        with patch("prompt_matrix.swarm.live_targets", return_value=["gemini"]):
            with patch("prompt_matrix.swarm.run_workflow", side_effect=script):
                result = run_swarm("html page", skip_tests=True, skip_docs=True)
        self.assertIn("page.html", result.files)
        self.assertIn("</html>", result.files["page.html"].lower())
        self.assertFalse(dump_is_truncated(result.files["page.html"], path="page.html"))

    def test_truncated_html_is_not_applied(self):
        script = ScriptedWorkflow(
            architect=[_pipe("spec")],
            developer=[
                _pipe(
                    "### templates/index.html\n```\n<!DOCTYPE html>\n<html>\n<body>\n<div\n```\n"
                ),
                _pipe("<span>still cut\n"),
                _pipe("no close\n"),
                _pipe("still no\n"),
                _pipe("nope\n"),
                _pipe("redhat 1\n"),
                _pipe("redhat 2\n"),
                _pipe("redhat 3\n"),
            ],
            reviewer=[_pipe("Verdict: Keep\nConfidence: 0.9\n")],
            tester=[],
            documenter=[],
        )
        with patch("prompt_matrix.swarm.live_targets", return_value=["gemini"]):
            with patch("prompt_matrix.swarm.run_workflow", side_effect=script):
                result = run_swarm("cut html", skip_tests=True, skip_docs=True)
        self.assertNotIn("templates/index.html", result.files)
        self.assertTrue(any("not applying" in note or "truncated" in note for note in result.notes))
        self.assertEqual(result.review_verdict, "Revise")
        self.assertIs(result.lint_ok, False)

    def test_accepted_files_drops_cut_html(self):
        blob = "### templates/index.html\n```\n<!DOCTYPE html>\n<html>\n<body>\n<div\n```\n"
        files, skipped = accepted_files(blob)
        self.assertEqual(files, {})
        self.assertTrue(skipped)

    def test_log_developer_attempt_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            log_developer_attempt("app.py", "x" * 600, log_dir=log_dir)
            text = (log_dir / "swarm_attempt.log").read_text(encoding="utf-8")
        self.assertIn("=== ATTEMPT: app.py ===", text)
        self.assertIn("x" * 500, text)
        self.assertIn("...", text)
        self.assertNotIn("x" * 600, text)


class ApplyPatchHelperTests(unittest.TestCase):
    def test_applies_unified_diff(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "hello.txt").write_text("hello\n", encoding="utf-8")
            import difflib

            diff = "".join(
                difflib.unified_diff(
                    ["hello\n"],
                    ["hello world\n"],
                    fromfile="a/hello.txt",
                    tofile="b/hello.txt",
                )
            )
            text = apply_unified_diff(diff, root=root)
            self.assertIn("hello.txt", text)
            self.assertEqual((root / "hello.txt").read_text(encoding="utf-8"), "hello world\n")

    def test_rejects_traversal(self):
        diff = "--- a/../../etc/passwd\n" "+++ b/../../etc/passwd\n" "@@ -1 +1 @@\n" "-a\n" "+b\n"
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PatchError):
                apply_unified_diff(diff, root=Path(tmp))

    def test_rejects_truncated_dump(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PatchError):
                apply_unified_diff("```\ndiff --git a/foo.py b/foo.py\n", root=Path(tmp))
            with self.assertRaises(PatchError):
                apply_unified_diff("*** Begin Patch\n*** Update File: x.py\n", root=Path(tmp))

    def test_new_file_diff(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            import difflib

            diff = "".join(
                difflib.unified_diff(
                    [],
                    ["print(1)\n"],
                    fromfile="/dev/null",
                    tofile="b/app.py",
                )
            )
            apply_unified_diff(diff, root=root)
            self.assertEqual((root / "app.py").read_text(encoding="utf-8"), "print(1)\n")


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

    def test_apply_flag(self):
        from prompt_matrix.swarm import SwarmResult, main

        fake = SwarmResult(task="x", summary="ok\n")
        buf = io.StringIO()
        with patch("prompt_matrix.swarm.run_swarm", return_value=fake) as mocked:
            with patch("sys.stdout", buf):
                main(["--task", "x", "--apply"])
        self.assertTrue(mocked.call_args.kwargs["apply_workspace"])


class ContextAndApplyTests(unittest.TestCase):
    def test_load_context_resolves_bare_name(self):
        from prompt_matrix.swarm import load_context

        blob, originals, notes = load_context(["web.py"])
        self.assertTrue(any("prompt_matrix/web.py" in key for key in originals))
        self.assertTrue(
            any("Resolved context" in note for note in notes) or "prompt_matrix/web.py" in originals
        )

    def test_write_workspace_files_and_reject_traversal(self):
        from prompt_matrix.patch_apply import PatchError, write_workspace_files

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            msg = write_workspace_files({"ok.py": "x = 1\n"}, root=root)
            self.assertIn("ok.py", msg)
            self.assertEqual((root / "ok.py").read_text(encoding="utf-8"), "x = 1\n")
            with self.assertRaises(PatchError):
                write_workspace_files({"../escape.py": "nope\n"}, root=root)


if __name__ == "__main__":
    unittest.main()
