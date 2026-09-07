"""Groundrails CLI — run inside Python 3.12 venv; JSON stdin/stdout."""

from __future__ import annotations

import json
import sys
from typing import Any


def _verify_claim(claim: str, source: str) -> dict[str, Any]:
    import groundrails

    try:
        groundrails.init()
    except Exception:
        pass

    doc = groundrails.grounding_document([claim], [("source.txt", source)])
    claims = doc.get("claims") if isinstance(doc, dict) else []
    if not claims:
        return {
            "verdict": "uncertain",
            "grounded": False,
            "score": 0.0,
            "passage": "",
            "engine": "groundrails-subprocess",
        }

    item = claims[0]
    grounded = bool(item.get("grounded"))
    score = float(item.get("score") or 0.0)
    support = item.get("support") or {}
    passage = str(support.get("matched_text") or "")
    verdict = "grounded" if grounded else "ungrounded"
    if score < 0.5 and grounded:
        verdict = "uncertain"

    return {
        "verdict": verdict,
        "grounded": grounded,
        "score": score,
        "passage": passage,
        "engine": "groundrails-subprocess",
    }


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw or "{}")
        claim = str(payload.get("claim") or "").strip()
        source = str(payload.get("source") or "")
        if not claim:
            print(
                json.dumps(
                    {"error": "claim is required", "verdict": "uncertain", "grounded": False}
                )
            )
            return 1
        result = _verify_claim(claim, source)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "error": str(exc),
                    "verdict": "uncertain",
                    "grounded": False,
                    "engine": "groundrails-subprocess",
                },
                ensure_ascii=False,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
