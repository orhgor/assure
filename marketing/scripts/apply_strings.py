#!/usr/bin/env python3
"""Apply a reviewed string patch to marketing/locales/*.json.

Patch file (JSON), every section optional:
  {"set":     {"en": {"key": "value"}},
   "delete":  {"es": ["key", "key2"]},
   "replace": {"ar": [["old text", "new text"], ["old", "new", "meta."]]}}

"replace" swaps an exact substring inside that locale's values; the optional
third item limits it to keys starting with that prefix. A replace that
matches nothing is an error. Files keep the repo format: indent=2,
ensure_ascii=False, trailing newline; existing key order is kept and new
keys go at the end.

    .venv/bin/python marketing/scripts/apply_strings.py /tmp/omp/patch.json
    .venv/bin/python marketing/scripts/apply_strings.py --check
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

LOCALES_DIR = Path(__file__).resolve().parents[1] / "locales"
LOCALES = ("en", "es", "zh", "fr", "de", "ja", "tr", "ar")


def _path(loc: str) -> Path:
    return LOCALES_DIR / f"{loc}.json"


def _dump(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def check() -> int:
    bad = []
    for loc in LOCALES:
        text = _path(loc).read_text(encoding="utf-8")
        data = json.loads(text)
        if _dump(data) != text or not all(isinstance(v, str) for v in data.values()):
            bad.append(loc)
    if bad:
        print("STOP: locale files not in repo format:", bad)
        return 1
    print("locales OK")
    return 0


def apply(patch_file: str) -> int:
    patch = json.loads(Path(patch_file).read_text(encoding="utf-8"))
    unknown = set(patch) - {"set", "delete", "replace"}
    if unknown:
        print("STOP: unknown patch sections:", sorted(unknown))
        return 1
    changed = 0
    for loc in LOCALES:
        sets = patch.get("set", {}).get(loc, {})
        dels = patch.get("delete", {}).get(loc, [])
        reps = patch.get("replace", {}).get(loc, [])
        if not (sets or dels or reps):
            continue
        data = json.loads(_path(loc).read_text(encoding="utf-8"))
        for key in dels:
            if key in data:
                del data[key]
                changed += 1
        for item in reps:
            old, new = item[0], item[1]
            prefix = item[2] if len(item) > 2 else ""
            hits = [k for k, v in data.items() if k.startswith(prefix) and old in v]
            if not hits:
                print(f"STOP: {loc}: text to replace not found: {old!r}")
                return 1
            for k in hits:
                data[k] = data[k].replace(old, new)
                changed += 1
        for key, value in sets.items():
            if not isinstance(value, str):
                print(f"STOP: {loc}.{key}: value must be a string")
                return 1
            data[key] = value
            changed += 1
        _path(loc).write_text(_dump(data), encoding="utf-8")
    print(f"applied {changed} change(s)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("patch", nargs="?")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return check()
    if not args.patch:
        ap.error("give a patch file or --check")
    return apply(args.patch)


if __name__ == "__main__":
    sys.exit(main())
