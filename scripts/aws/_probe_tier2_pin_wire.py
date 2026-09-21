#!/usr/bin/env python3
"""PROBE — read the provider pin off the wire for the Tier 2 translator call.

Not product code. Points the real translator at a local listener and prints the
request body litellm actually sent, so "the pin applies to this call" is a
captured fact rather than a reading of the source. No provider key is involved:
the request never leaves 127.0.0.1, and the key in the header is a placeholder.

usage: .venv/bin/python scripts/aws/_probe_tier2_pin_wire.py
"""

from __future__ import annotations

import http.server
import json
import os
import socketserver
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

CAPTURED: list[tuple[str, bytes]] = []

CLAIM_JSON = {
    "metric": "CPT code 90837 minimum face-to-face time",
    "operands": [
        {
            "name": "CPT_90837_minimum_face_to_face_psychotherapy_time_per_session",
            "value": 53,
            "unit": "minutes",
            "source_sentence": (
                "CPT code 90837 is used only when the documented face-to-face "
                "psychotherapy time is 53 minutes or longer."
            ),
        }
    ],
    "relation": "ge",
    "expected": 53,
}


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - http.server's spelling
        length = int(self.headers.get("Content-Length") or 0)
        CAPTURED.append((self.path, self.rfile.read(length)))
        payload = {
            "id": "gen-local-capture",
            "object": "chat.completion",
            "created": 1,
            "model": "capture",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": json.dumps(CLAIM_JSON)},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        return


def main() -> int:
    with socketserver.TCPServer(("127.0.0.1", 0), _Handler) as server:
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()

        os.environ["OPENROUTER_API_BASE"] = f"http://127.0.0.1:{port}/api/v1"
        os.environ["OPENROUTER_API_KEY"] = "sk-or-v1-local-capture-not-a-real-key"

        from prompt_matrix.services.relational_translate import (
            _governor_policy,
            _pinned_caller,
            build_translation_prompt,
        )

        model_id, litellm_model = _governor_policy()
        facts = {
            "CPT_90837_minimum_face_to_face_psychotherapy_time_per_session": 53.0,
            "CPT_90834_face_to_face_psychotherapy_time_range_per_session": 52.0,
        }
        prompt = build_translation_prompt(
            "CPT code 90837 is used only when the documented face-to-face psychotherapy "
            "time is 53 minutes or longer.",
            facts,
        )
        print(f"model_id={model_id}")
        print(f"litellm_model={litellm_model}")
        print(f"local_base=http://127.0.0.1:{port}/api/v1")
        try:
            raw = _pinned_caller(prompt, litellm_model)
        except Exception as exc:
            print(f"call failed: {type(exc).__name__}: {exc}")
            return 2

        print(f"response_len={len(raw)}")
        if not CAPTURED:
            print("HARD FAILURE: no request reached the local listener")
            return 1
        path, body = CAPTURED[-1]
        print(f"requests_captured={len(CAPTURED)} path={path}")
        parsed = json.loads(body.decode())
        print("--- captured request body (key redacted by construction) ---")
        for field in ("model", "temperature", "max_tokens", "provider"):
            print(f"  {field}: {json.dumps(parsed.get(field))}")
        provider = parsed.get("provider")
        ok = (
            isinstance(provider, dict)
            and provider.get("order") == ["Alibaba"]
            and provider.get("allow_fallbacks") is False
        )
        print(f"PIN_ON_WIRE={'SUCCESS' if ok else 'HARD FAILURE'}")
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
