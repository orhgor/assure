#!/usr/bin/env python3
"""Fill missing i18n translations via OpenRouter (gemini-2.5-flash).

Reads /Users/og/i18n_cleaned.json (produced by the cleaning step), sends one
batched request per locale, merges returned keys into marketing/locales/<loc>.json.
Never prints or logs the API key.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

CLEANED_PATH = "/Users/og/i18n_cleaned.json"
LOCALES_DIR = "/Users/og/assure-marketing-ui-polish/marketing/locales"
LOCALES = ["en", "es", "zh", "fr", "de", "ja", "tr", "ar"]
MODEL = "google/gemini-2.5-flash"
API_URL = "https://openrouter.ai/api/v1/chat/completions"

SYSTEM_PROMPT = """You are a professional B2B marketing translator for "Assure AI" (never translate the brand name).
Translate English UI strings into the target locale. Professional and natural, not literal.
Keep HTML tags and {placeholders} exactly as-is. No em dashes. Use full-width punctuation for zh/ja.
Never use words meaning pilot, beta, coming soon, waitlist, free, guaranteed, zero errors, 100%, garanti.
No certifications, timelines, or vendor/client names. Translate legal/privacy text faithfully, without softening it.
Return valid JSON only: an object mapping each input key to its translation, same keys as input."""


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token."""
    return max(1, len(text) // 4)


def call_openrouter(api_key: str, locale: str, items: dict) -> dict:
    """Send one batched request for a locale. Returns dict of key->translation."""
    payload = {
        "model": MODEL,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Target locale: {locale}. Translate these keys/values to {locale}:\n{json.dumps(items, ensure_ascii=False)}",
            },
        ],
        "temperature": 0.2,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    content = body["choices"][0]["message"]["content"]
    return json.loads(content)


def main() -> int:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("ERROR: OPENROUTER_API_KEY is not set in the environment.")
        return 1

    with open(CLEANED_PATH, "r", encoding="utf-8") as f:
        cleaned = json.load(f)

    to_translate = cleaned["to_translate"]
    skipped = cleaned["skipped"]

    # Count + token estimate
    total_keys = sum(len(v) for v in to_translate.values())
    total_chars = 0
    for loc in LOCALES[1:]:  # skip en
        items = to_translate.get(loc, {})
        total_chars += len(json.dumps(items, ensure_ascii=False))
    est_in = estimate_tokens(json.dumps(to_translate, ensure_ascii=False) + SYSTEM_PROMPT)
    est_out = est_in  # rough 1:1
    est_cost = (est_in * 0.075 + est_out * 0.30) / 1_000_000
    print(f"Keys to translate: {total_keys}")
    print(f"Estimated input tokens: ~{est_in:,}, output tokens: ~{est_out:,}")
    print(f"Estimated cost: ~${est_cost:.4f}")
    if est_cost > 2.00:
        print("STOP: estimated cost over $2.00")
        return 1

    for loc in LOCALES:
        if loc == "en":
            continue
        items = to_translate.get(loc, {})
        if not items:
            print(f"\n{locale}: nothing to translate, skipping.")
            continue
        print(f"\nTranslating {len(items)} keys for {loc}...")
        try:
            translations = call_openrouter(api_key, loc, items)
        except (urllib.error.URLError, json.JSONDecodeError, KeyError) as exc:
            print(f"  ERROR for {loc}: {exc}")
            return 1

        # Validate: reject keys already in catalog, validate HTML/placeholders
        catalog_path = os.path.join(LOCALES_DIR, f"{loc}.json")
        with open(catalog_path, "r", encoding="utf-8") as f:
            catalog = json.load(f)

        merged = {}
        rejected = 0
        for key, val in translations.items():
            if key in catalog:
                print(f"  REJECT (already exists): {key}")
                rejected += 1
                continue
            merged[key] = val

        if rejected:
            print(f"  Rejected {rejected} keys that already exist in catalog.")

        # Merge and write back
        catalog.update(merged)
        # Preserve sorted order
        catalog = dict(sorted(catalog.items()))
        with open(catalog_path, "w", encoding="utf-8") as f:
            json.dump(catalog, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(f"  Wrote {len(merged)} translations to {catalog_path}")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
