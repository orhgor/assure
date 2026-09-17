"""SSE streaming endpoint for JDF document engineering inquire pipeline."""

from __future__ import annotations

import contextvars
import copy
import json
import os
import re
import threading
import time
import uuid
from typing import Any, Callable, Generator, Iterator

from flask import Response, request, stream_with_context
from pydantic import BaseModel, ConfigDict, Field

try:
    from ..compiler.aperture import build_aperture_context
    from ..cost_governance import (
        BudgetExhaustedError,
        CostGovernor,
        QuotaExceededError,
        TaskType,
        TokenLimitExceededError,
    )
    from ..ledger.truth_engine import TruthLedgerEngine
    from ..lib.logger import get_audit_logger
    from ..models.jdf import (
        JDFDocumentTree,
        document_to_dict,
        empty_annotations,
        get_node_by_id,
        new_node_id,
        node_text,
        parse_document,
        splice_node,
    )
except ImportError:
    from compiler.aperture import build_aperture_context
    from cost_governance import (
        BudgetExhaustedError,
        CostGovernor,
        QuotaExceededError,
        TaskType,
        TokenLimitExceededError,
    )
    from ledger.truth_engine import TruthLedgerEngine
    from lib.logger import get_audit_logger
    from models.jdf import (
        JDFDocumentTree,
        document_to_dict,
        empty_annotations,
        get_node_by_id,
        new_node_id,
        node_text,
        parse_document,
        splice_node,
    )

_METRIC_RE = re.compile(
    r"(?P<key>[a-zA-Z_][\w.-]*)\s*(?:=|:)\s*\$?\s*(?P<val>\d+(?:\.\d+)?)\s*(?P<suffix>[MBKmbk])?",
)


class InquiryPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    user_intent: str
    target_node_id: str | None = None
    run_redhat: bool = True
    document: dict[str, Any] | None = None
    incoming_metrics: list[list[Any]] = Field(default_factory=list)


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# The edge closes an idle streaming connection at ~100s; keep bytes flowing and end
# the stream ourselves before then. Comment frames are ignored by the SSE parsers.
_KEEPALIVE_INTERVAL_SECONDS = 12.0
_KEEPALIVE_FRAME = ": keepalive\n\n"
DEFAULT_STREAM_DEADLINE_SECONDS = 90.0


class StreamDeadlineExceeded(RuntimeError):
    """A model phase outlived the SSE stream deadline (Cloudflare 524 guard)."""


def _stream_deadline_seconds() -> float:
    raw = (os.environ.get("INQUIRE_STREAM_DEADLINE_SECONDS") or "").strip()
    try:
        return float(raw) if raw else DEFAULT_STREAM_DEADLINE_SECONDS
    except ValueError:
        return DEFAULT_STREAM_DEADLINE_SECONDS


def _blocking_with_keepalive(
    fn: Callable[[], Any],
    *,
    deadline_at: float,
    phase: str,
) -> Generator[str, None, Any]:
    """Run a blocking model call on a worker thread, yielding SSE keepalive comments.

    The worker inherits a copy of the caller's context variables, so Flask's request
    context (BYOK keys, locale, request id) stays visible to the governor. Raises
    StreamDeadlineExceeded once the overall stream deadline passes, so the client gets
    a `complete` frame instead of a silently dead connection.
    """
    box: dict[str, Any] = {}
    done = threading.Event()
    ctx = contextvars.copy_context()

    def _worker() -> None:
        try:
            box["result"] = ctx.run(fn)
        except BaseException as exc:  # surfaced on the request thread below
            box["error"] = exc
        finally:
            done.set()

    threading.Thread(target=_worker, name=f"inquire-{phase}", daemon=True).start()
    while not done.is_set():
        remaining = deadline_at - time.monotonic()
        if remaining <= 0:
            raise StreamDeadlineExceeded(
                f"{phase} phase exceeded the {_stream_deadline_seconds():g}s stream deadline"
            )
        if done.wait(min(_KEEPALIVE_INTERVAL_SECONDS, remaining)):
            break
        yield _KEEPALIVE_FRAME
    if "error" in box:
        raise box["error"]
    return box.get("result")


