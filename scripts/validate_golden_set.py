#!/usr/bin/env python3
"""Validate Parsure's classify → extract → decide chain against tests/golden.

Spec §9 items 26–27: a small golden set with known answers, a script that
measures per-type and per-field accuracy, and the result feeding
``db.parsure_repository.analytics`` (``golden_accuracy``) by way of
``tests/golden/last_run.json`` — the only place that number may come from.

Field accuracy counts a field correct when the extracted ``value`` equals the
expected one (numbers within 0.005, strings case- and whitespace-insensitive).
Exit status is 1 when field accuracy is below ``--threshold`` (default 0.8),
so CI can gate on it. Run:

    .venv/bin/python scripts/validate_golden_set.py [--golden tests/golden] [--no-write]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prompt_matrix.services import field_extractor as fx  # noqa: E402
from prompt_matrix.services import v1_orchestrator as orch  # noqa: E402


def _same(actual, expected) -> bool:
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return isinstance(actual, (int, float)) and abs(float(actual) - float(expected)) < 0.005
    if actual is None or expected is None:
        return actual is expected
    norm = lambda s: re.sub(r"\s+", " ", str(s)).strip().lower()  # noqa: E731
    return norm(actual) == norm(expected)


def run(golden_dir: Path) -> dict:
    expected = json.loads((golden_dir / "expected.json").read_text(encoding="utf-8"))
    per_type: dict[str, dict[str, int]] = {}
    per_field: dict[str, dict[str, int]] = {}
    docs = []
    fields_checked = fields_correct = 0
    types_correct = 0
    for name, exp in expected.items():
        text = (golden_dir / name).read_text(encoding="utf-8")
        bundle = {"jdf": None, "chunks": [], "text": text, "page_count": 1, "parser_name": "text", "source_kind": "text",
                  "parse_confidence": None, "ocr_confidence": None, "images": []}
        texts = fx.page_texts(bundle)
        classification = fx.classify_document("\n".join(texts))
        detected = classification["document_type"]
        type_ok = detected == exp["document_type"]
        types_correct += int(type_ok)
        bucket = per_type.setdefault(exp["document_type"], {"documents": 0, "classified": 0, "fields": 0, "correct": 0})
        bucket["documents"] += 1
        bucket["classified"] += int(type_ok)
        fields = fx.extract_fields(exp["document_type"], texts, parser_name="jdf-cli", parse_confidence=None,
                                   ocr_confidence=None, page_quality=[1.0] * len(texts))
        fields, _rules = orch.decide_fields(fields, verification=None, document_type=exp["document_type"])
        by_name = {f["name"]: f for f in fields}
        misses = []
        for fname, want in exp["fields"].items():
            got = (by_name.get(fname) or {}).get("value")
            ok = _same(got, want)
            fields_checked += 1
            fields_correct += int(ok)
            bucket["fields"] += 1
            bucket["correct"] += int(ok)
            fb = per_field.setdefault(fname, {"checked": 0, "correct": 0})
            fb["checked"] += 1
            fb["correct"] += int(ok)
            if not ok:
                misses.append({"field": fname, "expected": want, "got": got})
        hallucinated = [f["name"] for f in fields if f["value"] is not None and f["name"] not in exp["fields"] and f["field_type"] != "signature"]
        docs.append({"document": name, "expected_type": exp["document_type"], "detected_type": detected,
                     "classification_confidence": classification["confidence"], "misses": misses,
                     "extra_values": hallucinated, "states": {f["name"]: f["field_state"] for f in fields}})
    result = {
        "run_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "documents": len(expected),
        "type_accuracy": round(types_correct / max(1, len(expected)), 4),
        "fields_checked": fields_checked,
        "field_accuracy": round(fields_correct / max(1, fields_checked), 4),
        "per_type": {t: {**b, "field_accuracy": round(b["correct"] / max(1, b["fields"]), 4)} for t, b in per_type.items()},
        "per_field": {f: {**b, "accuracy": round(b["correct"] / max(1, b["checked"]), 4)} for f, b in sorted(per_field.items())},
        "documents_detail": docs,
    }
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--golden", default=str(ROOT / "tests" / "golden"))
    ap.add_argument("--threshold", type=float, default=0.8)
    ap.add_argument("--no-write", action="store_true", help="do not write last_run.json")
    args = ap.parse_args()
    golden = Path(args.golden)
    result = run(golden)
    print(f"golden set: {result['documents']} documents, type accuracy {result['type_accuracy']:.0%}, "
          f"field accuracy {result['field_accuracy']:.1%} over {result['fields_checked']} fields")
    for t, b in result["per_type"].items():
        print(f"  {t:18s} docs {b['documents']}  classified {b['classified']}/{b['documents']}  fields {b['correct']}/{b['fields']} ({b['field_accuracy']:.0%})")
    for f, b in result["per_field"].items():
        flag = "" if b["correct"] == b["checked"] else "  <-- miss"
        print(f"    {f:28s} {b['correct']}/{b['checked']}{flag}")
    for d in result["documents_detail"]:
        if d["detected_type"] != d["expected_type"]:
            print(f"  ! {d['document']}: classified {d['detected_type']} (expected {d['expected_type']})")
        for m in d["misses"]:
            print(f"  ! {d['document']}: {m['field']} expected {m['expected']!r} got {m['got']!r}")
        if d["extra_values"]:
            print(f"  i {d['document']}: values also extracted for {', '.join(d['extra_values'])} (not in expected.json)")
    if not args.no_write:
        out = golden / "last_run.json"
        out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"wrote {out}")
    if result["field_accuracy"] < args.threshold:
        print(f"FAIL: field accuracy {result['field_accuracy']:.1%} below threshold {args.threshold:.0%}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
