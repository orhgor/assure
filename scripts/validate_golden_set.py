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

``--prose`` runs ``tests/golden/prose`` — the same documents as free-running
prose (no ``Label: value`` lines) plus a three-page mixed bundle — twice per
document: label pass only, and label pass + the grounded LLM fallback
(``services/llm_extraction``, the model ``cost_governance`` resolves; on
``ASSURE_LLM_BACKEND=ollama`` with ``OLLAMA_API_BASE`` set that is the local
``qwen2.5:1.5b``). It prints both per-field accuracies and the wall time of
the model pass per document, and writes ``tests/golden/last_run_prose.json``.
The threshold gate applies to the label-only figure only when ``--prose`` is
not given; prose numbers are evidence, not a gate.

An entry in ``expected.json`` with a ``documents`` list is a mixed bundle:
the script checks the page-level segmentation (``v1_orchestrator.segment_pages``)
against the expected segments and scores each segment's fields.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prompt_matrix.services import field_extractor as fx  # noqa: E402
from prompt_matrix.services import llm_extraction as lx  # noqa: E402
from prompt_matrix.services import v1_orchestrator as orch  # noqa: E402


def _same(actual, expected) -> bool:
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return isinstance(actual, (int, float)) and abs(float(actual) - float(expected)) < 0.005
    if actual is None or expected is None:
        return actual is expected
    norm = lambda s: re.sub(r"\s+", " ", str(s)).strip().lower()  # noqa: E731
    return norm(actual) == norm(expected)


class _Tally:
    """Per-type / per-field counters for one extraction mode."""

    def __init__(self) -> None:
        self.per_type: dict[str, dict[str, int]] = {}
        self.per_field: dict[str, dict[str, int]] = {}
        self.checked = self.correct = 0

    def add(self, doc_type: str, fname: str, ok: bool) -> None:
        self.checked += 1
        self.correct += int(ok)
        b = self.per_type.setdefault(doc_type, {"fields": 0, "correct": 0})
        b["fields"] += 1
        b["correct"] += int(ok)
        f = self.per_field.setdefault(fname, {"checked": 0, "correct": 0})
        f["checked"] += 1
        f["correct"] += int(ok)

    def summary(self) -> dict:
        return {
            "fields_checked": self.checked,
            "field_accuracy": round(self.correct / max(1, self.checked), 4),
            "per_type": {t: {**b, "field_accuracy": round(b["correct"] / max(1, b["fields"]), 4)} for t, b in self.per_type.items()},
            "per_field": {f: {**b, "accuracy": round(b["correct"] / max(1, b["checked"]), 4)} for f, b in sorted(self.per_field.items())},
        }


def _extract(doc_type: str, texts: list[str], *, llm: bool, notes: list[str]) -> tuple[list[dict], float]:
    """Fields for one document (or segment) in one mode, with the wall time of the pass."""
    started = time.monotonic()
    if llm:
        fields, _rules = orch.extract_segment_fields(
            doc_type, texts, layout=None, parser_name="jdf-cli", parse_confidence=None, ocr_confidence=None,
            page_quality=[1.0] * len(texts), visual_pages=[None] * len(texts), verification=None, notes=notes,
        )
    else:
        fields = fx.extract_fields(doc_type, texts, parser_name="jdf-cli", parse_confidence=None, ocr_confidence=None,
                                   page_quality=[1.0] * len(texts))
        fields, _rules = orch.decide_fields(fields, verification=None, document_type=doc_type)
    return fields, round(time.monotonic() - started, 2)


def _score(tally: _Tally, doc_type: str, fields: list[dict], want_fields: dict) -> list[dict]:
    by_name = {f["name"]: f for f in fields}
    misses = []
    for fname, want in want_fields.items():
        f = by_name.get(fname) or {}
        ok = _same(f.get("value"), want)
        tally.add(doc_type, fname, ok)
        if not ok:
            misses.append({"field": fname, "expected": want, "got": f.get("value"), "method": f.get("extraction_method")})
    return misses


