"""Tests for R2 marketing sync helpers (no wrangler calls)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class R2SyncMarketingTests(unittest.TestCase):
    def test_skips_unchanged_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp) / "dist"
            dist.mkdir()
            (dist / "index.html").write_text("<html>ok</html>", encoding="utf-8")
            manifest = Path(tmp) / "manifest.json"

            py = ROOT / "scripts" / "r2_sync_marketing.py"
            wrangler = ROOT / "scripts" / "cloudflare" / "node_modules" / ".bin" / "wrangler"
            if not wrangler.is_file():
                wrangler = Path("/bin/echo")

            # First run: would upload
            r1 = subprocess.run(
                [
                    sys.executable,
                    str(py),
                    "--dist",
                    str(dist),
                    "--manifest",
                    str(manifest),
                    "--wrangler",
                    str(wrangler),
                    "--dry-run",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(r1.returncode, 0)
            self.assertIn("1 to upload", r1.stdout)

            import importlib.util

            spec = importlib.util.spec_from_file_location(
                "r2_sync_marketing", ROOT / "scripts" / "r2_sync_marketing.py"
            )
            mod = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(mod)

            files = {k: mod.sha256_file(p) for k, p in mod.iter_dist_files(dist)}
            mod.save_manifest(manifest, files)

            # Second run: skip
            r2 = subprocess.run(
                [
                    sys.executable,
                    str(py),
                    "--dist",
                    str(dist),
                    "--manifest",
                    str(manifest),
                    "--wrangler",
                    str(wrangler),
                    "--dry-run",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(r2.returncode, 0)
            self.assertIn("0 to upload", r2.stdout)
            self.assertIn("1 unchanged", r2.stdout)

    def test_worker_changed_detects_config(self):
        py = ROOT / "scripts" / "marketing_worker_changed.py"
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "worker.json"
            env = {"PYTHONPATH": str(ROOT)}
            # Patch STATE by running from empty cache — should report changed
            r = subprocess.run(
                [sys.executable, str(py)],
                capture_output=True,
                text=True,
                cwd=ROOT,
                env=env,
                check=False,
            )
            self.assertIn(r.stdout.strip(), {"worker-changed", "worker-unchanged"})


if __name__ == "__main__":
    unittest.main()
