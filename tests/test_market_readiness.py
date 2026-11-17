"""Market readiness: example chips, catalogs, compose, CI file. No live Send."""

from __future__ import annotations

import base64
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from prompt_matrix.i18n import CATALOGS, LOCALES

ROOT = Path(__file__).resolve().parents[1]


def _postgres_reachable() -> bool:
    """FirstRun/Compose need the app's Postgres; CI provides it as a service."""
    import socket

    try:
        with socket.create_connection(("127.0.0.1", 5432), timeout=0.5):
            return True
    except OSError:
        return False


EXAMPLE_KEYS = (
    "examples.hint",
    "examples.compare.label",
    "examples.compare.text",
    "examples.paper.label",
    "examples.paper.text",
    "examples.email.label",
    "examples.email.text",
)
TERMS_KEYS = (
    "terms.duty.h",
    "terms.duty",
    "terms.prohibit.h",
    "terms.prohibit",
    "terms.prohibit.violence",
    "terms.prohibit.hate",
    "terms.prohibit.illegal",
    "terms.prohibit.deceive",
    "terms.prohibit.rights",
    "terms.providers.h",
    "terms.providers",
    "privacy.terms.note",
    "terms.advice.h",
    "terms.advice",
    "terms.verify.h",
    "terms.verify",
    "terms.accuracy.h",
    "terms.accuracy",
    "terms.use.h",
    "terms.use",
    "terms.third.h",
    "terms.third",
    "terms.indemnity.h",
    "terms.indemnity",
    "disclaimer.title",
    "disclaimer.body",
    "disclaimer.ack",
    "disclaimer.terms",
    "disclaimer.banner",
    "audit.disclaimer.h",
    "audit.disclaimer",
    "privacy.privilege.h",
    "privacy.privilege",
    "landing.disclaimer.title",
    "landing.disclaimer.body",
    "landing.disclaimer.link",
)
LAUNCH_KEYS = (
    "nav.library",
    "preview.title",
    "preview.empty",
    "intent.auto",
    "trust.verified",
    "trust.review",
    "refine",
    "recent.title",
    "recent.all",
    "header.privacy",
    "go.copy.hint",
)


class CatalogTests(unittest.TestCase):
    def test_example_keys_in_every_locale(self):
        for locale in LOCALES:
            cat = CATALOGS[locale]
            for key in EXAMPLE_KEYS:
                self.assertIn(key, cat)
                self.assertTrue(str(cat[key]).strip(), msg=f"{locale} {key}")

    def test_turkish_uses_soru_not_sorun(self):
        tr = CATALOGS["tr"]
        self.assertIn("soru", tr["examples.hint"])
        self.assertNotIn("sorun", tr["examples.hint"])
        self.assertIn("soru", tr["examples.label"])
        self.assertIn("healthcare", CATALOGS["en"]["examples.compare.text"])
        for locale in LOCALES:
            self.assertIn("nav.terms", CATALOGS[locale])
            self.assertIn("terms.title", CATALOGS[locale])
            for key in TERMS_KEYS:
                self.assertIn(key, CATALOGS[locale], msg=f"{locale} {key}")
                self.assertTrue(str(CATALOGS[locale][key]).strip(), msg=f"{locale} {key}")
        for locale in LOCALES:
            for key in LAUNCH_KEYS:
                self.assertIn(key, CATALOGS[locale], msg=f"{locale} {key}")
                self.assertTrue(str(CATALOGS[locale][key]).strip(), msg=f"{locale} {key}")
        self.assertNotIn("OpenAI", CATALOGS["en"]["terms.providers"])

    def test_intent_research_copy(self):
        self.assertIn("structures your question", CATALOGS["en"]["intent.research"])
        self.assertNotIn("sorun", CATALOGS["tr"]["intent.research"])
        self.assertIn("soru", CATALOGS["tr"]["intent.research"])


