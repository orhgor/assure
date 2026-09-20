#!/usr/bin/env python3
"""PROBE — real pinned translations for the Tier 2 fixture. Runs ON the box.

Not product code, and deliberately free of the app's import graph.

What is product and what is not, precisely:

* **Product, shipped in this probe**: the prompt builder and the answer parser
  (``relational_translate.build_translation_prompt`` / ``parse_claim``), so the
  text sent and the acceptance criteria for the answer are the ones that will run.
* **Product, but built locally and carried in the fixture**: the model id, the
  provider pin (``keys.PROVIDER_PIN``) and the eleven rendered prompts. Carrying
  them in avoids importing ``cost_governance``/``keys`` here, and the pin's
  provenance is not lost: the product's own transport is captured on the wire
  separately, and this probe reports the provider OpenRouter *served*, which is
  the fact that matters.
* **Not product**: the HTTP call itself (stdlib urllib) and this script.

Two properties are load-bearing:

* **No app database.** ``db.pipeline_cache`` is replaced by a no-op module before
  the product import, so a cache hit cannot fake the determinism repeats and this
  process cannot write the app's SQLite. No cache means three real calls.
* **No ``/tmp`` on ``sys.path``.** An earlier version put /tmp there and picked up
  a stray ``/tmp/inspect.py`` left by another probe, which shadowed the stdlib and
  broke ``dataclasses``. Modules are imported flat from their own directory.

usage (on the box): <venv>/bin/python /tmp/tier2/probe.py /tmp/tier2/fixture.json
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import types
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # only this directory: my modules, nothing else

pkg = types.ModuleType("db")
pkg.__path__ = []  # a package with nothing in it
cache = types.ModuleType("db.pipeline_cache")
cache.fetch_pipeline_cache = lambda *_a, **_k: None
cache.save_pipeline_cache = lambda *_a, **_k: None
pkg.pipeline_cache = cache
sys.modules["db"] = pkg
sys.modules["db.pipeline_cache"] = cache

import relational_translate as rt  # noqa: E402  (product: prompt + parser)

REPEAT_INDEX = 4  # the 90837 "53 minutes or longer" claim
REPEATS = 3


def _load_key() -> str:
    if os.environ.get("OPENROUTER_API_KEY"):
        return os.environ["OPENROUTER_API_KEY"]
    app = Path("/home/ubuntu/assure-prototype")
    for env_file in (app / ".env.staging", app / ".env.production", app / ".env"):
        if not env_file.exists():
            continue
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("OPENROUTER_API_KEY"):
                return line.split("=", 1)[1].strip().strip("'\"")
    return ""


def _body(model: str, prompt: str, pin: dict) -> dict:
    """The same request the product sends, spelled here so this probe stays app-free.

    The fields come from the product — ``MAX_OUTPUT_TOKENS`` is imported, the pin
    comes from the fixture (built from ``keys.PROVIDER_PIN``) — and the product's
    own ``pinned_request_body`` is the thing the local wire capture reads, which
    is where "this body is the product's body" is actually established.
    """
    # litellm's `openrouter/` prefix is routing syntax for litellm, not part of the
    # model id the API accepts: posting it verbatim to OpenRouter is a 400.
    api_model = model.split("/", 1)[1] if model.startswith("openrouter/") else model
    return {
        "model": api_model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": rt.MAX_OUTPUT_TOKENS,
        "provider": dict(pin),
    }


def _call(body: dict, key: str) -> dict:
    base = os.environ.get("OPENROUTER_API_BASE", "https://openrouter.ai/api/v1")
    request = urllib.request.Request(
        base.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": os.environ.get(
                "OPENROUTER_HTTP_REFERER", "https://staging.getassureai.com"
            ),
            "X-Title": os.environ.get("OPENROUTER_APP_TITLE", "Assure AI"),
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        return json.loads(response.read().decode())


def translate(prompt: str, model: str, pin: dict, key: str) -> dict:
    body = _body(model, prompt, pin)
    started = time.time()
    try:
        response = _call(body, key)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode()[:300]
        except Exception:
            pass
        return {
            "ok": False,
            "reason": f"HTTP {exc.code}: {detail}",
            "seconds": round(time.time() - started, 2),
        }
    except Exception as exc:
        return {
            "ok": False,
            "reason": f"{type(exc).__name__}: {exc}",
            "seconds": round(time.time() - started, 2),
        }
    content = ((response.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    parsed = rt.parse_claim(content)
    return {
        "ok": parsed is not None,
        "claim_json": parsed,
        "served_provider": response.get("provider"),
        "model_returned": response.get("model"),
        "json_sha256": hashlib.sha256(
            json.dumps(parsed, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if parsed
        else "",
        "raw_sha256": hashlib.sha256(content.encode()).hexdigest(),
        "raw": content[:300],
        "reason": "" if parsed else "the model returned no usable JSON relation",
        "temperature": body.get("temperature"),
        "prompt_provider": body.get("provider"),
        "usage": response.get("usage"),
        "seconds": round(time.time() - started, 2),
    }


def main() -> int:
    fixture = json.loads(Path(sys.argv[1]).read_text())
    key = _load_key()
    model = fixture["translator_model"]
    pin = fixture["pin"]
    prompts = fixture["prompts"]
    claims = fixture["candidates"]

    print(f"key_present={bool(key)} model={model}")
    print(f"pin={json.dumps(pin)} prompt_fingerprint={fixture.get('prompt_fingerprint')}")
    print(f"z3={rt.z3_version()} claims={len(claims)} prompts={len(prompts)}")
    if not key or not prompts:
        print("HARD FAILURE: missing key or prompts")
        return 2

    results = []
    valid = 0
    for index, prompt in enumerate(prompts):
        outcome = translate(prompt, model, pin, key)
        valid += 1 if outcome["ok"] else 0
        outcome["index"] = index
        outcome["claim"] = claims[index] if index < len(claims) else ""
        results.append(outcome)
        print(
            f"  [{index:02d}] ok={outcome['ok']} served={outcome.get('served_provider')} "
            f"{outcome['seconds']:5.2f}s {(outcome.get('reason') or '')[:60]}"
        )

    partial = {
        "key_present": bool(key),
        "model": model,
        "pin": pin,
        "z3_version": rt.z3_version(),
        "prompt_fingerprint": fixture.get("prompt_fingerprint"),
        "candidates": len(claims),
        "valid_json": valid,
        "validity_rate": round(valid / len(prompts), 4) if prompts else 0.0,
        "served_providers": sorted({str(r.get("served_provider")) for r in results}),
        "results": results,
    }
    Path("/tmp/tier2/probe_output.json").write_text(json.dumps(partial, indent=2))

    repeat = {"prompt_index": REPEAT_INDEX, "runs": []}
    if 0 <= REPEAT_INDEX < len(prompts):
        for _ in range(REPEATS):
            outcome = translate(prompts[REPEAT_INDEX], model, pin, key)
            repeat["runs"].append(
                {
                    "ok": outcome.get("ok", False),
                    "json_sha256": outcome.get("json_sha256", ""),
                    "raw_sha256": outcome.get("raw_sha256", ""),
                    "served_provider": outcome.get("served_provider"),
                    "reason": outcome.get("reason", ""),
                }
            )
        hashes = [run.get("json_sha256", "") for run in repeat["runs"]]
        repeat["distinct_hashes"] = len(set(hashes))
        print(f"  repeat distinct_hashes={len(set(hashes))} hashes={hashes}")

    payload = {
        "key_present": bool(key),
        "model": model,
        "pin": pin,
        "z3_version": rt.z3_version(),
        "prompt_fingerprint": fixture.get("prompt_fingerprint"),
        "candidates": len(claims),
        "valid_json": valid,
        "validity_rate": round(valid / len(prompts), 4) if prompts else 0.0,
        "served_providers": sorted({str(r.get("served_provider")) for r in results}),
        "results": results,
        "repeat": repeat,
    }
    Path("/tmp/tier2/probe_output.json").write_text(json.dumps(payload, indent=2))
    print(
        "SUMMARY "
        + json.dumps(
            {
                k: payload[k]
                for k in ("candidates", "valid_json", "validity_rate", "served_providers", "prompt_fingerprint")
            }
        )
    )
    print("RESULT_JSON " + json.dumps(payload, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