def _status_payload(stage: str, **extra: Any) -> dict[str, Any]:
    """Human-readable stream step for the command-deck progress pill."""
    steps: dict[str, tuple[int, str]] = {
        "preflight": (1, "Thinking…"),
        "model": (1, "Thinking…"),
        "verify": (2, "Verifying numbers…"),
        "redhat": (3, "Running Red-Hat audit…"),
        "ready": (4, "Ready to dock"),
    }
    step, message = steps.get(stage, (1, "Working…"))
    return {"stage": stage, "step": step, "message": message, **extra}


def _parse_metrics(text: str) -> list[tuple[str, float]]:
    found: list[tuple[str, float]] = []
    for match in _METRIC_RE.finditer(text or ""):
        key = match.group("key")
        val = float(match.group("val"))
        suffix = (match.group("suffix") or "").upper()
        if suffix == "M":
            val *= 1_000_000
        elif suffix == "K":
            val *= 1_000
        elif suffix == "B":
            val *= 1_000_000_000
        found.append((key, val))
    return found


def _default_document(project_id: str) -> JDFDocumentTree:
    return JDFDocumentTree(
        document_id=f"doc-{project_id}",
        meta={"project_id": project_id},
        truth_ledger={},
        body=[],
    )


def resolve_active_document(
    project_id: str,
    payload_doc: JDFDocumentTree | dict[str, Any] | None,
) -> JDFDocumentTree:
    """Prefer inline payload document; fall back to latest SQLite revision."""
    if payload_doc is not None:
        if isinstance(payload_doc, JDFDocumentTree):
            return payload_doc
        return parse_document(payload_doc)
    try:
        from ..db.jdf_repository import fetch_latest_jdf
    except ImportError:
        from db.jdf_repository import fetch_latest_jdf
    stored = fetch_latest_jdf(project_id)
    if stored:
        return parse_document(stored)
    return _default_document(project_id)


def _mutate_node_in_tree(
    tree: JDFDocumentTree | dict[str, Any], node_id: str, node: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    """Replace node_id in a copy of the tree (section child or top-level block)."""
    doc = document_to_dict(tree)
    mutated, found = splice_node(doc, node_id, node)
    if found:
        return mutated, True
    for index, block in enumerate(mutated.get("body") or []):
        if isinstance(block, dict) and block.get("id") == node_id:
            mutated["body"][index] = copy.deepcopy(node)
            return mutated, True
    return mutated, False


def persist_surgical_rewrite(
    project_id: str,
    tree: JDFDocumentTree | dict[str, Any],
    *,
    node_id: str,
    node: dict[str, Any],
    change_summary: str | None = None,
    expected_version: int | None = None,
) -> dict[str, Any]:
    """Snapshot a surgical rewrite into the tree the pipeline ran against.

    Uses ``save_jdf_revision`` (document revision + node snapshot), the same path the
    rest of the app takes. Never raises: a failed/conflicting write is reported back to
    the SSE stream so it can surface in the final frame instead of killing the stream.
    """
    try:
        from ..db.jdf_repository import RevisionConflict, save_jdf_revision
    except ImportError:
        from db.jdf_repository import RevisionConflict, save_jdf_revision

    mutated, found = _mutate_node_in_tree(tree, node_id, node)
    if not found:
        return {"persisted": False, "persist_error": f"target node not found: {node_id}"}

    try:
        result = save_jdf_revision(
            project_id,
            mutated,
            mutation_type="surgical_rewrite",
            target_node_id=node_id,
            change_summary=change_summary,
            expected_version=expected_version,
        )
    except RevisionConflict as exc:
        return {
            "persisted": False,
            "persist_conflict": True,
            "persist_error": str(exc),
            "latest_version": exc.latest_version,
        }
    except Exception as exc:  # a persistence failure must not abort the stream
        return {"persisted": False, "persist_error": str(exc)}
    return {
        "persisted": True,
        "version": result.get("version"),
        "revision_id": result.get("revision_id"),
    }


def _build_messages(user_intent: str, aperture: dict[str, Any] | None) -> list[dict[str, str]]:
    system = (
        "You are Assure document engineering. Return only the revised paragraph content "
        "for the target node. Preserve locked metrics from the truth ledger."
    )
    user_parts = [f"User intent:\n{user_intent}"]
    if aperture:
        user_parts.append(aperture.get("prompt_harness") or "")
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n\n".join(p for p in user_parts if p)},
    ]


