"""Smoke tests for static marketing export."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RenderStaticTests(unittest.TestCase):
    def test_build_marketing_static_produces_en_home(self):
        env = os.environ.copy()
        env.setdefault("ASSURE_REQUIRE_LOGIN", "false")
        env.setdefault("PEM_HTTP_PASS", "")
        env.setdefault("CLERK_PUBLISHABLE_KEY", "")
        env.setdefault("CLERK_SECRET_KEY", "")
        dist = ROOT / "dist-test-render"
        env["DIST_DIR"] = str(dist)
        subprocess.run(
            ["bash", str(ROOT / "scripts/build-marketing-static.sh")],
            cwd=ROOT,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        index = dist / "index.html"
        self.assertTrue(index.is_file())
        html = index.read_text(encoding="utf-8")
        # Current brand hero (i18n.py brand.hero_title) — the copy was
        # rebranded from "Draft at the speed of AI…" deliberately.
        self.assertIn("Zero hallucination. Absolute verification.", html)
        self.assertIn("https://app.getassureai.com/app", html)
        self.assertIn("__ASSURE_APP_ORIGIN", html)
        self.assertIn("/static/landing.css", html)
        tr_index = dist / "tr" / "index.html"
        self.assertTrue(tr_index.is_file())
        tr_html = tr_index.read_text(encoding="utf-8")
        self.assertIn("Sıfır halüsinasyon. Mutlak doğrulama.", tr_html)


if __name__ == "__main__":
    unittest.main()
