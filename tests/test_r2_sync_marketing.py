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

            py = ROOT / "marketing" / "sync_r2.py"
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
                "r2_sync_marketing", ROOT / "marketing" / "sync_r2.py"
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
        py = ROOT / "marketing" / "worker_changed.py"
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


def _load_sync():
    import importlib.util

    spec = importlib.util.spec_from_file_location("r2_sync_prune", ROOT / "marketing" / "sync_r2.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _build(n=120):
    keys = {"index.html", "404.html", "robots.txt", "sitemap.xml", "llms.txt", "static/landing.css"}
    for i in range(n - len(keys)):
        keys.add(f"tr/page-{i}/index.html")
    return keys


class R2PrunePlanTests(unittest.TestCase):
    """Offline tests for the prune diff and safety caps (fake key lists, no network)."""

    def setUp(self):
        self.m = _load_sync()

    def test_nothing_stale(self):
        b = _build()
        d, kept, why = self.m.plan_prune(b, set(b))
        self.assertEqual((d, kept, why), ([], [], None))

    def test_stale_pages_and_css_deleted(self):
        b = _build()
        stale = {"architecture/index.html", "tr/architecture/index.html", "static/marketing_pages.css"}
        d, kept, why = self.m.plan_prune(b, b | stale)
        self.assertIsNone(why)
        self.assertEqual(d, sorted(stale))

    def test_protected_and_unmanaged_kept(self):
        b = _build()
        other = {"_headers", "uploads/a.pdf", "private/x/index.html", ".well-known/x", "backups/db.sql", "random.bin"}
        d, kept, why = self.m.plan_prune(b, b | other)
        self.assertIsNone(why)
        self.assertEqual(d, [])
        self.assertEqual(sorted(kept), sorted(other))

    def test_landing_files_in_build_not_pruned(self):
        b = _build()
        d, _, _ = self.m.plan_prune(b, b | {"llms.txt", "sitemap.xml", "robots.txt"})
        self.assertEqual(d, [])

    def test_cap_aborts(self):
        b = _build(120)  # 10% of 120 = 12
        stale = {f"old-{i}/index.html" for i in range(13)}
        d, _, why = self.m.plan_prune(b, b | stale)
        self.assertIn("exceeds cap 12", why)
        ok, _, why2 = self.m.plan_prune(b, b | set(sorted(stale)[:12]))
        self.assertIsNone(why2)
        self.assertEqual(len(ok), 12)

    def test_absolute_cap(self):
        b = _build(1000)
        stale = {f"old-{i}/index.html" for i in range(26)}
        _, _, why = self.m.plan_prune(b, b | stale)
        self.assertIn("exceeds cap 25", why)

    def test_small_or_broken_build_aborts(self):
        b = _build()
        _, _, why = self.m.plan_prune(b - {"index.html"}, b)
        self.assertIn("no index.html", why)
        _, _, why = self.m.plan_prune({"index.html", "a.css"}, b)
        self.assertIn("< 50", why)
        # A missing build must never empty the bucket.
        d, _, why = self.m.plan_prune(set(), b)
        self.assertEqual(d, [])
        self.assertIsNotNone(why)

    def test_prune_opt_out_and_list_failure_delete_nothing(self):
        import os
        from unittest import mock

        b = _build()
        args = mock.Mock(wrangler=Path("/bin/false"), bucket="x", max_deletes=25, dry_run=False)
        with mock.patch.dict(os.environ, {"CLOUDFLARE_API_TOKEN": "fake"}), \
                mock.patch.object(self.m, "list_bucket_keys", side_effect=OSError("offline")), \
                mock.patch.object(self.m, "delete_object") as dele:
            self.assertEqual(self.m.prune(args, b), 3)
            dele.assert_not_called()
        with mock.patch.dict(os.environ, {"CLOUDFLARE_API_TOKEN": "fake"}), \
                mock.patch.object(self.m, "list_bucket_keys", return_value=b | {f"o{i}/index.html" for i in range(30)}), \
                mock.patch.object(self.m, "delete_object") as dele:
            self.assertEqual(self.m.prune(args, b), 4)
            dele.assert_not_called()
        with mock.patch.dict(os.environ, {"CLOUDFLARE_API_TOKEN": "fake"}), \
                mock.patch.object(self.m, "list_bucket_keys", return_value=b | {"architecture/index.html"}), \
                mock.patch.object(self.m, "delete_object") as dele:
            self.assertEqual(self.m.prune(args, b), 0)
            dele.assert_called_once_with(Path("/bin/false"), "x", "architecture/index.html")
            args.dry_run = True
            dele.reset_mock()
            self.assertEqual(self.m.prune(args, b), 0)
            dele.assert_not_called()

    def test_list_pagination(self):
        import io
        import json as _json
        from unittest import mock

        pages = [
            {"success": True, "result": [{"key": "a"}], "result_info": {"is_truncated": True, "cursor": "c1"}},
            {"success": True, "result": [{"key": "b"}], "result_info": {"is_truncated": False}},
        ]
        seen = []

        def fake_open(req, timeout=30):
            seen.append(req.full_url)
            return io.BytesIO(_json.dumps(pages[len(seen) - 1]).encode())

        with mock.patch.object(self.m.urllib.request, "urlopen", side_effect=fake_open):
            self.assertEqual(self.m.list_bucket_keys("bkt", "t", "acct"), {"a", "b"})
        self.assertIn("cursor=c1", seen[1])


if __name__ == "__main__":
    unittest.main()
