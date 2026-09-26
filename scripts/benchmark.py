#!/usr/bin/env python3
"""Run the frozen Parsure benchmark (``bench/manifest.json``) through the real pipeline.

Every case is ingested in-process with ``services.pdf_ingest.ingest_pdf_for_project``
— the same function the worker runs — into a throwaway project, against the
PostgreSQL named by ``DATABASE_URL`` (use a scratch schema: ``ASSURE_PG_SCHEMA=bench_run``).
Nothing is mocked: the router, jdf-cli (+ tesseract.js for scans and photos), Z3,
the Red-Hat hook and the Parsure report are the production code paths.

Measured per class (``policies``, ``claims``, ``photos``, ``signatures``,
``handwritten``, ``mixed``) and overall:

* routing accuracy — ``classification.document_type`` equals the expected type
  (a mixed bundle must also segment as expected); family accuracy alongside.
  Inputs whose expected type has no schema yet (``schema_exists: false``) count
  as misses and are reported separately as schema gaps.
* extraction recall — expected fields found with the expected value
  (numbers within 0.005, strings case/whitespace-insensitive, code lists as sets).
* anchoring rate — found fields that carry ``field_source_node_id``.
* Red-Hat contradiction recall — seeded cross-document conflicts that appear in
  ``conflicts[]`` (``kind == cross_document``) or in ``redhat.findings[]`` under
  rule ``cross_document_conflict`` (``services/redhat_graph``), when that block
  is on the report.
* replay determinism — each input is ingested twice; the multisets of
  ``element_id`` (falling back to ``field_source_node_id``) over the fields and
  the layout must be identical. The value set is compared as a secondary figure.
* latency p50 / p95 — the end-to-end ingest time: the pipeline's own
  ``duration_ms`` (route → parse/OCR → verify → save → report), else the
  caller's wall clock. The report's ``timings_ms.total`` is recorded alongside
  but NOT gated: measured 2026-09-26 it covers the report build only (layout,
  classify, extract, llm, quality — 11.7 ms on a photo whose ingest took
  2447 ms), and the plan's ceilings are about parsing staying fast.
  Percentiles are nearest-rank.

Gates (plan §8): routing ≥ 95 %, anchoring ≥ 90 %, Red-Hat recall ≥ 85 %, replay
100 %, P95 latency policy_form < 5 s, claim_packet < 8 s, photo_signature < 10 s,
mixed_bundle < 15 s. A metric with no cases prints ``n/a`` and neither passes nor
fails. Exit status 1 when any gate fails, 2 when nothing ran.

Results: ``bench/results/<UTC timestamp>-<git sha>.json`` plus a Markdown
summary on stdout. Options: ``--class``, ``--case`` (repeatable filters),
``--baseline <file>`` (diff against a previous result), ``--llm`` (enable the
grounded LLM pass; off by default because the local Ollama may be absent),
``--no-replay``, ``--dump-dir`` (write the generated inputs for inspection),
``--no-write``.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BENCH_DIR = ROOT / "bench"
MANIFEST_PATH = BENCH_DIR / "manifest.json"
RESULTS_DIR = BENCH_DIR / "results"
REAL_CASES_DIR = BENCH_DIR / "cases" / "real"

CLASSES = ("policies", "claims", "photos", "signatures", "handwritten", "mixed")
LATENCY_CLASSES = ("policy_form", "claim_packet", "photo_signature", "mixed_bundle")
#: The plan's P95 ceilings, milliseconds. The manifest may restate them; when
#: it does the manifest wins so a re-frozen set carries its own gates.
DEFAULT_LATENCY_P95_MS = {"policy_form": 5000, "claim_packet": 8000, "photo_signature": 10000, "mixed_bundle": 15000}
DEFAULT_GATES = {"routing_accuracy": 0.95, "anchoring_rate": 0.90, "redhat_recall": 0.85, "replay_determinism": 1.0}

log = logging.getLogger("benchmark")


# --------------------------------------------------------------------------- #
# Manifest
# --------------------------------------------------------------------------- #

def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate_manifest(manifest: dict[str, Any]) -> list[str]:
    """Structural problems as sentences; an empty list means the set is usable."""
    errors: list[str] = []
    if not re.fullmatch(r"bench-v\d+", str(manifest.get("version") or "")):
        errors.append(f"version must look like bench-vN, got {manifest.get('version')!r}")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(manifest.get("frozen_at") or "")):
        errors.append("frozen_at must be an ISO date")
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        return errors + ["cases must be a non-empty list"]
    ids = [c.get("id") for c in cases]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        errors.append(f"duplicate case ids: {dupes}")
    seen_classes = set()
    for c in cases:
        cid = c.get("id") or "<no id>"
        if not re.fullmatch(r"[a-z0-9][a-z0-9\-]*", str(c.get("id") or "")):
            errors.append(f"{cid}: id must be lowercase kebab-case")
        if c.get("class") not in CLASSES:
            errors.append(f"{cid}: class {c.get('class')!r} not in {CLASSES}")
        else:
            seen_classes.add(c["class"])
        if c.get("latency_class") not in LATENCY_CLASSES:
            errors.append(f"{cid}: latency_class {c.get('latency_class')!r} not in {LATENCY_CLASSES}")
        if not c.get("family"):
            errors.append(f"{cid}: family missing")
        src = c.get("source") or {}
        if src.get("kind") not in ("synthetic", "s3"):
            errors.append(f"{cid}: source.kind must be synthetic or s3")
        inputs = c.get("inputs")
        if not isinstance(inputs, list) or not inputs:
            errors.append(f"{cid}: inputs must be a non-empty list")
            continue
        for i, inp in enumerate(inputs):
            exp = inp.get("expected")
            if not isinstance(exp, dict) or "document_type" not in exp:
                errors.append(f"{cid}[{i}]: expected.document_type missing")
            if src.get("kind") == "synthetic" and not inp.get("generator"):
                errors.append(f"{cid}[{i}]: synthetic input needs a generator")
            if src.get("kind") == "s3" and not (src.get("key") or "").startswith("s3://"):
                errors.append(f"{cid}: s3 source needs an s3:// key")
            fields = (exp or {}).get("fields")
            if fields is not None and not isinstance(fields, dict):
                errors.append(f"{cid}[{i}]: expected.fields must be an object or null")
        if not isinstance(c.get("seeded_conflicts"), list):
            errors.append(f"{cid}: seeded_conflicts must be a list")
        elif c["seeded_conflicts"] and len(inputs) < 2:
            errors.append(f"{cid}: a seeded conflict needs at least two inputs")
    missing = [k for k in CLASSES if k not in seen_classes]
    if missing:
        errors.append(f"classes with no case: {missing}")
    return errors


def select_cases(manifest: dict[str, Any], *, classes: list[str] | None = None, ids: list[str] | None = None) -> list[dict[str, Any]]:
    out = []
    for c in manifest["cases"]:
        if classes and c.get("class") not in classes:
            continue
        if ids and c.get("id") not in ids:
            continue
        out.append(c)
    return out


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #

def _generators():
    if str(BENCH_DIR) not in sys.path:
        sys.path.insert(0, str(BENCH_DIR))
    from cases import generators  # type: ignore[import-not-found]

    return generators


def materialise_input(case: dict[str, Any], inp: dict[str, Any]) -> tuple[str, bytes, dict[str, Any]] | None:
    """The bytes for one input, or None (with the reason in ``inp['_skip']``)."""
    kind = (case.get("source") or {}).get("kind")
    if kind == "synthetic":
        filename, data, meta = _generators().build(inp["generator"])
        return filename, data, meta
    # Real document: a local drop-in under bench/cases/real/<case id>/ wins,
    # then the bucket named by ASSURE_BENCH_BUCKET; otherwise the placeholder
    # is skipped and says so.
    filename = inp.get("filename") or (case["source"].get("key") or "").rsplit("/", 1)[-1]
    local = REAL_CASES_DIR / case["id"] / filename
    if local.exists():
        return filename, local.read_bytes(), {"origin": str(local.relative_to(ROOT))}
    bucket = os.environ.get("ASSURE_BENCH_BUCKET", "").strip()
    key = str(case["source"].get("key") or "").replace("${ASSURE_BENCH_BUCKET}", bucket)
    if bucket and key.startswith("s3://"):
        try:
            import boto3  # type: ignore

            b, k = key[5:].split("/", 1)
            body = boto3.client("s3").get_object(Bucket=b, Key=k)["Body"].read()
            return filename, body, {"origin": key}
        except Exception as exc:  # noqa: BLE001 — a missing object is a skip, not a crash
            inp["_skip"] = f"s3 fetch failed: {type(exc).__name__}: {exc}"
            return None
    inp["_skip"] = "placeholder: real document not dropped in (bench/cases/real/<case id>/ or ASSURE_BENCH_BUCKET)"
    return None


# --------------------------------------------------------------------------- #
# Scoring helpers (pure; unit-tested)
# --------------------------------------------------------------------------- #

def _norm_str(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value)).strip().lower()


def same_value(actual: Any, expected: Any) -> bool:
    """Golden-set equality: numbers within 0.005, strings whitespace/case-insensitive, lists as sets."""
    if isinstance(expected, list):
        return isinstance(actual, list) and {_norm_str(x) for x in actual} == {_norm_str(x) for x in expected}
    if isinstance(expected, bool):
        return actual is expected
    if isinstance(expected, (int, float)):
        return isinstance(actual, (int, float)) and not isinstance(actual, bool) and abs(float(actual) - float(expected)) < 0.005
    if expected is None or actual is None:
        return actual is expected
    return _norm_str(actual) == _norm_str(expected)


def report_family(report: dict[str, Any]) -> str | None:
    """The family the report's type belongs to; the cue family for types outside the taxonomy."""
    cls = report.get("classification") or {}
    doc_type = str(cls.get("document_type") or "")
    try:
        from prompt_matrix.services.field_extractor import DOCUMENT_FAMILIES, TYPE_FAMILY
    except Exception:  # noqa: BLE001 — scoring a stored result without the package
        TYPE_FAMILY, DOCUMENT_FAMILIES = {}, ()
    if doc_type in TYPE_FAMILY:
        return TYPE_FAMILY[doc_type]
    if doc_type.endswith("_unknown") and doc_type[: -len("_unknown")] in DOCUMENT_FAMILIES:
        return doc_type[: -len("_unknown")]
    validation = cls.get("validation") if isinstance(cls.get("validation"), dict) else {}
    cue_family = cls.get("family") if isinstance(cls.get("family"), dict) else {}
    fam = validation.get("family") or cue_family.get("family")
    return fam if fam and fam != "unknown" else None