def run(golden_dir: Path, *, llm: bool = False) -> dict:
    expected = json.loads((golden_dir / "expected.json").read_text(encoding="utf-8"))
    label = _Tally()
    with_llm = _Tally()
    docs = []
    types_correct = 0
    type_counts: dict[str, dict[str, int]] = {}
    llm_reachable = False
    for name, exp in expected.items():
        text = (golden_dir / name).read_text(encoding="utf-8")
        bundle = {"jdf": None, "chunks": [], "text": text, "page_count": 1, "parser_name": "text", "source_kind": "text",
                  "parse_confidence": None, "ocr_confidence": None, "images": []}
        texts = fx.page_texts(bundle)
        segments = orch.segment_pages(texts)
        want_type = exp["document_type"]
        detected = orch.bundle_classification(segments)["document_type"] if len(segments) > 1 else segments[0]["document_type"]
        type_ok = detected == want_type
        types_correct += int(type_ok)
        tc = type_counts.setdefault(want_type, {"documents": 0, "classified": 0})
        tc["documents"] += 1
        tc["classified"] += int(type_ok)
        detail: dict = {"document": name, "expected_type": want_type, "detected_type": detected,
                        "segments": [{"pages": s["pages"], "document_type": s["document_type"], "confidence": s["confidence"]} for s in segments]}
        # Expected units: one per document, or one per expected segment of a mixed bundle.
        if "documents" in exp:
            units = []
            for i, want_seg in enumerate(exp["documents"]):
                seg = segments[i] if i < len(segments) else None
                seg_ok = bool(seg and seg["pages"] == want_seg["pages"] and seg["document_type"] == want_seg["document_type"])
                units.append((want_seg["document_type"], orch.segment_texts(texts, want_seg["pages"]), want_seg["fields"], seg_ok))
            detail["segmentation_ok"] = all(u[3] for u in units) and len(segments) == len(exp["documents"])
        else:
            units = [(want_type, texts, exp["fields"], True)]
        detail["label_only"] = {"misses": []}
        if llm:
            detail["label_llm"] = {"misses": [], "notes": [], "elapsed_s": 0.0, "llm_grounded": 0}
        for doc_type, unit_texts, want_fields, _ok in units:
            fields, _t = _extract(doc_type, unit_texts, llm=False, notes=[])
            detail["label_only"]["misses"] += _score(label, doc_type, fields, want_fields)
            if llm:
                notes: list[str] = []
                fields, elapsed = _extract(doc_type, unit_texts, llm=True, notes=notes)
                detail["label_llm"]["misses"] += _score(with_llm, doc_type, fields, want_fields)
                detail["label_llm"]["notes"] += notes
                detail["label_llm"]["elapsed_s"] = round(detail["label_llm"]["elapsed_s"] + elapsed, 2)
                detail["label_llm"]["llm_grounded"] += sum(1 for f in fields if f.get("extraction_method") == "llm_grounded")
                if any(n.startswith("llm extraction: model") for n in notes):
                    llm_reachable = True
        docs.append(detail)
    result = {
        "run_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "golden_dir": str(golden_dir.relative_to(ROOT)) if golden_dir.is_relative_to(ROOT) else str(golden_dir),
        "documents": len(expected),
        "type_accuracy": round(types_correct / max(1, len(expected)), 4),
        **label.summary(),
        "per_type": {t: {**type_counts.get(t, {}), **b} for t, b in label.summary()["per_type"].items()},
        "documents_detail": docs,
    }
    if llm:
        result["label_llm"] = {
            "enabled": lx.llm_extraction_enabled(),
            "model": lx.current_model_id(),
            "backend": os.environ.get("ASSURE_LLM_BACKEND", "").strip().lower() or "cloud",
            "reachable": llm_reachable,
            "elapsed_s_total": round(sum(d["label_llm"]["elapsed_s"] for d in docs), 2),
            **with_llm.summary(),
        }
    return result


