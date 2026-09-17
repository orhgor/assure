#!/usr/bin/env python3
"""Local dev server for the shell prototype."""

from __future__ import annotations
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_ROOT = HERE
UPSTREAM_BASE = os.environ.get("UPSTREAM_BASE", "http://localhost:8899")
HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8990"))

FORWARD_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"}


class Handler(BaseHTTPRequestHandler):
    server_version = "AssureShellProxy/1.0"

    def log_message(self, fmt, *args):
        return

    def _serve_static(self):
        path = self.path.split("?", 1)[0] or "/index.html"
        if path == "/":
            path = "/index.html"
        fs_path = os.path.abspath(os.path.join(STATIC_ROOT, path.lstrip("/")))
        if not fs_path.startswith(os.path.abspath(STATIC_ROOT)):
            self.send_error(403, "Forbidden")
            return
        if not os.path.isfile(fs_path):
            self.send_error(404, "Not Found")
            return
        with open(fs_path, "rb") as fh:
            body = fh.read()
        ext = os.path.splitext(fs_path)[1].lower()
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".ico": "image/x-icon",
        }.get(ext, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _proxy(self, method):
        if method not in FORWARD_METHODS:
            self.send_error(405)
            return
        url = UPSTREAM_BASE + self.path
        cl = int(self.headers.get("Content-Length") or "0")
        body = self.rfile.read(cl) if cl > 0 else None
        hdrs = {}
        for k in (
            "Content-Type",
            "Accept",
            "Authorization",
            "Origin",
            "Cache-Control",
            "X-Requested-With",
        ):
            v = self.headers.get(k)
            if v:
                hdrs[k] = v
        req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=None) as resp:
                self.send_response(resp.status)
                seen = set()
                for k, v in resp.getheaders():
                    kl = k.lower()
                    if kl in (
                        "connection",
                        "transfer-encoding",
                        "content-encoding",
                        "keep-alive",
                        "upgrade",
                    ):
                        continue
                    if kl in seen:
                        continue
                    seen.add(kl)
                    self.send_header(k, v)
                self.end_headers()
                while True:
                    # read1 returns as soon as any bytes land; read() would block
                    # until 4 KB accumulate, so SSE frames never reached the edge.
                    chunk = resp.read1(4096)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            for k, v in e.headers.items() if hasattr(e.headers, "items") else []:
                if k.lower() in ("connection", "transfer-encoding", "content-encoding"):
                    continue
                self.send_header(k, v)
            self.end_headers()
            try:
                b = e.read()
            except Exception:
                b = b""
            if b:
                self.wfile.write(b)
        except urllib.error.URLError as e:
            msg = ("Upstream unavailable: %s" % (e.reason,)).encode()
            self.send_response(502)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)
        except BrokenPipeError:
            pass

    def _route(self, method):
        if self.path.startswith("/api/"):
            self._proxy(method)
        else:
            if method != "GET":
                self.send_error(405)
                return
            self._serve_static()

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_PUT(self):
        self._route("PUT")

    def do_DELETE(self):
        self._route("DELETE")

    def do_PATCH(self):
        self._route("PATCH")

    def do_HEAD(self):
        self._route("HEAD")

    def do_OPTIONS(self):
        self._route("OPTIONS")


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        print(
            "Dev server on http://localhost:{} (proxying /api/* to :8899)".format(PORT), flush=True
        )
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    sys.exit(main())