def _segments_match(report: dict[str, Any], expected_docs: list[dict[str, Any]]) -> bool:
    docs = report.get("documents") or []
    if len(docs) != len(expected_docs):
        return False
    for got, want in zip(docs, expected_docs):
        if got.get("document_type") != want.get("document_type"):
            return False
        if want.get("pages") is not None and list(got.get("pages") or []) != list(want["pages"]):
            return False
    return True


def score_fields(fields: list[dict[str, Any]], expected: dict[str, Any] | None) -> dict[str, Any]:
    """Recall over ``expected`` (name → value) and anchoring over the found fields."""
    by_name: dict[str, dict[str, Any]] = {}
    for f in fields:
        by_name.setdefault(str(f.get("name")), f)
    misses: list[dict[str, Any]] = []
    hits = 0
    expected = expected or {}
    for name, want in expected.items():
        f = by_name.get(name) or {}
        if same_value(f.get("value"), want):
            hits += 1
        else:
            misses.append({"field": name, "expected": want, "got": f.get("value"), "method": f.get("extraction_method"), "reason": f.get("reason")})
    found = [f for f in fields if f.get("value") is not None]
    anchored = [f for f in found if f.get("field_source_node_id") or f.get("tree_node_id")]
    return {
        "fields_expected": len(expected),
        "fields_hit": hits,
        "misses": misses,
        "found_count": len(found),
        "anchored_count": len(anchored),
        "found_with_element_id": sum(1 for f in found if f.get("element_id")),
    }


