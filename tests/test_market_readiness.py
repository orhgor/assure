"""Market readiness: example chips, catalogs, landing, CI file. No live Send."""

from __future__ import annotations

import base64
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from prompt_matrix.i18n import CATALOGS, LOCALES

ROOT = Path(__file__).resolve().parents[1]
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


class ComposeMarkupTests(unittest.TestCase):
    def test_example_chips_in_index(self):
        html = (ROOT / "prompt_matrix" / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="task"', html)
        self.assertIn('class="example-chip"', html)
        self.assertIn('data-example="compare"', html)
        self.assertIn('data-example="paper"', html)
        self.assertIn('data-example="email"', html)
        self.assertIn("function applyExample", html)
        self.assertIn('id="live-preview"', html)
        self.assertIn('id="intent-chips"', html)
        self.assertIn('id="refine-answer"', html)
        self.assertNotIn('id="tour"', html)
        self.assertNotIn("assure.tour.v1", html)
        self.assertNotIn('id="feedback-yes"', html)


class LandingTests(unittest.TestCase):
    def test_quick_start_and_use_cases(self):
        html = (ROOT / "landing" / "index.html").read_text(encoding="utf-8")
        self.assertIn("Quick start", html)
        self.assertIn("Download for macOS", html)
        self.assertIn("Download for Windows", html)
        self.assertIn("Linux package", html)
        self.assertIn("Verify AI Drafts Against Your Source", html)
        self.assertIn("Launch App", html)
        self.assertIn("https://app.getassureai.com", html)
        self.assertNotIn("Talk to AI like you talk to a", html)
        self.assertNotIn("hero-brand", html)
        self.assertNotIn("OpenAlex", html)
        self.assertNotIn("bibliography auditing", html)
        self.assertIn("Your browser should open", html)
        self.assertIn("http://127.0.0.1:8765", html)
        self.assertNotIn("changeme", html)
        self.assertIn("assure --web", html)
        self.assertIn("./scripts/install.sh", html)
        self.assertNotIn("2 minutes", html)
        self.assertIn("Document grounding", html)
        self.assertIn("Multi-model consensus", html)
        self.assertIn("Visual trust signals", html)
        self.assertIn("Privacy-first BYOK", html)
        self.assertIn("sentence-level NLI", html)
        self.assertIn("Private by", html)
        self.assertIn("The desktop app is coming", html)
        self.assertIn("Is Assure just another AI chatbot?", html)
        self.assertIn("$19", html)
        self.assertIn("5 checks / day", html)
        self.assertNotIn("Choose your AI and context", html)
        self.assertNotIn("Choose what you need: quick, validated, or refined", html)
        self.assertIn("Do I pick Quick, Validated, or Refined first?", html)
        self.assertNotIn("perfect prompt", html)
        self.assertNotIn("SSO", html)
        self.assertNotIn("Contact Sales", html)
        self.assertNotIn("never leaves your machine", html)
        self.assertIn("getassureai.com", html)
        css = (ROOT / "landing" / "assets" / "site.css").read_text(encoding="utf-8")
        js = (ROOT / "landing" / "assets" / "site.js").read_text(encoding="utf-8")
        self.assertIn("--accent: #FF6B35", css)
        self.assertNotIn("Trusted by", html)
        self.assertIn("IntersectionObserver", js)
        self.assertIn("site.css?v=30", html)
        self.assertIn("hallucination-detection.html", html)
        self.assertIn("localStorage", html)
        self.assertIn("There is no public download file yet", html)
        self.assertIn('data-download-macos=""', html)
        install = (ROOT / "landing" / "install.html").read_text(encoding="utf-8")
        self.assertIn("The app opens", install)
        self.assertIn("not on PyPI yet", install)
        self.assertIn("build-desktop.sh", install)
        self.assertIn("orhgor/assure", install)

    def test_install_scripts_exist(self):
        sh = ROOT / "scripts" / "install.sh"
        ps = ROOT / "scripts" / "install.ps1"
        sync = ROOT / "scripts" / "sync-webpage.sh"
        self.assertTrue(sh.is_file())
        self.assertTrue(ps.is_file())
        self.assertTrue(sync.is_file())
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
        self.assertIn("sync-webpage.sh", readme)
        self.assertNotIn("cd prompt_matrix", readme)

    def test_use_case_pages(self):
        for name in ("consultant", "researcher", "analyst"):
            path = ROOT / "landing" / "use-cases" / f"{name}.html"
            self.assertTrue(path.is_file(), msg=str(path))
            text = path.read_text(encoding="utf-8")
            self.assertIn("Pain", text)
            self.assertIn("Solution", text)
            self.assertIn("Outcome", text)
            self.assertIn("Open Compose", text)
        hall = (ROOT / "landing" / "hallucination-detection.html").read_text(encoding="utf-8")
        self.assertIn("Check against my files", hall)
        self.assertIn("Compare", hall)
        self.assertNotIn("never leaves your machine", hall)
        self.assertNotIn("$1.46T", hall)
        sitemap = (ROOT / "landing" / "sitemap.xml").read_text(encoding="utf-8")
        self.assertIn("hallucination-detection.html", sitemap)
        self.assertIn("Not installed yet", text)

    def test_pricing_two_tiers(self):
        html = (ROOT / "landing" / "pricing.html").read_text(encoding="utf-8")
        self.assertIn("$5", html)
        self.assertNotIn("$9", html)
        self.assertIn("Self-serve", html)
        self.assertIn("ASSURE_EDITION=team", html)
        self.assertNotIn("Contact Sales", html)
        self.assertIn("pricing-two", html)
        self.assertNotIn('class="plan-name">Team', html)
        self.assertNotIn("Contact Sales", html)
        self.assertIn("You bring your own API keys", html)
        self.assertIn("billed directly", html)
        self.assertIn("Do I need my own API keys?", html)

    def test_byok_pricing_transparency(self):
        index = (ROOT / "landing" / "index.html").read_text(encoding="utf-8")
        pricing = (ROOT / "landing" / "pricing.html").read_text(encoding="utf-8")
        for html in (index, pricing):
            self.assertIn("You bring your own API keys", html, msg="BYOK missing")
            self.assertIn("billed directly", html, msg="provider billing missing")
            self.assertIn("workbench, not the models", html, msg="workbench vs models missing")
        self.assertIn("Do I need my own API keys?", index)
        self.assertIn("Is Assure open-source?", index)
        self.assertIn("open-source prompt tools", index)
        self.assertIn("footer-oss", index)
        self.assertIn("PEM, LiteLLM, Jinja2", index)
        self.assertIn("still pay the provider", index)

    def test_icp_and_objections_exist(self):
        icp = (ROOT / "landing" / "ICP.md").read_text(encoding="utf-8")
        self.assertIn("Lena", icp)
        self.assertIn("Marek", icp)
        self.assertIn("Priya", icp)
        self.assertIn("The Sovereign Analyst", icp)
        self.assertIn("The Privacy-First Researcher", icp)
        self.assertIn("The Prompt Reluctant Professional", icp)
        self.assertIn("Compliance Officer", icp)
        self.assertIn("PromptLayer", icp)
        self.assertIn("structures your question", CATALOGS["en"]["intent.research"])
        self.assertNotIn("sorun", CATALOGS["tr"]["intent.research"])
        self.assertIn("soru", CATALOGS["tr"]["intent.research"])
        obj = (ROOT / "landing" / "objections.md").read_text(encoding="utf-8")
        self.assertIn("Why not just use ChatGPT?", obj)
        self.assertIn("Do not say", obj)

    def test_terms_and_domain(self):
        self.assertEqual((ROOT / "landing" / "CNAME").read_text(encoding="utf-8").strip(), "getassureai.com")
        self.assertIn("getassureai.com", (ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        terms = (ROOT / "landing" / "terms.html").read_text(encoding="utf-8")
        self.assertIn("Answers can be wrong", terms)
        self.assertIn("Your responsibility", terms)
        self.assertIn("Prohibited uses", terms)
        self.assertIn("Violence, threats, or harassment", terms)
        self.assertIn("Provider policies", terms)
        self.assertIn("without warranties", terms)
        self.assertIn("getassureai.com", terms)
        self.assertNotIn("assure.ai", terms)
        self.assertNotIn("OpenAI", terms)
        self.assertIn("Quick start", (ROOT / "README.md").read_text(encoding="utf-8"))


class WorkflowTests(unittest.TestCase):
    def test_ci_yml_runs_pem(self):
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("pem --ci", text)
        self.assertIn("pem eval", text)
        self.assertIn("python -m unittest", text)
        self.assertNotIn("pytest", text)


class ExecuteCopyTests(unittest.TestCase):
    def test_copy_false_skips_clipboard(self):
        from unittest.mock import patch

        from prompt_matrix.engine import execute

        with patch("prompt_matrix.engine.copy_to_clipboard") as mocked:
            result = execute("gemini", "analysis", "Summarize", copy=False, direct=False)
        mocked.assert_not_called()
        self.assertTrue(result.rendered.prompt)
        self.assertFalse(result.copied)


class ComposePageTests(unittest.TestCase):
    def setUp(self):
        self._clerk = patch.dict(
            os.environ,
            {"CLERK_PUBLISHABLE_KEY": "", "CLERK_SECRET_KEY": ""},
            clear=False,
        )
        self._clerk.start()
        self.addCleanup(self._clerk.stop)

    def test_compose_renders_chips(self):
        from prompt_matrix.web import create_app

        app = create_app()
        client = app.test_client()
        token = base64.b64encode(b"admin:changeme").decode("ascii")
        res = client.get("/app", headers={"Authorization": f"Basic {token}"})
        self.assertEqual(res.status_code, 200)
        body = res.get_data(as_text=True)
        self.assertIn("example-chip", body)
        self.assertIn("Compare AWS vs GCP", body)
        self.assertIn("Prompt Library", body)
        self.assertIn("What Assure will send", body)
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
        self.assertNotIn("OpenAI", body)


class FirstRunTests(unittest.TestCase):
    def test_first_open_connect_when_no_provider(self):
        from unittest.mock import patch

        from prompt_matrix.web import first_open_url, lan_bind_with_default_password

        with patch("prompt_matrix.web.send_ready", return_value=False):
            self.assertEqual(first_open_url("http://127.0.0.1:8765"), "http://127.0.0.1:8765/connect")
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
        with patch.dict(os.environ, {"CLERK_PUBLISHABLE_KEY": "", "CLERK_SECRET_KEY": ""}, clear=False):
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

        self.assertEqual(APP_CSS, "assure-42")
        self.assertEqual(LANDING_CSS, "30")
        self.assertEqual(LANDING_JS, "29")
        base = (ROOT / "prompt_matrix" / "templates" / "base.html").read_text(encoding="utf-8")
        self.assertIn("css_version", base)
        self.assertNotIn("?v=assure-29", base)
        self.assertIn("privacy-chip", base)
        css = (ROOT / "prompt_matrix" / "static" / "style.css").read_text(encoding="utf-8")
        self.assertIn("@media (max-width: 640px)", css)
        self.assertIn("max-height: 70vh", css)
        self.assertIn("#history-compare", css)
        for path in (ROOT / "landing").rglob("*.html"):
            html = path.read_text(encoding="utf-8")
            if "site.css?" in html:
                self.assertIn(f"site.css?v={LANDING_CSS}", html, msg=str(path))
            if "site.js?" in html:
                self.assertIn(f"site.js?v={LANDING_JS}", html, msg=str(path))

    def test_analytics_tag_on_every_page_and_disclosed(self):
        pages = sorted((ROOT / "landing").rglob("*.html"))
        self.assertGreaterEqual(len(pages), 12)
        for path in pages:
            html = path.read_text(encoding="utf-8")
            head = html.split("</head>", 1)[0]
            self.assertIn("googletagmanager.com/gtag/js?id=G-54F5NE9Y0P", head, msg=str(path))
            self.assertIn("gtag('config', 'G-54F5NE9Y0P')", head, msg=str(path))
        # A tracker on the site has to stay disclosed on the privacy page.
        privacy = (ROOT / "landing" / "privacy.html").read_text(encoding="utf-8")
        self.assertIn("Google Analytics", privacy)
        self.assertIn("G-54F5NE9Y0P", privacy)
        self.assertIn("never sent to Google", privacy)

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