class ComposeMarkupTests(unittest.TestCase):
    def test_workbench_shell_in_index(self):
        html = (ROOT / "prompt_matrix" / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="workbench-root"', html)
        self.assertIn('id="panel-draft"', html)
        self.assertIn('id="generate-compile-btn"', html)
        self.assertIn('data-tool="projects"', html)
        self.assertIn("data-tooltip=", html)
        self.assertIn('id="task"', html)
        self.assertIn('id="live-preview"', html)
        self.assertIn('id="intent-chips"', html)
        self.assertIn('id="refine-answer"', html)
        self.assertNotIn('id="tour"', html)
        self.assertNotIn("assure.tour.v1", html)
        self.assertNotIn('id="feedback-yes"', html)


class LandingTests(unittest.TestCase):
    def test_install_scripts_exist(self):
        sh = ROOT / "scripts" / "install.sh"
        ps = ROOT / "scripts" / "install.ps1"
        self.assertTrue(sh.is_file())
        self.assertTrue(ps.is_file())
        desktop = ROOT / "scripts" / "build-desktop.sh"
        spec = ROOT / "packaging" / "assure.spec"
        self.assertTrue(desktop.is_file())
        spec_text = spec.read_text(encoding="utf-8")
        self.assertIn("desktop.py", spec_text)
        self.assertIn("BUNDLE", spec_text)
        text = sh.read_text(encoding="utf-8")
        self.assertIn("pip install -e", text)
        self.assertIn("prompt_matrix/.venv", text)
        self.assertIn("orhgor/assure", text)

    def test_readme_uses_install_script(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("./scripts/install.sh", readme)
        self.assertIn("build-desktop.sh", readme)
        self.assertNotIn("cd prompt_matrix", readme)

class WorkflowTests(unittest.TestCase):
    def test_ci_yml_runs_pytest_and_playwright(self):
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("pytest", ci)
        self.assertIn("test_z3_benchmark.py", ci)
        self.assertIn("Run Z3-heavy tests serially", ci)
        self.assertIn("playwright-tests", ci)
        self.assertIn("tests/playwright/", ci)
        self.assertNotIn("openuser", ci)
        self.assertFalse((ROOT / ".github" / "workflows" / "ux-tests.yml").exists())

    def test_deploy_workflows_exist(self):
        staging = (ROOT / ".github" / "workflows" / "deploy-staging.yml").read_text(
            encoding="utf-8"
        )
        production = (ROOT / ".github" / "workflows" / "deploy-production.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("branches: [staging]", staging)
        self.assertIn("workflow_dispatch:", production)
        self.assertNotIn("branches: [main]", production)
        self.assertIn("ssm-redeploy-and-wait.sh", staging)
        self.assertIn("ssm-redeploy-and-wait.sh", production)
        self.assertIn("AWS_ACCESS_KEY_ID", staging)
        self.assertIn("ASSURE_INSTANCE_ID", production)


class ExecuteCopyTests(unittest.TestCase):
    def test_copy_false_skips_clipboard(self):
        from unittest.mock import patch

        from prompt_matrix.engine import execute

        with patch("prompt_matrix.engine.copy_to_clipboard") as mocked:
            result = execute("gemini", "analysis", "Summarize", copy=False, direct=False)
        mocked.assert_not_called()
        self.assertTrue(result.rendered.prompt)
        self.assertFalse(result.copied)


@unittest.skipUnless(_postgres_reachable(), "workbench tests need Postgres (CI provides it as a service)")
class ComposePageTests(unittest.TestCase):
    def setUp(self):
        self._clerk = patch.dict(
            os.environ,
            {"CLERK_PUBLISHABLE_KEY": "", "CLERK_SECRET_KEY": ""},
            clear=False,
        )
        self._clerk.start()
        self.addCleanup(self._clerk.stop)

    def test_compose_renders_workbench(self):
        from prompt_matrix.web import create_app

        app = create_app()
        client = app.test_client()
        token = base64.b64encode(b"admin:changeme").decode("ascii")
        res = client.get("/app", headers={"Authorization": f"Basic {token}"})
        self.assertEqual(res.status_code, 200)
        body = res.get_data(as_text=True)
        self.assertIn("jdf-workbench", body)
        self.assertIn("panel-draft", body)
        self.assertIn("generate-compile-btn", body)
        self.assertIn("Prompt Library", body)
        self.assertIn("Refine this answer", body)
        self.assertNotIn('id="tour"', body)
        self.assertNotIn("assure.tour.v1", body)

    def test_intent_and_preview_api(self):
        from prompt_matrix.web import create_app

        app = create_app()
        client = app.test_client()
        token = base64.b64encode(b"admin:changeme").decode("ascii")
        headers = {"Authorization": f"Basic {token}", "Content-Type": "application/json"}
        intent = client.post("/api/intent", headers=headers, data='{"task":"Compare AWS vs GCP"}')
        self.assertEqual(intent.status_code, 200)
        self.assertEqual(intent.get_json()["intent"], "comparison")
        preview = client.post(
            "/api/preview",
            headers=headers,
            data='{"target_ai":"gemini","task":"Compare AWS vs GCP","intent":"auto"}',
        )
        self.assertEqual(preview.status_code, 200)
        data = preview.get_json()
        self.assertEqual(data["intent"], "comparison")
        self.assertTrue(data["prompt"])

    def test_terms_page_renders(self):
        from prompt_matrix.web import create_app

        app = create_app()
        client = app.test_client()
        token = base64.b64encode(b"admin:changeme").decode("ascii")
        res = client.get("/terms", headers={"Authorization": f"Basic {token}"})
        self.assertEqual(res.status_code, 200)
        body = res.get_data(as_text=True)
        self.assertIn("Terms of use", body)
        self.assertIn("getassureai.com", body)
        self.assertIn("Your responsibility", body)
        self.assertIn("Prohibited uses", body)
        self.assertIn("Violence, threats, or harassment", body)
        self.assertIn("Provider policies", body)
        self.assertIn("without warranties", body)
        self.assertIn("Not professional advice", body)
        self.assertIn("Human review", body)
        self.assertIn("Liability", body)
        self.assertNotIn("OpenAI", body)


@unittest.skipUnless(_postgres_reachable(), "workbench tests need Postgres (CI provides it as a service)")
class FirstRunTests(unittest.TestCase):
    def test_first_open_connect_when_no_provider(self):
        from unittest.mock import patch

        from prompt_matrix.web import first_open_url, lan_bind_with_default_password

        with patch("prompt_matrix.web.send_ready", return_value=False):
            self.assertEqual(
                first_open_url("http://127.0.0.1:8765"), "http://127.0.0.1:8765/connect"
            )
        with patch("prompt_matrix.web.send_ready", return_value=True):
            self.assertEqual(first_open_url("http://127.0.0.1:8765"), "http://127.0.0.1:8765")

    def test_lan_bind_warns_on_default_password(self):
        from unittest.mock import patch

        from prompt_matrix.web import lan_bind_with_default_password

        with patch.dict("os.environ", {"PEM_HTTP_PASS": "changeme"}, clear=False):
            self.assertTrue(lan_bind_with_default_password("0.0.0.0"))
            self.assertFalse(lan_bind_with_default_password("127.0.0.1"))
        with patch.dict("os.environ", {"PEM_HTTP_PASS": "secret-pass"}, clear=False):
            self.assertFalse(lan_bind_with_default_password("0.0.0.0"))

    def test_loopback_skips_sign_in_by_default(self):
        from unittest.mock import patch

        from prompt_matrix.web import create_app, http_auth_required, startup_lines

        with patch.dict("os.environ", {"PEM_HTTP_PASS": "changeme"}, clear=False):
            self.assertFalse(http_auth_required("127.0.0.1"))
            self.assertTrue(http_auth_required("0.0.0.0"))
        with patch.dict("os.environ", {"PEM_HTTP_PASS": "secret-pass"}, clear=False):
            self.assertTrue(http_auth_required("127.0.0.1"))

        app = create_app(require_auth=False)
        with patch.dict(
            os.environ, {"CLERK_PUBLISHABLE_KEY": "", "CLERK_SECRET_KEY": ""}, clear=False
        ):
            res = app.test_client().get("/")
        self.assertEqual(res.status_code, 200)

        locked = create_app(require_auth=True)
        denied = locked.test_client().get("/app")
        self.assertEqual(denied.status_code, 401)

        text = "\n".join(
            startup_lines(
                local="http://127.0.0.1:8765",
                host="127.0.0.1",
                send_ready_now=False,
                auth_on=False,
                user="admin",
            )
        )
        self.assertIn("Your browser should open", text)
        self.assertIn("paste a provider key", text)
        self.assertNotIn("changeme", text)
        self.assertNotIn("HTTP Basic", text)


class CacheAndDocsTests(unittest.TestCase):
    def test_cache_versions_unified(self):
        from prompt_matrix.ui_cache import APP_CSS, LANDING_CSS, LANDING_JS

        self.assertTrue(APP_CSS.startswith("assure-"))
        self.assertTrue(LANDING_CSS.isdigit())
        self.assertTrue(LANDING_JS.isdigit())
        base = (ROOT / "prompt_matrix" / "templates" / "base.html").read_text(encoding="utf-8")
        self.assertIn("css_version", base)
        self.assertNotIn("?v=assure-29", base)
        self.assertIn("privacy-chip", base)
        css = (ROOT / "prompt_matrix" / "static" / "style.css").read_text(encoding="utf-8")
        self.assertIn("@media (max-width: 640px)", css)
        self.assertIn("max-height: 70vh", css)
        self.assertIn("#history-compare", css)
        landing = ROOT / "prompt_matrix" / "templates" / "landing.html"
        if landing.is_file():
            html = landing.read_text(encoding="utf-8")
            if "landing.css?" in html:
                self.assertIn(f"landing.css?v={LANDING_CSS}", html)
    def test_pem_quick_ref_matches_compose(self):
        pem = (ROOT / "prompt_matrix" / "PEM.md").read_text(encoding="utf-8")
        self.assertIn("Assure detects what you need (Auto intent)", pem)
        self.assertNotIn("3-step tour on the first Compose visit", pem)
        self.assertNotIn("thumbs under the answer", pem)
        self.assertIn("Live prompt preview that teaches by doing", pem)
        self.assertIn("Ground runs automatically when you attach a file", pem)


class DesktopPackagingTests(unittest.TestCase):
    def test_resource_dir_is_package(self):
        from prompt_matrix.paths import resource_dir, user_data_dir

        self.assertTrue((resource_dir() / "config.json").is_file())
        self.assertEqual(user_data_dir(), resource_dir())

    def test_frozen_user_data_is_home(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        from prompt_matrix import paths

        with tempfile.TemporaryDirectory() as tmp:
            fake_home = Path(tmp) / "home"
            fake_home.mkdir()
            with (
                patch.object(paths.sys, "frozen", True, create=True),
                patch.object(paths.Path, "home", return_value=fake_home),
            ):
                d = paths.user_data_dir()
        self.assertEqual(d, fake_home / ".assure")

    def test_desktop_entry_imports(self):
        from prompt_matrix.desktop import main

        self.assertTrue(callable(main))


if __name__ == "__main__":
    unittest.main()