def evaluate_input(expected: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    """Routing, family, recall, anchoring and signature outcome for one report."""
    cls = report.get("classification") or {}
    got_type = cls.get("document_type")
    want_type = expected.get("document_type")
    routing_ok = got_type == want_type
    segments_ok = None
    if expected.get("documents"):
        segments_ok = _segments_match(report, expected["documents"])
        routing_ok = routing_ok and segments_ok
    fam = report_family(report)
    family_ok = (fam == expected.get("family")) if expected.get("family") else None
    fields = report.get("fields") or []
    if expected.get("documents"):
        # A bundle: score each expected segment against the fields of the
        # segment with that index; an unsegmented report is scored whole.
        segmented = len(report.get("documents") or []) > 1
        agg = {"fields_expected": 0, "fields_hit": 0, "misses": [], "found_count": 0, "anchored_count": 0, "found_with_element_id": 0}
        for idx, seg in enumerate(expected["documents"]):
            seg_fields = [f for f in fields if f.get("segment") == idx] if segmented else fields
            s = score_fields(seg_fields, seg.get("fields"))
            for k in ("fields_expected", "fields_hit"):
                agg[k] += s[k]
            agg["misses"].extend({**m, "segment": idx} for m in s["misses"])
        whole = score_fields(fields, None)
        agg.update({k: whole[k] for k in ("found_count", "anchored_count", "found_with_element_id")})
        scored = agg
    else:
        scored = score_fields(fields, expected.get("fields"))
    sig = (report.get("quality_report") or {}).get("signature") or {}
    signature_ok = None
    if expected.get("signature_present") is not None:
        signature_ok = sig.get("present") is expected["signature_present"]
    return {
        "document_type": got_type,
        "expected_type": want_type,
        "routing_ok": bool(routing_ok),
        "segments_ok": segments_ok,
        "family": fam,
        "expected_family": expected.get("family"),
        "family_ok": family_ok,
        "schema_gap": expected.get("schema_exists") is False,
        "classification_method": cls.get("method") or "keywords",
        "classification_basis": cls.get("basis"),
        "signature_ok": signature_ok,
        "signature": {k: sig.get(k) for k in ("present", "quality")} if sig else None,
        **scored,
    }


def node_id_multiset(report: dict[str, Any]) -> Counter:
    """The saved-tree node ids (``field_source_node_id`` on fields, ``node_id``
    on layout segments) — the identity a *re-parse* must reproduce for the
    Review page's deep links to survive a replay."""
    fields = report.get("fields") or []
    layout = report.get("_layout") or []
    return Counter([str(f.get("field_source_node_id") or "") for f in fields] + [str(seg.get("node_id") or "") for page in layout for seg in page])


def identity_multiset(report: dict[str, Any]) -> tuple[str, Counter]:
    """The identity multiset replay must reproduce: ``element_id`` when the
    fields/layout carry it (identity agent), else ``field_source_node_id``."""
    fields = report.get("fields") or []
    layout = report.get("_layout") or []
    if any(f.get("element_id") for f in fields) or any(seg.get("element_id") for page in layout for seg in page):
        key = "element_id"
        ids = [str(f.get("element_id") or "") for f in fields] + [str(seg.get("element_id") or "") for page in layout for seg in page]
    else:
        key = "field_source_node_id"
        ids = [str(f.get("field_source_node_id") or "") for f in fields] + [str(seg.get("node_id") or "") for page in layout for seg in page]
    return key, Counter(ids)


def value_signature(report: dict[str, Any]) -> tuple:
    return tuple(sorted((str(f.get("name")), json.dumps(f.get("value"), sort_keys=True, default=str)) for f in report.get("fields") or []))


def conflicts_found(report: dict[str, Any], seeded: list[str]) -> dict[str, Any]:
    """Which seeded conflict fields the last report of a pair surfaced, and where."""
    found: dict[str, str] = {}
    for c in report.get("conflicts") or []:
        if c.get("kind", "cross_document") == "cross_document" and c.get("field") in seeded:
            found.setdefault(str(c["field"]), "conflicts[]")
    redhat = report.get("redhat") if isinstance(report.get("redhat"), dict) else None
    findings = (redhat or {}).get("findings") or []
    for f in findings:
        # services/redhat_graph names the rule ``cross_document_conflict``
        # (its ``class`` is the severity family, ``evidentiary``) and anchors
        # the finding on the field; older shapes may carry the class name or a
        # top-level field. All three are read.
        if not isinstance(f, dict):
            continue
        if "cross_document_conflict" not in (f.get("rule"), f.get("class")):
            continue
        anchor = f.get("anchor") if isinstance(f.get("anchor"), dict) else {}
        fld = f.get("field") or f.get("field_name") or anchor.get("field")
        if fld in seeded:
            found.setdefault(str(fld), "redhat.findings")
    return {
        "seeded": list(seeded),
        "found": found,
        "missing": [s for s in seeded if s not in found],
        "redhat_block_present": redhat is not None,
        "redhat_status": (report.get("verification") or {}).get("redhat_status"),
    }


def latency_ms(report: dict[str, Any], result: dict[str, Any], wall_ms: float) -> tuple[float, str]:
    """The gated latency: end-to-end ingest. ``report["timings_ms"]`` is the
    orchestrator's stage clock (report build only) and is kept as a detail."""
    if isinstance(result.get("duration_ms"), (int, float)):
        return float(result["duration_ms"]), "ingest.duration_ms"
    return float(wall_ms), "wall_clock"


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile; None for no values."""
    if not values:
        return None
    s = sorted(values)
    rank = max(1, math.ceil(p * len(s)))
    return s[min(rank, len(s)) - 1]


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def aggregate(case_results: list[dict[str, Any]], *, latency_p95_ms: dict[str, int] | None = None) -> dict[str, Any]:
    """Per-class, per-latency-class and overall metrics from the case results."""
    latency_p95_ms = latency_p95_ms or DEFAULT_LATENCY_P95_MS
    buckets: dict[str, dict[str, Any]] = {}

    def bucket(name: str) -> dict[str, Any]:
        return buckets.setdefault(name, {
            "inputs": 0, "errors": 0, "routing_ok": 0, "routing_n": 0, "schema_gaps": 0, "schema_gap_misses": 0, "family_ok": 0, "family_n": 0,
            "fields_expected": 0, "fields_hit": 0, "found": 0, "anchored": 0, "signature_ok": 0, "signature_n": 0,
            "seeded": 0, "conflicts_found": 0, "replay_n": 0, "replay_identical": 0, "replay_values_identical": 0, "replay_node_ids_identical": 0,
            "latency_class_agree": 0, "latency_class_n": 0, "latencies": [],
        })

    lat_by_class: dict[str, list[float]] = {}
    ran_cases = skipped = 0
    for case in case_results:
        if case.get("status") == "skipped":
            skipped += 1
            continue
        ran_cases += 1
        names = (case["class"], "overall")
        for inp in case.get("inputs") or []:
            for name in names:
                b = bucket(name)
                b["inputs"] += 1
                if inp.get("status") == "error":
                    b["errors"] += 1
                    b["routing_n"] += 1  # a document that did not ingest was not routed
                    b["fields_expected"] += int(inp.get("fields_expected") or 0)
                    continue
                b["routing_n"] += 1
                b["routing_ok"] += int(bool(inp.get("routing_ok")))
                if inp.get("schema_gap"):
                    b["schema_gaps"] += 1
                    b["schema_gap_misses"] += int(not inp.get("routing_ok"))
                if inp.get("family_ok") is not None:
                    b["family_n"] += 1
                    b["family_ok"] += int(bool(inp["family_ok"]))
                b["fields_expected"] += int(inp.get("fields_expected") or 0)
                b["fields_hit"] += int(inp.get("fields_hit") or 0)
                b["found"] += int(inp.get("found_count") or 0)
                b["anchored"] += int(inp.get("anchored_count") or 0)
                if inp.get("signature_ok") is not None:
                    b["signature_n"] += 1
                    b["signature_ok"] += int(bool(inp["signature_ok"]))
                if inp.get("replay_identical") is not None:
                    b["replay_n"] += 1
                    b["replay_identical"] += int(bool(inp["replay_identical"]))
                    b["replay_values_identical"] += int(bool(inp.get("replay_values_identical")))
                    b["replay_node_ids_identical"] += int(bool(inp.get("replay_node_ids_identical")))
                if inp.get("latency_class_agrees") is not None:
                    b["latency_class_n"] += 1
                    b["latency_class_agree"] += int(bool(inp["latency_class_agrees"]))
                if isinstance(inp.get("latency_ms"), (int, float)):
                    b["latencies"].append(float(inp["latency_ms"]))
            if isinstance(inp.get("latency_ms"), (int, float)) and inp.get("status") != "error":
                lat_by_class.setdefault(case.get("latency_class") or "unknown", []).append(float(inp["latency_ms"]))
        conf = case.get("conflicts") or {}
        for name in names:
            b = bucket(name)
            b["seeded"] += len(conf.get("seeded") or [])
            b["conflicts_found"] += len(conf.get("found") or {})

    def finish(b: dict[str, Any]) -> dict[str, Any]:
        lat = b.pop("latencies")
        return {
            **b,
            "routing_accuracy": _ratio(b["routing_ok"], b["routing_n"]),
            # A schema-gap input can never route correctly (its type is not in
            # the taxonomy), so routing_ok already excludes it.
            "routing_accuracy_existing_schemas": _ratio(b["routing_ok"], b["routing_n"] - b["schema_gaps"]),
            "family_accuracy": _ratio(b["family_ok"], b["family_n"]),
            "extraction_recall": _ratio(b["fields_hit"], b["fields_expected"]),
            "anchoring_rate": _ratio(b["anchored"], b["found"]),
            "signature_accuracy": _ratio(b["signature_ok"], b["signature_n"]),
            "redhat_recall": _ratio(b["conflicts_found"], b["seeded"]),
            "replay_determinism": _ratio(b["replay_identical"], b["replay_n"]),
            "replay_value_determinism": _ratio(b["replay_values_identical"], b["replay_n"]),
            "replay_node_id_determinism": _ratio(b["replay_node_ids_identical"], b["replay_n"]),
            "latency_class_agreement": _ratio(b["latency_class_agree"], b["latency_class_n"]),
            "latency_n": len(lat),
            "latency_p50_ms": percentile(lat, 0.50),
            "latency_p95_ms": percentile(lat, 0.95),
        }

    per_class = {k: finish(v) for k, v in buckets.items() if k != "overall"}
    overall = finish(buckets["overall"]) if "overall" in buckets else finish(bucket("overall"))
    per_latency = {
        name: {"n": len(vals), "p50_ms": percentile(vals, 0.5), "p95_ms": percentile(vals, 0.95), "ceiling_ms": latency_p95_ms.get(name)}
        for name, vals in sorted(lat_by_class.items())
    }
    for name in LATENCY_CLASSES:
        per_latency.setdefault(name, {"n": 0, "p50_ms": None, "p95_ms": None, "ceiling_ms": latency_p95_ms.get(name)})
    return {"per_class": per_class, "overall": overall, "per_latency_class": per_latency, "cases_run": ran_cases, "cases_skipped": skipped}


def evaluate_gates(metrics: dict[str, Any], *, gates: dict[str, float] | None = None, latency_p95_ms: dict[str, int] | None = None) -> list[dict[str, Any]]:
    """PASS / FAIL / n/a per gate; ``n/a`` when the metric has no cases."""
    gates = {**DEFAULT_GATES, **(gates or {})}
    latency_p95_ms = {**DEFAULT_LATENCY_P95_MS, **(latency_p95_ms or {})}
    out: list[dict[str, Any]] = []
    overall = metrics.get("overall") or {}
    for name, threshold in gates.items():
        value = overall.get(name)
        status = "n/a" if value is None else ("PASS" if value >= threshold else "FAIL")
        out.append({"gate": name, "scope": "overall", "threshold": threshold, "value": value, "status": status, "comparison": ">="})
    for lat_class, ceiling in latency_p95_ms.items():
        p95 = (metrics.get("per_latency_class") or {}).get(lat_class, {}).get("p95_ms")
        status = "n/a" if p95 is None else ("PASS" if p95 < ceiling else "FAIL")
        out.append({"gate": "latency_p95_ms", "scope": lat_class, "threshold": ceiling, "value": p95, "status": status, "comparison": "<"})
    return out


def fmt_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.0f} %"


def fmt_ms(value: float | None) -> str:
    return "n/a" if value is None else f"{value / 1000:.2f} s"


def render_markdown(result: dict[str, Any]) -> str:
    m = result["metrics"]
    lines = [
        f"## Parsure benchmark {result['benchmark_version']} — {result['git_sha']} — {result['run_at']}",
        "",
        f"cases run {m['cases_run']}, skipped {m['cases_skipped']} (placeholders); LLM pass {'on' if result['options'].get('llm') else 'off'}; "
        f"schema `{result['env'].get('pg_schema')}`; scan backend `{result['env'].get('scan_backend')}`",
        "",
        "| class | inputs | routing | family | recall | anchoring | red-hat | replay | p50 | p95 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    rows = [(k, m["per_class"][k]) for k in CLASSES if k in m["per_class"]] + [("**overall**", m["overall"])]
    for name, b in rows:
        routing = fmt_pct(b["routing_accuracy"])
        if b.get("schema_gap_misses"):
            routing += f" ({b['schema_gap_misses']} schema-gap)"
        lines.append(
            f"| {name} | {b['inputs']}{' (' + str(b['errors']) + ' err)' if b['errors'] else ''} | {routing} | {fmt_pct(b['family_accuracy'])} | "
            f"{fmt_pct(b['extraction_recall'])} ({b['fields_hit']}/{b['fields_expected']}) | {fmt_pct(b['anchoring_rate'])} ({b['anchored']}/{b['found']}) | "
            f"{fmt_pct(b['redhat_recall'])} ({b['conflicts_found']}/{b['seeded']}) | {fmt_pct(b['replay_determinism'])} | "
            f"{fmt_ms(b['latency_p50_ms'])} | {fmt_ms(b['latency_p95_ms'])} |"
        )
    lines += ["", "| latency class | n | p50 | p95 | ceiling |", "|---|---|---|---|---|"]
    for name in LATENCY_CLASSES:
        b = m["per_latency_class"].get(name) or {}
        lines.append(f"| {name} | {b.get('n', 0)} | {fmt_ms(b.get('p50_ms'))} | {fmt_ms(b.get('p95_ms'))} | < {fmt_ms(b.get('ceiling_ms'))} |")
    lines += ["", "| gate | scope | threshold | measured | status |", "|---|---|---|---|---|"]
    for g in result["gates"]:
        if g["gate"] == "latency_p95_ms":
            thr, val = f"< {fmt_ms(g['threshold'])}", fmt_ms(g["value"])
        else:
            thr, val = f">= {fmt_pct(g['threshold'])}", fmt_pct(g["value"])
        lines.append(f"| {g['gate']} | {g['scope']} | {thr} | {val} | **{g['status']}** |")
    notes = result.get("notes") or []
    if notes:
        lines += ["", "Notes:"] + [f"- {n}" for n in notes]
    failures = [c for c in result["cases"] if c.get("status") == "run" and (
        any(not i.get("routing_ok") or i.get("misses") or i.get("status") == "error" or i.get("replay_identical") is False for i in c["inputs"])
        or (c.get("conflicts") or {}).get("missing"))]
    if failures:
        lines += ["", "Failing cases:"]
        for c in failures:
            parts = []
            for i in c["inputs"]:
                if i.get("status") == "error":
                    parts.append(f"{i['filename']}: ERROR {i.get('error')}")
                    continue
                bits = []
                if not i.get("routing_ok"):
                    bits.append(f"routed {i.get('document_type')} (expected {i.get('expected_type')}{', schema gap' if i.get('schema_gap') else ''})")
                if i.get("misses"):
                    bits.append("missed " + ", ".join(m["field"] for m in i["misses"]))
                if i.get("replay_identical") is False:
                    bits.append(f"replay ids differ ({i.get('replay_identity_key')})")
                if i.get("signature_ok") is False:
                    bits.append(f"signature {i.get('signature')}")
                if bits:
                    parts.append(f"{i['filename']}: " + "; ".join(bits))
            if (c.get("conflicts") or {}).get("missing"):
                parts.append("conflicts not surfaced: " + ", ".join(c["conflicts"]["missing"]))
            lines.append(f"- `{c['id']}` — " + " | ".join(parts))
    if result.get("baseline_diff"):
        d = result["baseline_diff"]
        lines += ["", f"Against baseline `{d['baseline']}`:"]
        for line in d["lines"]:
            lines.append(f"- {line}")
    return "\n".join(lines)


def diff_against_baseline(current: dict[str, Any], baseline: dict[str, Any], baseline_path: str) -> dict[str, Any]:
    lines: list[str] = []
    cur, base = current["metrics"], baseline.get("metrics") or {}
    for name in list(CLASSES) + ["overall"]:
        c = cur["per_class"].get(name) if name != "overall" else cur["overall"]
        b = (base.get("per_class") or {}).get(name) if name != "overall" else base.get("overall")
        if not c or not b:
            continue
        for metric in ("routing_accuracy", "extraction_recall", "anchoring_rate", "redhat_recall", "replay_determinism", "latency_p95_ms"):
            cv, bv = c.get(metric), b.get(metric)
            if cv is None or bv is None or abs(cv - bv) < 1e-9:
                continue
            if metric.endswith("_ms"):
                lines.append(f"{name} {metric}: {fmt_ms(bv)} → {fmt_ms(cv)}")
            else:
                lines.append(f"{name} {metric}: {fmt_pct(bv)} → {fmt_pct(cv)}")
    base_cases = {c["id"]: c for c in baseline.get("cases") or []}
    for c in current["cases"]:
        b = base_cases.get(c["id"])
        if not b or c.get("status") != "run" or b.get("status") != "run":
            continue
        for ci, bi in zip(c["inputs"], b.get("inputs") or []):
            if ci.get("routing_ok") != bi.get("routing_ok"):
                lines.append(f"{c['id']} {ci['filename']}: routing {'fixed' if ci.get('routing_ok') else 'REGRESSED'} ({bi.get('document_type')} → {ci.get('document_type')})")
            cm = {m["field"] for m in ci.get("misses") or []}
            bm = {m["field"] for m in bi.get("misses") or []}
            if cm - bm:
                lines.append(f"{c['id']} {ci['filename']}: newly missed {sorted(cm - bm)}")
            if bm - cm:
                lines.append(f"{c['id']} {ci['filename']}: now found {sorted(bm - cm)}")
    cur_gates = {(g["gate"], g["scope"]): g["status"] for g in current["gates"]}
    for g in baseline.get("gates") or []:
        now = cur_gates.get((g["gate"], g["scope"]))
        if now and now != g["status"]:
            lines.append(f"gate {g['gate']} [{g['scope']}]: {g['status']} → {now}")
    return {"baseline": baseline_path, "lines": lines or ["no change"]}


# --------------------------------------------------------------------------- #
# Running the pipeline
# --------------------------------------------------------------------------- #

def _git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=5).stdout.strip() or "nogit"
    except Exception:  # noqa: BLE001
        return "nogit"


def configure_environment(*, llm: bool) -> dict[str, Any]:
    """Pin the process to the inline pipeline. Must run before prompt_matrix imports."""
    if not (os.environ.get("DATABASE_URL") or "").strip():
        sys.exit("DATABASE_URL is required (e.g. postgresql://assure:assure@localhost:5432/assure); the benchmark runs the real pipeline against PostgreSQL")
    os.environ["PARSE_ASYNC"] = "0"
    os.environ.setdefault("CELERY_BROKER_URL", "")
    os.environ["PARSURE_LLM_EXTRACTION"] = "1" if llm else "0"
    if not (os.environ.get("ASSURE_DATA_DIR") or "").strip():
        os.environ["ASSURE_DATA_DIR"] = tempfile.mkdtemp(prefix="assure-bench-")
    if not (os.environ.get("ASSURE_PG_SCHEMA") or "").strip():
        log.warning("ASSURE_PG_SCHEMA is not set: throwaway projects land in the public schema. Use ASSURE_PG_SCHEMA=bench_run.")
    return {
        "database": re.sub(r"//[^@]*@", "//<redacted>@", os.environ["DATABASE_URL"]),
        "pg_schema": os.environ.get("ASSURE_PG_SCHEMA") or "public",
        "data_dir": os.environ["ASSURE_DATA_DIR"],
        "llm_extraction": llm,
        "scan_backend": None,  # filled after import
        "jdf_ocr": os.environ.get("JDF_OCR") or "tesseract",
        "s3_bucket": bool((os.environ.get("ASSURE_S3_BUCKET") or "").strip()),
    }


def run_input(project_id: str, filename: str, data: bytes) -> tuple[dict[str, Any], dict[str, Any], float]:
    """Ingest one file into ``project_id``; returns (report, ingest result, wall ms)."""
    from prompt_matrix.db import parsure_repository as repo
    from prompt_matrix.history import db_scope
    from prompt_matrix.services.pdf_ingest import ingest_pdf_for_project

    started = time.perf_counter()
    with db_scope():
        result = ingest_pdf_for_project(project_id, filename, data)
        wall = (time.perf_counter() - started) * 1000
        report_id = result.get("parsure_report_id")
        report = repo.get_report(project_id, report_id) if report_id else None
    if report is None:
        raise RuntimeError("ingest returned no Parsure report (run_after_parse failed; see log)")
    return report, result, wall


def run_case(case: dict[str, Any], *, run_id: str, replay: bool, dump_dir: Path | None, progress) -> dict[str, Any]:
    out: dict[str, Any] = {"id": case["id"], "class": case["class"], "family": case["family"], "latency_class": case["latency_class"],
                           "source": case["source"], "status": "run", "inputs": [], "conflicts": None}
    project_a = f"bench-{run_id}-{case['id']}"
    project_b = f"{project_a}-r2"
    last_report = None
    for idx, inp in enumerate(case["inputs"]):
        expected = inp.get("expected") or {}
        material = materialise_input(case, inp)
        if material is None:
            out["status"] = "skipped"
            out["skip_reason"] = inp.get("_skip")
            out["inputs"] = []
            return out
        filename, data, meta = material
        if dump_dir is not None:
            target = dump_dir / case["id"]
            target.mkdir(parents=True, exist_ok=True)
            (target / filename).write_bytes(data)
        rec: dict[str, Any] = {"index": idx, "filename": filename, "generator": inp.get("generator"), "size_bytes": len(data), "meta": meta, "status": "run",
                               "fields_expected": len(expected.get("fields") or {}) + sum(len(d.get("fields") or {}) for d in expected.get("documents") or [])}
        try:
            report, result, wall = run_input(project_a, filename, data)
        except Exception as exc:  # noqa: BLE001 — the failure IS the measurement
            rec.update({"status": "error", "error": f"{type(exc).__name__}: {str(exc)[:300]}", "routing_ok": False, "fields_hit": 0, "misses": [],
                        "expected_type": expected.get("document_type")})
            out["inputs"].append(rec)
            progress(case, rec)
            continue
        rec.update(evaluate_input(expected, report))
        rec["parser_name"] = report.get("parser_name")
        rec["material_type"], rec["modality"] = report.get("material_type"), report.get("modality")
        rec["page_count"] = report.get("page_count")
        rec["ocr_confidence"] = result.get("ocr_confidence")
        rec["quality_flags"] = report.get("quality_flags")
        rec["document_quality_score"] = report.get("document_quality_score")
        rec["laya_route"] = (report.get("laya") or {}).get("suggested_route")
        rec["latency_ms"], rec["latency_source"] = latency_ms(report, result, wall)
        rec["wall_ms"] = round(wall, 1)
        rec["report_stage_timings_ms"] = report.get("timings_ms") if isinstance(report.get("timings_ms"), dict) else None
        rec["report_latency_class"] = report.get("latency_class")
        rec["latency_class_agrees"] = (report.get("latency_class") == case["latency_class"]) if report.get("latency_class") else None
        rec["report_id"] = report.get("report_id")
        if meta.get("not_extractable_by_design"):
            rec["not_extractable_by_design"] = True
        if replay:
            try:
                report2, _res2, _wall2 = run_input(project_b, filename, data)
                key, ids1 = identity_multiset(report)
                _key2, ids2 = identity_multiset(report2)
                rec["replay_identity_key"] = key
                rec["replay_identical"] = ids1 == ids2
                rec["replay_values_identical"] = value_signature(report) == value_signature(report2)
                rec["replay_node_ids_identical"] = node_id_multiset(report) == node_id_multiset(report2)
                rec["replay_type_identical"] = (report2.get("classification") or {}).get("document_type") == rec["document_type"]
            except Exception as exc:  # noqa: BLE001
                rec["replay_identical"] = False
                rec["replay_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        last_report = report
        out["inputs"].append(rec)
        progress(case, rec)
    if case.get("seeded_conflicts") and last_report is not None:
        out["conflicts"] = conflicts_found(last_report, list(case["seeded_conflicts"]))
    elif case.get("seeded_conflicts"):
        out["conflicts"] = {"seeded": list(case["seeded_conflicts"]), "found": {}, "missing": list(case["seeded_conflicts"]), "redhat_block_present": False}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--manifest", default=str(MANIFEST_PATH))
    ap.add_argument("--class", dest="classes", action="append", choices=CLASSES, help="run only this class (repeatable)")
    ap.add_argument("--case", dest="cases", action="append", help="run only this case id (repeatable)")
    ap.add_argument("--baseline", help="a previous bench/results/*.json to diff against")
    ap.add_argument("--llm", action="store_true", help="enable the grounded LLM pass (PARSURE_LLM_EXTRACTION=1)")
    ap.add_argument("--no-replay", action="store_true", help="skip the second run per input (no determinism figure)")
    ap.add_argument("--dump-dir", help="write the generated inputs here for inspection")
    ap.add_argument("--results-dir", default=str(RESULTS_DIR))
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    if not args.verbose:
        for name in ("prompt_matrix", "celery", "kombu", "botocore", "urllib3"):
            logging.getLogger(name).setLevel(logging.ERROR)

    manifest = load_manifest(Path(args.manifest))
    problems = validate_manifest(manifest)
    if problems:
        for p in problems:
            print(f"manifest: {p}", file=sys.stderr)
        return 2
    selected = select_cases(manifest, classes=args.classes, ids=args.cases)
    if not selected:
        print("no case matches the filters", file=sys.stderr)
        return 2

    env = configure_environment(llm=args.llm)
    sys.path.insert(0, str(ROOT))
    from prompt_matrix.services import parser_router  # noqa: E402  (after env is pinned)

    env["scan_backend"] = parser_router.scan_backend()
    from prompt_matrix.services.v1_orchestrator import current_jdf_cli_version

    env["jdf_cli_version"] = current_jdf_cli_version()

    run_at = datetime.now(timezone.utc).replace(microsecond=0)
    run_id = run_at.strftime("%Y%m%dT%H%M%SZ")
    sha = _git_sha()
    dump_dir = Path(args.dump_dir) if args.dump_dir else None
    total_inputs = sum(len(c["inputs"]) for c in selected)
    done = [0]

    def progress(case: dict[str, Any], rec: dict[str, Any]) -> None:
        done[0] += 1
        if rec.get("status") == "error":
            line = f"ERROR {rec.get('error')}"
        else:
            line = (f"{rec.get('parser_name')} {rec.get('latency_ms', 0) / 1000:.2f}s → {rec.get('document_type')} "
                    f"{'ok' if rec.get('routing_ok') else 'MISS'}; fields {rec.get('fields_hit')}/{rec.get('fields_expected')}; "
                    f"anchored {rec.get('anchored_count')}/{rec.get('found_count')}"
                    + (f"; replay {'same' if rec.get('replay_identical') else 'DIFFERENT'}" if rec.get("replay_identical") is not None else ""))
        print(f"[{done[0]:2d}/{total_inputs}] {case['id']} / {rec['filename']}: {line}", file=sys.stderr, flush=True)

    case_results = []
    for case in selected:
        res = run_case(case, run_id=run_id, replay=not args.no_replay, dump_dir=dump_dir, progress=progress)
        if res["status"] == "skipped":
            print(f"[ skip ] {case['id']}: {res.get('skip_reason')}", file=sys.stderr, flush=True)
        case_results.append(res)

    latency_ceilings = {**DEFAULT_LATENCY_P95_MS, **{k: int(v) for k, v in (manifest.get("latency_classes") or {}).items()}}
    gates_cfg = {**DEFAULT_GATES, **{k: float(v) for k, v in (manifest.get("gates") or {}).items()}}
    metrics = aggregate(case_results, latency_p95_ms=latency_ceilings)
    gates = evaluate_gates(metrics, gates=gates_cfg, latency_p95_ms=latency_ceilings)

    notes: list[str] = []
    gaps = metrics["overall"].get("schema_gaps") or 0
    if gaps:
        notes.append(f"{gaps} input(s) expect a document type with no schema in field_extractor.DOCUMENT_TYPES yet (routing misses counted, flagged schema-gap); "
                     f"routing over existing schemas only: {fmt_pct(metrics['overall'].get('routing_accuracy_existing_schemas'))}")
    if metrics["overall"].get("replay_n"):
        keys = Counter(i.get("replay_identity_key") for c in case_results for i in c.get("inputs") or [] if i.get("replay_identity_key"))
        notes.append(f"replay identity compared on {dict(keys)}; value determinism {fmt_pct(metrics['overall'].get('replay_value_determinism'))}; "
                     f"saved-tree node-id determinism (field_source_node_id / layout node_id) {fmt_pct(metrics['overall'].get('replay_node_id_determinism'))}")
    redhat_blocks = sum(1 for c in case_results if (c.get("conflicts") or {}).get("redhat_block_present"))
    if metrics["overall"].get("seeded"):
        notes.append(f"Red-Hat recall counts conflicts[] and redhat.findings[]; reports carrying a redhat block: {redhat_blocks} of "
                     f"{sum(1 for c in case_results if c.get('conflicts'))} pair cases")
    lat_sources = Counter(i.get("latency_source") for c in case_results for i in c.get("inputs") or [] if i.get("latency_source"))
    if lat_sources:
        stage_totals = [i["report_stage_timings_ms"].get("total") for c in case_results for i in c.get("inputs") or []
                        if isinstance(i.get("report_stage_timings_ms"), dict) and isinstance(i["report_stage_timings_ms"].get("total"), (int, float))]
        extra = f"; report.timings_ms.total (report build only, not gated) p95 {fmt_ms(percentile(stage_totals, 0.95))}" if stage_totals else ""
        agree = metrics["overall"].get("latency_class_agreement")
        extra += f"; report.latency_class agrees with the manifest on {fmt_pct(agree)} of inputs" if agree is not None else ""
        notes.append(f"gated latency = end-to-end ingest, source {dict(lat_sources)}{extra}")
    nx = [i["filename"] for c in case_results for i in c.get("inputs") or [] if i.get("not_extractable_by_design")]
    if nx:
        notes.append(f"no script font on this machine; handwritten inputs rendered in Helvetica and marked not_extractable_by_design: {nx}")
    if not args.llm:
        notes.append("grounded LLM pass off (PARSURE_LLM_EXTRACTION=0); label-anchored extraction only — pass --llm to include it")

    result = {
        "benchmark_version": manifest["version"],
        "manifest_frozen_at": manifest.get("frozen_at"),
        "run_at": run_at.isoformat(),
        "run_id": run_id,
        "git_sha": sha,
        "options": {"classes": args.classes, "cases": args.cases, "llm": args.llm, "replay": not args.no_replay},
        "env": env,
        "metrics": metrics,
        "gates": gates,
        "notes": notes,
        "cases": case_results,
    }
    if args.baseline:
        baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        result["baseline_diff"] = diff_against_baseline(result, baseline, args.baseline)

    if not args.no_write:
        results_dir = Path(args.results_dir)
        results_dir.mkdir(parents=True, exist_ok=True)
        out_path = results_dir / f"{run_id}-{sha}.json"
        out_path.write_text(json.dumps(result, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        result["results_path"] = str(out_path)
    print(render_markdown(result))
    if result.get("results_path"):
        print(f"\nresults: {result['results_path']}")
    if metrics["cases_run"] == 0:
        return 2
    return 1 if any(g["status"] == "FAIL" for g in gates) else 0


if __name__ == "__main__":
    sys.exit(main())
