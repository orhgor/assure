"""Batch evaluation over a local JSON dataset. Uses existing quality scores."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    from .agents.redteam import scan_prompt_and_reply
    from .engine import MatrixError, render_prompt_detailed
    from .quality import score_run
except ImportError:
    from agents.redteam import scan_prompt_and_reply
    from engine import MatrixError, render_prompt_detailed
    from quality import score_run


def load_dataset(path: str | Path) -> dict[str, Any]:
    raw = Path(path).read_text(encoding="utf-8")
    data = json.loads(raw)
    if isinstance(data, list):
        return {"cases": data}
    if not isinstance(data, dict):
        raise MatrixError("Dataset must be a JSON object or list.")
    cases = data.get("cases") or data.get("items") or data.get("tests")
    if not isinstance(cases, list):
        raise MatrixError("Dataset needs a 'cases' array.")
    data = dict(data)
    data["cases"] = cases
    return data


def _contains_score(hay: str, needles: list[str]) -> float:
    if not needles:
        return 1.0
    blob = (hay or "").casefold()
    hits = sum(1 for item in needles if item and item.casefold() in blob)
    return round(hits / len(needles), 4)


def _needles(case: dict) -> list[str]:
    extra = case.get("expect_contains") or case.get("contains") or []
    if isinstance(extra, str):
        extra = [extra]
    expect = case.get("expect") or case.get("expected") or ""
    needles = [str(item) for item in extra if str(item).strip()]
    if expect and str(expect).strip():
        needles.append(str(expect).strip())
    return needles


def eval_case(
    case: dict,
    *,
    target: str,
    intent: str,
    prompt_prefix: str = "",
    direct: bool = False,
    class_id: str | None = None,
) -> dict[str, Any]:
    case_id = str(case.get("id") or case.get("name") or "")
    task = str(case.get("task") or case.get("prompt") or case.get("question") or "").strip()
    context = str(case.get("context") or "")
    if prompt_prefix:
        task = f"{prompt_prefix.strip()}\n\n{task}".strip()
    if not task:
        return {"id": case_id, "ok": False, "error": "Case has no task."}
    use_intent = str(case.get("intent") or intent).strip() or intent
    use_target = str(case.get("target") or case.get("target_ai") or target).strip() or target
    reply = None
    note = None
    quality = None
    tokens = {}
    if direct:
        try:
            from .pipelines import run_workflow
        except ImportError:
            from pipelines import run_workflow
        pipe = run_workflow(
            use_target,
            use_intent,
            task,
            context,
            workflow=str(case.get("workflow") or "single"),
            direct=True,
            copy=False,
            class_id=class_id or case.get("class_id"),
            ground=bool(case.get("ground")),
        )
        compiled = pipe.prompt
        reply = pipe.reply
        note = pipe.note
        quality = pipe.quality
        tokens = pipe.tokens or {
            "input": pipe.input_tokens,
            "output": pipe.output_tokens,
            "total": pipe.total_tokens,
        }
    else:
        rendered = render_prompt_detailed(
            use_target, use_intent, task, context, class_id=class_id or case.get("class_id")
        )
        compiled = rendered.prompt
        golden = str(case.get("reply") or case.get("gold") or "")
        if golden:
            reply = golden
            quality = score_run(reply=golden, context=context, drafts=[], total_tokens=0).as_dict()
    needles = _needles(case)
    hay = reply if reply else compiled
    accuracy = _contains_score(hay, needles) if needles else None
    safety = scan_prompt_and_reply(compiled, reply, context=context)
    return {
        "id": case_id or (task[:40] + ("…" if len(task) > 40 else "")),
        "ok": safety["ok"] and (accuracy is None or accuracy >= 1.0),
        "target": use_target,
        "intent": use_intent,
        "task": task,
        "prompt": compiled,
        "reply": reply,
        "note": note,
        "accuracy": accuracy,
        "safety": safety,
        "quality": quality,
        "tokens": tokens,
    }


def run_eval(
    dataset: dict[str, Any],
    *,
    target: str = "gemini",
    intent: str = "analysis",
    prompt_prefix: str = "",
    direct: bool = False,
    class_id: str | None = None,
) -> dict[str, Any]:
    cases = dataset.get("cases") or []
    target = str(dataset.get("target") or target)
    intent = str(dataset.get("intent") or intent)
    rows = [
        eval_case(
            case if isinstance(case, dict) else {"task": str(case)},
            target=target,
            intent=intent,
            prompt_prefix=prompt_prefix,
            direct=direct,
            class_id=class_id,
        )
        for case in cases
    ]
    n = len(rows)
    accuracies = [row["accuracy"] for row in rows if row.get("accuracy") is not None]
    safe_n = sum(1 for row in rows if (row.get("safety") or {}).get("ok"))
    passed = sum(1 for row in rows if row.get("ok"))
    return {
        "n": n,
        "passed": passed,
        "accuracy": round(sum(accuracies) / len(accuracies), 4) if accuracies else None,
        "safety_rate": round(safe_n / n, 4) if n else None,
        "direct": direct,
        "cases": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="assure eval", description="Run a local JSON eval set.")
    parser.add_argument("--dataset", required=True, help="JSON file with a cases array")
    parser.add_argument("--prompt", default="", help="Prefix prepended to each case task")
    parser.add_argument("--output", help="Write the JSON report here (also printed)")
    parser.add_argument("--target", default="gemini")
    parser.add_argument("--intent", default="analysis")
    parser.add_argument("--class-id", dest="class_id")
    parser.add_argument("--direct", action="store_true", help="Send each case (counts as a Send)")
    parser.add_argument(
        "--ci", action="store_true", help="Print JSON only; exit 1 if any case fails"
    )
    args = parser.parse_args(argv)
    try:
        dataset = load_dataset(args.dataset)
        report = run_eval(
            dataset,
            target=args.target,
            intent=args.intent,
            prompt_prefix=args.prompt,
            direct=bool(args.direct),
            class_id=args.class_id,
        )
    except (OSError, json.JSONDecodeError, MatrixError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    sys.stdout.write(text + "\n")
    if args.ci and report["passed"] < report["n"]:
        return 1
    return 0
