#!/usr/bin/env python3
"""Fill missing i18n translations via OpenRouter (gemini-2.5-flash).

Default mode: reads <repo>/i18n_cleaned.json (produced by the cleaning step),
sends one batched request per locale, merges returned keys into
marketing/locales/<locale>.json.

--identical mode: for each locale, finds keys whose value is byte-identical to
the EN value (English placeholders that were never translated), and translates
those. Same prompt and rules as default mode.

All paths are relative to the repository root (the directory containing
marketing/). Never prints or logs the API key.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

# Repository root = parent of marketing/
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCALES_DIR = os.path.join(REPO_ROOT, "marketing", "locales")
CLEANED_PATH = os.path.join(REPO_ROOT, "i18n_cleaned.json")
LOCALES = ["en", "es", "zh", "fr", "de", "ja", "tr", "ar"]
MODEL = "google/gemini-2.5-flash"
API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Keys that are legitimate English values (not placeholders to translate)
LEGIT_ENGLISH = {
    "es": {"Roles"},
    "fr": {"Section", "Direct", "Plans"},
    "de": {"Name", "Enterprise"},
}

BANNED = ["pilot", "beta", "coming soon", "waitlist", "free", "guaranteed",
          "zero errors", "100%", "garanti"]

SYSTEM_PROMPT = """You are a professional B2B marketing translator for "Assure AI" (never translate the brand name).
Translate English UI strings into the target locale. Professional and natural, not literal.
Keep HTML tags and {placeholders} exactly as-is. No em dashes. Use full-width punctuation for zh/ja.
Never use words meaning pilot, beta, coming soon, waitlist, free, guaranteed, zero errors, 100%, garanti.
No certifications, timelines, or vendor/client names. Translate legal/privacy text faithfully, without softening it.
Return valid JSON only: an object mapping each input key to its translation, same keys as input."""


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token."""
    return max(1, len(text) // 4)


def is_brand_only_or_url_or_email(val: str) -> bool:
    """Return True if the value should not be translated (brand, URL, email)."""
    v = val.strip()
    if v.startswith(("http://", "https://", "mailto:")):
        return True
    if "@" in v and "." in v.split("@")[-1] and len(v) < 100:
        return True
    return False


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


def load_catalog(loc: str) -> dict:
    path = os.path.join(LOCALES_DIR, f"{loc}.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_catalog(loc: str, catalog: dict) -> None:
    path = os.path.join(LOCALES_DIR, f"{loc}.json")
    catalog = dict(sorted(catalog.items()))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)
        f.write("\n")


def translate_locale(api_key: str, loc: str, items: dict) -> int:
    """Translate one locale's items. Returns number of keys written."""
    if not items:
        print(f"\n{loc}: nothing to translate, skipping.")
        return 0

    total_chars = len(json.dumps(items, ensure_ascii=False))
    est_in = estimate_tokens(json.dumps(items, ensure_ascii=False) + SYSTEM_PROMPT)
    est_out = est_in
    est_cost = (est_in * 0.075 + est_out * 0.30) / 1_000_000
    print(f"\nTranslating {len(items)} keys for {loc} (est. ~${est_cost:.4f})...")

    translations = call_openrouter(api_key, loc, items)

    catalog = load_catalog(loc)
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

    catalog.update(merged)
    write_catalog(loc, catalog)
    print(f"  Wrote {len(merged)} translations to marketing/locales/{loc}.json")
    return len(merged)


def default_mode(api_key: str) -> int:
    with open(CLEANED_PATH, "r", encoding="utf-8") as f:
        cleaned = json.load(f)

    to_translate = cleaned["to_translate"]
    skipped = cleaned["skipped"]

    total_keys = sum(len(v) for v in to_translate.values())
    est_cost = 0.0
    for loc in LOCALES:
        if loc == "en":
            continue
        items = to_translate.get(loc, {})
        if not items:
            continue
        total_chars = len(json.dumps(items, ensure_ascii=False))
        est_in = estimate_tokens(total_chars + len(SYSTEM_PROMPT))
        est_cost += (est_in * 0.075 + est_in * 0.30) / 1_000_000

    print(f"Keys to translate: {total_keys}")
    print(f"Estimated cost: ~${est_cost:.4f}")
    if est_cost > 2.00:
        print("STOP: estimated cost over $2.00")
        return 1

    total_written = 0
    for loc in LOCALES:
        if loc == "en":
            continue
        items = to_translate.get(loc, {})
        total_written += translate_locale(api_key, loc, items)

    print(f"\nDone. Total written: {total_written}")
    return 0


def identical_mode(api_key: str) -> int:
    # Load EN catalog as reference
    en_cat = load_catalog("en")

    total_written = 0
    for loc in LOCALES:
        if loc == "en":
            continue

        cat = load_catalog(loc)
        items = {}
        for key, en_val in en_cat.items():
            if key not in cat:
                continue  # key doesn't exist in this locale
            loc_val = cat[key]
            if loc_val != en_val:
                continue  # not identical to EN
            if is_brand_only_or_url_or_email(en_val):
                continue
            if en_val in LEGIT_ENGLISH.get(loc, set()):
                continue
            items[key] = en_val

        n = translate_locale(api_key, loc, items)
        print(f"  {loc}: {n} identical-to-EN keys found")
        total_written += n

    print(f"\nDone. Total written: {total_written}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Fill missing i18n translations")
    parser.add_argument("--identical", action="store_true",
                        help="Translate keys identical to EN value (placeholders)")
    args = parser.parse_args()

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("ERROR: OPENROUTER_API_KEY is not set in the environment.")
        return 1

    if args.identical:
        return identical_mode(api_key)
    return default_mode(api_key)


if __name__ == "__main__":
    sys.exit(main())