def _ensure_node_annotations(node: dict[str, Any]) -> dict[str, Any]:
    node.setdefault("annotations", empty_annotations())
    ann = node["annotations"]
    ann.setdefault("redhat", [])
    ann.setdefault("z3", [])
    return node


def _paragraph_node(
    node_id: str, content: str, *, status: str = "ok", z3_error: str | None = None
) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    if z3_error:
        meta["z3_error"] = z3_error
    node = {
        "type": "paragraph",
        "id": node_id,
        "content": content,
        "entities_referenced": [],
        "provenance": [],
        "meta": meta,
        "annotations": empty_annotations(),
    }
    if status != "ok":
        node["status"] = status
    if z3_error:
        node = _ensure_node_annotations(node)
        node["annotations"]["z3"].append(
            {
                "id": new_node_id("z3"),
                "message": z3_error,
                "status": "violation",
                "canonical_key": "",
            }
        )
    return node


def _record_llm_usage(
    governor: CostGovernor,
    project_id: str,
    *,
    input_tokens: int,
    output_tokens: int,
    model_id: str,
    task_type: TaskType,
) -> dict[str, Any]:
    governor.record_usage(
        project_id=project_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        model_id=model_id,
        task_type=task_type,
    )
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "model_id": model_id,
        "task_type": task_type.value,
    }


