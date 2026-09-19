"""Local DOM-replay server for Phase 2.

Serves the box's own shell (prototype/index.html + prototype/shell.js, md5
90d14ae96a497d76b046413fb20b5720 — byte-identical to what the box serves) as static
files, and answers /api/* from a stub whose only real behaviour is
POST /api/projects/<id>/draft/stream: it replays a recorded frame script
(/tmp/domreplay/replay.json, case A or B) with the recorded arrival times.

Nothing here touches the box or any provider. Usage: server.py <port> <case>
"""
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = "/tmp/domreplay"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8990
CASE = sys.argv[2] if len(sys.argv) > 2 else "A"
REPLAY = json.load(open(os.path.join(ROOT, "replay.json")))
FRAMES = REPLAY[CASE]

STATIC = {"/": "index.html", "/index.html": "index.html", "/shell.js": "shell.js",
          "/shell.css": "shell.css", "/wow_effects.js": "wow_effects.js", "/favicon.svg": "favicon.svg"}


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "AssureDomReplay/1.0"

    def log_message(self, fmt, *args):
        return

    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _static(self, name):
        p = os.path.join(ROOT, name)
        if not os.path.isfile(p):
            self.send_error(404)
            return
        b = open(p, "rb").read()
        ct = ("text/html" if name.endswith(".html") else
              "text/css" if name.endswith(".css") else
              "image/svg+xml" if name.endswith(".svg") else "application/javascript")
        self.send_response(200)
        self.send_header("Content-Type", ct + "; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _stub_api(self, path, method):
        if path.endswith("/substrate"):
            return {"files": [{"id": "edge-3ef1602e22bd441a", "filename": "mhci-rate-decision.pdf",
                               "included": True, "fetched_url": None}]}
        if path.endswith("/auth/config"):
            return {"clerk_only": False}
        if path.endswith("/versions") or path.endswith("/revisions"):
            return {"versions": [], "current": None, "list": []}
        if path.endswith("/compile-system"):
            return {"prompt": "**CASE AND DOMAIN:**\nreplay", "shape": "memo"}
        if path == "/api/projects" and method == "POST":
            return {"id": "audit-dom-replay", "title": "workspace"}
        if path == "/api/projects" and method == "GET":
            return {"projects": [{"id": "audit-dom-replay", "title": "workspace"}]}
        if "/api/projects/" in path:
            return {"id": "audit-dom-replay", "title": "workspace", "document": None}
        return {}

    def _stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        t0 = time.time()
        for f in FRAMES:
            dt = f["t"] - (time.time() - t0)
            if dt > 0:
                time.sleep(dt)
            data = f["text"].encode("utf-8")
            self.wfile.write(b"%x\r\n" % len(data) + data + b"\r\n")
            self.wfile.flush()
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def _drain_body(self):
        """Consume the request body. Leaving it unread corrupts the next request
        on a keep-alive connection (the body is then parsed as a request line)."""
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n > 0:
            try:
                self.rfile.read(n)
            except Exception:
                pass

    def _route(self, method):
        self._drain_body()
        path = self.path.split("?", 1)[0] or "/"
        if path in STATIC:
            return self._static(STATIC[path])
        if method == "POST" and path.endswith("/draft/stream"):
            return self._stream()
        if path.startswith("/api/"):
            return self._json(self._stub_api(path, method))
        self.send_error(404)

    do_GET = lambda s: s._route("GET")
    do_POST = lambda s: s._route("POST")
    do_PUT = lambda s: s._route("PUT")
    do_DELETE = lambda s: s._route("DELETE")


if __name__ == "__main__":
    print(f"replay case={CASE} port={PORT} frames={len(FRAMES)} ends_t={FRAMES[-1]['t']}s", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