def _print(result: dict, *, llm: bool) -> None:
    print(f"golden set {result['golden_dir']}: {result['documents']} documents, type accuracy {result['type_accuracy']:.0%}, "
          f"label-only field accuracy {result['field_accuracy']:.1%} over {result['fields_checked']} fields")
    if llm:
        ll = result["label_llm"]
        state = "reachable" if ll["reachable"] else "NOT reachable (every document skipped)"
        print(f"label+llm field accuracy {ll['field_accuracy']:.1%} — model {ll['model']} ({ll['backend']}) {state}, "
              f"{ll['elapsed_s_total']:.1f}s total model-pass time")
    for t, b in result["per_type"].items():
        line = f"  {t:18s} docs {b.get('documents', '-')}  classified {b.get('classified', '-')}/{b.get('documents', '-')}  label-only {b['correct']}/{b['fields']}"
        if llm and t in result["label_llm"]["per_type"]:
            lb = result["label_llm"]["per_type"][t]
            line += f"  label+llm {lb['correct']}/{lb['fields']}"
        print(line)
    for f, b in result["per_field"].items():
        line = f"    {f:28s} label-only {b['correct']}/{b['checked']}"
        if llm and f in result["label_llm"]["per_field"]:
            lb = result["label_llm"]["per_field"][f]
            line += f"   label+llm {lb['correct']}/{lb['checked']}"
            if lb["correct"] < lb["checked"]:
                line += "  <-- miss"
        elif b["correct"] < b["checked"]:
            line += "  <-- miss"
        print(line)
    for d in result["documents_detail"]:
        if d["detected_type"] != d["expected_type"]:
            print(f"  ! {d['document']}: classified {d['detected_type']} (expected {d['expected_type']})")
        if "segmentation_ok" in d:
            print(f"  {'ok' if d['segmentation_ok'] else '!'} {d['document']}: segments {[(s['document_type'], s['pages']) for s in d['segments']]}")
        if llm:
            ll = d["label_llm"]
            print(f"  {d['document']}: label-only misses {len(d['label_only']['misses'])}, label+llm misses {len(ll['misses'])}, "
                  f"{ll['llm_grounded']} llm-grounded, {ll['elapsed_s']:.1f}s")
            for m in ll["misses"]:
                print(f"      miss {m['field']}: expected {m['expected']!r} got {m['got']!r} ({m['method'] or 'not found'})")
            for n in ll["notes"]:
                if n.startswith("llm extraction skipped") or "rejected" in n:
                    print(f"      note: {n}")
        else:
            for m in d["label_only"]["misses"]:
                print(f"  ! {d['document']}: {m['field']} expected {m['expected']!r} got {m['got']!r}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--golden", default=None, help="golden directory (default tests/golden, or tests/golden/prose with --prose)")
    ap.add_argument("--prose", action="store_true", help="run the prose set with the label pass and the grounded LLM pass")
    ap.add_argument("--no-llm", action="store_true", help="with --prose: label pass only")
    ap.add_argument("--threshold", type=float, default=0.8)
    ap.add_argument("--no-write", action="store_true", help="do not write last_run*.json")
    args = ap.parse_args()
    golden = Path(args.golden) if args.golden else (ROOT / "tests" / "golden" / ("prose" if args.prose else ""))
    llm = bool(args.prose and not args.no_llm)
    result = run(golden, llm=llm)
    _print(result, llm=llm)
    if not args.no_write:
        out = (ROOT / "tests" / "golden" / "last_run_prose.json") if args.prose else (golden / "last_run.json")
        out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"wrote {out}")
    if not args.prose and result["field_accuracy"] < args.threshold:
        print(f"FAIL: field accuracy {result['field_accuracy']:.1%} below threshold {args.threshold:.0%}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