def run_inquire_pipeline(
    project_id: str,
    *,
    user_intent: str,
    target_node_id: str | None = None,
    run_redhat: bool = True,
    document: JDFDocumentTree | dict[str, Any] | None = None,
    incoming_metrics: list[list[Any]] | None = None,
    governor: CostGovernor | None = None,
    request_id: str | None = None,
) -> Iterator[str]:
    """Yield SSE frames for the inquire pipeline. No blocking sleep."""
    rid = request_id or str(uuid.uuid4())
    start_time = time.perf_counter()
    audit = get_audit_logger()
    gov = governor or CostGovernor()

    def _duration_ms() -> int:
        return int((time.perf_counter() - start_time) * 1000)

    def _fail_complete(**payload: Any) -> Iterator[str]:
        audit.log_audit(
            rid,
            project_id,
            "INQUIRE_STREAM",
            target_node_id=target_node_id,
            success=False,
            duration_ms=_duration_ms(),
            error_message=str(payload.get("error") or "failed"),
            details={"http_status": payload.get("http_status")},
        )
        yield _sse("complete", {"ok": False, "request_id": rid, **payload})

    yield _sse("status", {"stage": "load_document", "project_id": project_id, "request_id": rid})

    tree = resolve_active_document(project_id, document)

    truth = TruthLedgerEngine()
    truth.load_from_document(tree)
    for pair in incoming_metrics or []:
        if len(pair) >= 2:
            try:
                truth.lock_metric(str(pair[0]), float(pair[1]), "==")
            except (TypeError, ValueError):
                continue

    original_content = ""
    is_mutation = bool(target_node_id)
    if target_node_id:
        hit = get_node_by_id(tree, target_node_id)
        if hit:
            original_content = node_text(hit)

    aperture: dict[str, Any] | None = None
    task_type = TaskType.DEEP_SYNTHESIS
    node_id = target_node_id or new_node_id("para")

    if target_node_id:
        yield _sse("status", {"stage": "aperture", "target_node_id": target_node_id})
        try:
            aperture = build_aperture_context(tree, target_node_id)
            task_type = TaskType.SURGICAL_EDIT
        except KeyError as exc:
            yield from _fail_complete(error=str(exc))
            return

    messages = _build_messages(user_intent, aperture)

    yield _sse("status", _status_payload("preflight", task_type=task_type.value))

    try:
        gov.preflight(project_id, task_type, messages)
    except (BudgetExhaustedError, QuotaExceededError) as exc:
        yield from _fail_complete(error=str(exc), http_status=429)
        return
    except TokenLimitExceededError as exc:
        yield from _fail_complete(error=str(exc), http_status=400)
        return

    def _validate(text: str) -> tuple[bool, str | None]:
        metrics = _parse_metrics(text)
        if not metrics:
            return True, None
        ok, violations = truth.validate_entities(metrics)
        if not ok:
            return False, violations[0] if violations else "Z3 Conflict"
        return True, None

    yield _sse("status", _status_payload("model", task_type=task_type.value))

    result = yield from _blocking_with_keepalive(
        lambda: gov.execute_with_retry_budget(
            project_id,
            task_type,
            messages,
            validate_fn=_validate,
            build_node_fn=lambda text: _paragraph_node(node_id, text),
            defer_budget_record=True,
        ),
        # Fresh budget per phase: model and Red-Hat each get the deadline, so a
        # legit 90-110s total (each call under its own cap, total under the
        # edge's ~100s idle timer because keepalives flow) no longer dies.
        deadline_at=time.monotonic() + _stream_deadline_seconds(),
        phase="model",
    )

    text = result.text or ""
    chunk_size = 48
    for i in range(0, max(len(text), 1), chunk_size):
        yield _sse("token", {"delta": text[i : i + chunk_size]})

    yield _sse("status", _status_payload("verify", task_type=task_type.value))

    if result.status == "VALIDATION_FAILED":
        violations = [result.error or "validation failed"]
        audit.log_audit(
            rid,
            project_id,
            "Z3_VIOLATION",
            target_node_id=target_node_id,
            success=False,
            duration_ms=_duration_ms(),
            details={"violations": violations},
        )
        yield _sse(
            "truth_check",
            {"status": "VIOLATION", "violations": violations, "detail": "Z3 Conflict"},
        )
    else:
        metrics = _parse_metrics(text)
        ok, viol = truth.validate_entities(metrics) if metrics else (True, [])
        if not ok:
            audit.log_audit(
                rid,
                project_id,
                "Z3_VIOLATION",
                target_node_id=target_node_id,
                success=False,
                duration_ms=_duration_ms(),
                details={"violations": viol},
            )
        yield _sse(
            "truth_check",
            {
                "status": "PASS" if ok else "VIOLATION",
                "violations": viol,
                "detail": "Z3 Conflict" if viol else "Z3 Verified",
            },
        )

    node = result.node or _paragraph_node(
        node_id,
        text,
        status=result.status,
        z3_error=result.error,
    )

    if run_redhat and result.ok:
        yield _sse("status", _status_payload("redhat"))
        red_messages = [
            {
                "role": "user",
                "content": f"Red-hat critique this node:\n\n{node.get('content', '')}\n\nIntent: {user_intent}",
            }
        ]
        red = yield from _blocking_with_keepalive(
            lambda: gov.execute_with_retry_budget(
                project_id,
                TaskType.REDHAT,
                red_messages,
                defer_budget_record=True,
            ),
            deadline_at=time.monotonic() + _stream_deadline_seconds(),
            phase="redhat",
        )
        critique_text = (red.text or "").strip()
        if critique_text and not critique_text.startswith("ERROR:"):
            node = _ensure_node_annotations(dict(node))
            annotation = {
                "id": new_node_id("crit"),
                "text": critique_text,
                "status": "open",
            }
            node["annotations"]["redhat"].append(annotation)
            yield _sse(
                "redhat_annotation",
                {"node_id": node_id, "annotation": annotation},
            )
        _record_llm_usage(
            gov,
            project_id,
            input_tokens=red.input_tokens,
            output_tokens=red.output_tokens,
            model_id=red.model_id,
            task_type=TaskType.REDHAT,
        )

    yield _sse("status", _status_payload("ready"))

    yield _sse(
        "jdf_node_ready",
        {
            "node": node,
            "status": result.status,
            "original_content": original_content,
            "new_content": text,
            "target_node_id": target_node_id,
            "is_mutation": is_mutation,
        },
    )

    persist_info: dict[str, Any] = {}
    if is_mutation and result.ok:
        persist_info = persist_surgical_rewrite(
            project_id,
            tree,
            node_id=node_id,
            node=node,
            change_summary=f"Surgical rewrite: {user_intent.strip()[:120]}",
        )

    usage_payload = _record_llm_usage(
        gov,
        project_id,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        model_id=result.model_id,
        task_type=task_type,
    )
    yield _sse("usage", usage_payload)

    audit.log_audit(
        rid,
        project_id,
        "INQUIRE_STREAM",
        target_node_id=target_node_id,
        success=result.ok,
        duration_ms=_duration_ms(),
        details={"model": result.model_id, "task_type": task_type.value},
    )
    yield _sse(
        "complete",
        {
            "ok": result.ok,
            "retries": result.retries,
            "model_id": result.model_id,
            "request_id": rid,
            **persist_info,
        },
    )


