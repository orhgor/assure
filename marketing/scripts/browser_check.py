#!/usr/bin/env python3
"""Browser checks for the marketing site.

Fails (exit 1) on: console errors, uncaught page errors, failed requests,
horizontal scroll at 360, 390 or 1440 px, and a mobile menu that does not
open in place.

Local run (serves dist/ on 127.0.0.1:8765; analytics hosts are ignored):
    .venv/bin/python marketing/scripts/browser_check.py
    .venv/bin/python marketing/scripts/browser_check.py --all
Production run (nothing ignored, the CSP applies):
    .venv/bin/python marketing/scripts/browser_check.py --base https://assureproof.com --all
Screenshots for review: add --shots /tmp/omp/shots/phaseNN
Setup once: .venv/bin/pip install playwright && .venv/bin/python -m playwright install chromium
"""
from __future__ import annotations

import argparse
import functools
import http.server
import socketserver
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "dist"
LOCAL_IGNORE = ("googletagmanager.com", "google-analytics.com", "analytics.google.com",
                "cloudflareinsights.com", "/cdn-cgi/")
LOCALES = ("es", "zh", "fr", "de", "ja", "tr", "ar")
WIDTHS = (360, 390, 1440)


def page_paths(all_pages: bool) -> list[str]:
    paths = sorted("/" + p.parent.relative_to(DIST).as_posix() + "/" for p in DIST.rglob("index.html"))
    paths = ["/" if p == "/./" else p for p in paths]
    if all_pages:
        return paths
    return [p for p in paths if p.split("/")[1] not in LOCALES] + [f"/{l}/" for l in LOCALES]


def serve() -> socketserver.TCPServer:
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = functools.partial(Quiet, directory=str(DIST))
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", 8765), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--shots", default="", help="folder for full-page screenshots (390 and 1440 px)")
    args = ap.parse_args()
    if not DIST.is_dir():
        print("dist/ missing: run marketing/build.sh first")
        return 1
    local = not args.base
    base = "http://127.0.0.1:8765" if local else args.base.rstrip("/")
    httpd = serve() if local else None
    ignore = LOCAL_IGNORE if local else ()
    problems: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for width in WIDTHS:
            ctx = browser.new_context(viewport={"width": width, "height": 900})
            for path in page_paths(args.all):
                page = ctx.new_page()
                found: list[str] = []
                page.on("console", lambda m: m.type == "error" and not any(h in m.text for h in ignore)
                        and found.append(f"console: {m.text[:200]}"))
                page.on("pageerror", lambda e: found.append(f"pageerror: {str(e)[:200]}"))
                page.on("requestfailed", lambda r: not any(h in r.url for h in ignore)
                        and found.append(f"requestfailed: {r.url} {r.failure}"))
                page.on("response", lambda r: r.status >= 400 and not any(h in r.url for h in ignore)
                        and found.append(f"http {r.status}: {r.url}"))
                page.goto(base + path, wait_until="load")
                page.wait_for_timeout(400)
                over = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
                if over > 1:
                    found.append(f"horizontal scroll {over}px")
                if width == 390 and page.is_visible("#mobile-menu-btn"):
                    before = page.url
                    page.click("#mobile-menu-btn")
                    page.wait_for_timeout(300)
                    if page.url != before:
                        found.append("menu button navigated away")
                    elif not page.evaluate("(() => { const m = document.getElementById('mobile-menu');"
                                           " return !!m && !m.hidden && m.offsetHeight > 0; })()"):
                        found.append("mobile menu did not open")
                    elif page.get_attribute("#mobile-menu-btn", "aria-expanded") != "true":
                        found.append("aria-expanded not true after opening")
                if args.shots and width in (390, 1440):
                    Path(args.shots).mkdir(parents=True, exist_ok=True)
                    name = (path.strip("/").replace("/", "_") or "home") + f"_{width}.png"
                    page.screenshot(path=str(Path(args.shots) / name), full_page=True)
                problems += [f"{width}px {path}: {f}" for f in found]
                page.close()
            ctx.close()
        browser.close()
    if httpd:
        httpd.shutdown()
    for p in problems:
        print(p)
    print(f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