def register_inquire_routes(app) -> None:
    """Register POST /api/projects/<project_id>/inquire/stream on the Flask app."""
    try:
        from ..rate_limits import (
            DailyCompileLimitError,
            check_daily_compile_limit,
            increment_daily_compile_limit,
            limiter,
        )
    except ImportError:
        from rate_limits import (
            DailyCompileLimitError,
            check_daily_compile_limit,
            increment_daily_compile_limit,
            limiter,
        )

    try:
        from ..middleware import project_ownership_required
    except ImportError:
        from middleware import project_ownership_required

    @app.post("/api/projects/<project_id>/inquire/stream")
    @limiter.limit("30 per minute")
    @project_ownership_required
    def inquire_stream(project_id: str):
        data = request.get_json(silent=True) or {}
        try:
            payload = InquiryPayload.model_validate(
                {
                    "user_intent": data.get("user_intent") or "",
                    "target_node_id": data.get("target_node_id"),
                    "run_redhat": data.get("run_redhat", True),
                    "document": data.get("document"),
                    "incoming_metrics": data.get("incoming_metrics") or [],
                }
            )
        except Exception as exc:
            return {"error": str(exc)}, 400

        if not payload.user_intent.strip():
            return {"error": "user_intent required"}, 400

        try:
            check_daily_compile_limit(project_id)
        except DailyCompileLimitError as exc:
            return {"error": str(exc)}, 429
        increment_daily_compile_limit(project_id)

        def generate() -> Generator[str, None, None]:
            request_id = str(uuid.uuid4())
            start_time = time.perf_counter()
            audit = get_audit_logger()
            try:
                yield from run_inquire_pipeline(
                    project_id,
                    user_intent=payload.user_intent.strip(),
                    target_node_id=(payload.target_node_id or "").strip() or None,
                    run_redhat=payload.run_redhat,
                    document=payload.document,
                    incoming_metrics=payload.incoming_metrics,
                    request_id=request_id,
                )
            except (BudgetExhaustedError, QuotaExceededError) as exc:
                duration_ms = int((time.perf_counter() - start_time) * 1000)
                audit.log_exception(
                    request_id,
                    project_id,
                    "INQUIRE_STREAM",
                    exc,
                    target_node_id=(payload.target_node_id or "").strip() or None,
                    duration_ms=duration_ms,
                )
                yield _sse(
                    "complete",
                    {"ok": False, "error": str(exc), "http_status": 429, "request_id": request_id},
                )
            except TokenLimitExceededError as exc:
                duration_ms = int((time.perf_counter() - start_time) * 1000)
                audit.log_exception(
                    request_id,
                    project_id,
                    "INQUIRE_STREAM",
                    exc,
                    target_node_id=(payload.target_node_id or "").strip() or None,
                    duration_ms=duration_ms,
                )
                yield _sse(
                    "complete",
                    {"ok": False, "error": str(exc), "http_status": 400, "request_id": request_id},
                )
            except Exception as exc:
                duration_ms = int((time.perf_counter() - start_time) * 1000)
                audit.log_exception(
                    request_id,
                    project_id,
                    "INQUIRE_STREAM",
                    exc,
                    target_node_id=(payload.target_node_id or "").strip() or None,
                    duration_ms=duration_ms,
                )
                yield _sse("complete", {"ok": False, "error": str(exc), "request_id": request_id})

        headers = {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
        return Response(stream_with_context(generate()), headers=headers)
