"""SSE draft generation: progressive Claude → compile → Z3 → Red-Hat pipeline."""

from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from typing import Any, Callable, Generator, Iterator, Literal

from flask import Response, request, stream_with_context
from pydantic import AliasChoices, BaseModel, Field

_log = logging.getLogger(__name__)

try:
    from ..cost_governance import (
        BudgetExhaustedError,
        CostGovernor,
        QuotaExceededError,
        TaskType,
        TokenLimitExceededError,
    )
    from ..db.substrate_repository import fetch_substrate_entries_by_ids
    from ..ledger.truth_engine import TruthLedgerEngine
    from ..lib.logger import get_audit_logger
    from ..models.jdf import (
        JDFDocumentTree,
        apply_redhat_critiques_to_tree,
        apply_z3_violations_to_tree,
        attach_substrate_provenance_to_tree,
        build_document_from_draft,
        document_to_dict,
        parse_document,
    )
    from ..routers.inquire_stream import _parse_metrics
    from ..services.audit_summary import build_audit_summary
    from ..services.lock_inference import infer_lock_candidates
except ImportError:
    from cost_governance import (
        BudgetExhaustedError,
        CostGovernor,
        QuotaExceededError,
        TaskType,
        TokenLimitExceededError,
    )
    from db.substrate_repository import fetch_substrate_entries_by_ids
    from ledger.truth_engine import TruthLedgerEngine
    from lib.logger import get_audit_logger
    from models.jdf import (
        JDFDocumentTree,
        apply_redhat_critiques_to_tree,
        apply_z3_violations_to_tree,
        attach_substrate_provenance_to_tree,
        build_document_from_draft,
        document_to_dict,
        parse_document,
    )
    from routers.inquire_stream import _parse_metrics
    from services.audit_summary import build_audit_summary
    from services.lock_inference import infer_lock_candidates

DRAFT_MODEL = "anthropic/claude-sonnet-4-5"
LOCK_MODEL = "deepseek/deepseek-chat"

_DRAFT_SYSTEM = (
    "You are Assure document engineering. Draft clear, structured prose for a business document. "
    "Use markdown headings (## Section) for major sections. Include specific numbers where appropriate."
)

CancelCheck = Callable[[], bool]


class DraftCancelledError(Exception):
    """Raised when the client disconnects or aborts the stream."""


SUBSTRATE_CONTEXT_CHARS_PER_FILE = 4000
SUBSTRATE_CONTEXT_CHARS_TOTAL = 16000


class DraftPayload(BaseModel):
    intent: str = ""
    context: str | None = None
    substrate_file_ids: list[str] = Field(default_factory=list)
    compile_type: Literal["full", "selection"] = Field(
        default="full",
        validation_alias=AliasChoices("compileType", "compile_type"),
    )
    content: str | None = None


def _typed_sse(event_type: str, payload: dict[str, Any] | None = None) -> str:
    data: dict[str, Any] = {"type": event_type}
    if payload:
        data.update(payload)
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _done_sse() -> str:
    return "data: [DONE]\n\n"


def _check_cancel(cancel_check: CancelCheck | None) -> None:
    if cancel_check and cancel_check():
        raise DraftCancelledError("client disconnected")


def _draft_messages(intent: str, context: str | None) -> list[dict[str, str]]:
    parts = [f"User intent:\n{intent.strip()}"]
    if context and context.strip():
        parts.append(f"Additional context:\n{context.strip()}")
    return [
        {"role": "system", "content": _DRAFT_SYSTEM},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def _build_substrate_context(substrate_rows: list[dict[str, Any]]) -> str:
    """Concatenate selected Substrate Vault files (bounded) so the draft is
    actually grounded in them, not just told they exist."""
    if not substrate_rows:
        return ""
    blocks: list[str] = []
    total = 0
    for row in substrate_rows:
        text = str(row.get("extracted_text") or "").strip()
        if not text:
            continue
        excerpt = text[:SUBSTRATE_CONTEXT_CHARS_PER_FILE]
        block = f"### Source file: {row.get('filename') or 'substrate'}\n{excerpt}"
        if total + len(block) > SUBSTRATE_CONTEXT_CHARS_TOTAL:
            break
        blocks.append(block)
        total += len(block)
    return "\n\n".join(blocks)


def run_lock_inference(text: str) -> tuple[list[dict[str, Any]], str]:
    """DeepSeek-V3 lock extraction (Stage 2)."""
    result = infer_lock_candidates(text)
    return result.candidates, result.model


def verify_locks(
    locks: list[dict[str, Any]],
    draft_text: str,
) -> dict[str, Any]:
    """Z3 verification of inferred locks against draft metrics (Stage 4)."""
    truth = TruthLedgerEngine()
    lock_results: list[dict[str, Any]] = []

    for lock in locks:
        key = str(lock.get("canonical_key") or lock.get("metric") or "").strip()
        if not key:
            continue
        try:
            val = float(lock.get("value"))
            truth.lock_metric(key, val, "==")
            lock_results.append({"key": key, "ok": True, "value": val})
        except (TypeError, ValueError):
            lock_results.append({"key": key, "ok": False, "error": "invalid value"})

    metrics = _parse_metrics(draft_text)
    ok, violations = truth.validate_entities(metrics) if metrics else (True, [])

    return {
        "status": "PASS" if ok else "VIOLATION",
        "violations": violations,
        "lock_results": lock_results,
        "locks_verified": len(lock_results),
        "metrics_checked": len(metrics),
    }


def run_redhat_audit(
    project_id: str,
    draft_text: str,
    *,
    gov: CostGovernor,
    cancel_check: CancelCheck | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """DeepSeek-R1 adversarial critique (Stage 4 — heavy)."""
    _check_cancel(cancel_check)
    content = (draft_text or "").strip()[:8000]
    if not content:
        return [], {"input_tokens": 0, "output_tokens": 0, "model_id": ""}

    red_messages = [
        {
            "role": "user",
            "content": (
                "Red-hat adversarial review of this draft document. "
                "List concrete risks, unsupported claims, and missing citations.\n\n"
                f"{content}"
            ),
        }
    ]

    _check_cancel(cancel_check)
    red = gov.execute_with_retry_budget(
        project_id,
        TaskType.REDHAT,
        red_messages,
        defer_budget_record=True,
    )
    _check_cancel(cancel_check)

    critiques: list[dict[str, Any]] = []
    text = (red.text or "").strip()
    if text and not text.startswith("ERROR:"):
        critiques.append(
            {
                "title": "Red-hat review",
                "content": text,
                "model": red.model_id,
            }
        )

    usage = {
        "input_tokens": red.input_tokens,
        "output_tokens": red.output_tokens,
        "model_id": red.model_id,
        "task_type": TaskType.REDHAT.value,
    }
    return critiques, usage


def _stream_claude(
    gov: CostGovernor,
    messages: list[dict[str, str]],
    *,
    cancel_check: CancelCheck | None = None,
) -> Iterator[str | tuple[str, int, int, str]]:
    """Yield typed token SSE frames, then (full_text, in_tok, out_tok, model_id)."""
    policy = gov.policy_for(TaskType.DEEP_SYNTHESIS)
    model = policy.litellm_model or DRAFT_MODEL
    max_out = policy.max_output_tokens

    try:
        import litellm

        try:
            from ..services.language_guard import guard_messages, resolve_request_locale
        except ImportError:
            from services.language_guard import guard_messages, resolve_request_locale

        try:
            from ..keys import litellm_kwargs_for
        except ImportError:
            from keys import litellm_kwargs_for

        guarded = guard_messages(messages, locale=resolve_request_locale())
        _api_kwargs: dict = {}
        try:
            _api_kwargs = litellm_kwargs_for("claude")
        except Exception:
            pass
        stream = litellm.completion(
            model=model,
            messages=guarded,
            max_tokens=max_out,
            temperature=0.4,
            stream=True,
            **_api_kwargs,
        )
        full = ""
        for chunk in stream:
            _check_cancel(cancel_check)
            choice = chunk.choices[0] if chunk.choices else None
            delta = ""
            if choice is not None:
                content = getattr(getattr(choice, "delta", None), "content", None)
                if content:
                    delta = str(content)
            if delta:
                full += delta
                yield _typed_sse("token", {"delta": delta})
        in_tok = gov.accountant.count_messages(guarded)
        out_tok = gov.accountant.count(full)
        yield (full, in_tok, out_tok, model)
    except DraftCancelledError:
        raise
    except Exception as exc:
        yield _typed_sse("error", {"ok": False, "error": str(exc)})
        yield ("", 0, 0, model)


def run_draft_pipeline(
    project_id: str,
    *,
    intent: str,
    context: str | None = None,
    substrate_file_ids: list[str] | None = None,
    governor: CostGovernor | None = None,
    request_id: str | None = None,
    cancel_check: CancelCheck | None = None,
) -> Iterator[str]:
    rid = request_id or str(uuid.uuid4())
    start = time.perf_counter()
    audit = get_audit_logger()
    gov = governor or CostGovernor()

    substrate_rows = (
        fetch_substrate_entries_by_ids(project_id, substrate_file_ids) if substrate_file_ids else []
    )
    substrate_context = _build_substrate_context(substrate_rows)
    combined_context = "\n\n".join(p for p in [context, substrate_context] if p and p.strip())
    messages = _draft_messages(intent, combined_context)

    yield _typed_sse(
        "status", {"stage": "preflight", "message": "Checking budget…", "request_id": rid}
    )

    try:
        gov.preflight(project_id, TaskType.DEEP_SYNTHESIS, messages)
    except (BudgetExhaustedError, QuotaExceededError) as exc:
        yield _typed_sse(
            "error", {"ok": False, "error": str(exc), "http_status": 429, "request_id": rid}
        )
        yield _done_sse()
        return
    except TokenLimitExceededError as exc:
        yield _typed_sse(
            "error", {"ok": False, "error": str(exc), "http_status": 400, "request_id": rid}
        )
        yield _done_sse()
        return

    yield _typed_sse(
        "status", {"stage": "model", "message": "Drafting with Claude…", "model": DRAFT_MODEL}
    )

    full_text = ""
    in_tok = 0
    out_tok = 0
    model_id = DRAFT_MODEL

    try:
        for item in _stream_claude(gov, messages, cancel_check=cancel_check):
            if isinstance(item, str):
                if '"type": "error"' in item:
                    yield item
                    yield _done_sse()
                    return
                yield item
            else:
                full_text, in_tok, out_tok, model_id = item
    except DraftCancelledError:
        audit.log_audit(rid, project_id, "DRAFT_STREAM", success=False, error_message="cancelled")
        return

    if not full_text.strip():
        yield _typed_sse(
            "error", {"ok": False, "error": "Empty draft from model.", "request_id": rid}
        )
        yield _done_sse()
        return

    gov.record_usage(
        project_id,
        input_tokens=in_tok,
        output_tokens=out_tok,
        model_id=model_id,
        task_type=TaskType.DEEP_SYNTHESIS,
        meta={"pipeline": "draft_stream"},
    )
    yield _typed_sse(
        "usage",
        {
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "model_id": model_id,
            "task_type": TaskType.DEEP_SYNTHESIS.value,
        },
    )

    _check_cancel(cancel_check)

    yield _typed_sse("status", {"stage": "locks", "message": "Inferring locks…"})

    # Stage 2: compile full JDFDocumentTree + lock inference (emit immediately)
    ledger: dict[str, float] = {}
    locks: list[dict[str, Any]] = []
    lock_model = LOCK_MODEL

    try:
        locks, lock_model = run_lock_inference(full_text)
        for lock in locks:
            key = str(lock.get("canonical_key") or lock.get("metric") or "").strip()
            if key:
                try:
                    ledger[key] = float(lock.get("value"))
                except (TypeError, ValueError):
                    pass
        lock_in = gov.accountant.count(full_text[:12000])
        lock_out = gov.accountant.count(json.dumps({"candidates": locks}))
        gov.record_usage(
            project_id,
            input_tokens=lock_in,
            output_tokens=lock_out,
            model_id=lock_model,
            task_type=TaskType.SUMMARIZE_NODE,
            meta={"pipeline": "draft_lock_inference"},
        )
        yield _typed_sse(
            "usage",
            {
                "input_tokens": lock_in,
                "output_tokens": lock_out,
                "model_id": lock_model,
                "task_type": TaskType.SUMMARIZE_NODE.value,
            },
        )
    except RuntimeError as exc:
        yield _typed_sse("status", {"stage": "locks_skipped", "message": str(exc)})

    document = build_document_from_draft(project_id, full_text, truth_ledger=ledger)
    doc_dict = document_to_dict(document)
    if substrate_rows and locks:
        doc_dict = attach_substrate_provenance_to_tree(doc_dict, locks, substrate_rows)

    yield _typed_sse(
        "compiled",
        {
            "document": doc_dict,
            "nodes": doc_dict.get("body") or [],
            "locks": locks,
            "node_count": len(doc_dict.get("body") or []),
            "lock_count": len(locks),
            "draft_text": full_text,
        },
    )

    # Stage 3: Math Check (Z3) — fast, local, no external call. This is the
    # hybrid compile gate: docking unblocks here instead of waiting on the
    # much slower Stress Test below.
    yield _typed_sse("status", {"message": "Running Math Check…"})

    z3_results: dict[str, Any] = {"status": "SKIPPED", "violations": [], "lock_results": []}
    try:
        _check_cancel(cancel_check)
        z3_results = verify_locks(locks, full_text)
    except DraftCancelledError:
        audit.log_audit(
            rid,
            project_id,
            "DRAFT_STREAM",
            success=False,
            duration_ms=int((time.perf_counter() - start) * 1000),
            error_message="cancelled during z3 verify",
        )
        return

    verified_doc = doc_dict
    if z3_results.get("violations"):
        verified_doc = apply_z3_violations_to_tree(verified_doc, z3_results["violations"])
    parse_document(verified_doc)

    verified_payload = build_audit_summary(
        z3_results=z3_results,
        redhat_critiques=[],
        document=verified_doc,
    )
    yield _typed_sse("verified", verified_payload)

    # Hybrid compile gate: the pipeline ends here. Docking is unblocked now
    # that Math Check has passed. The Stress Test (Red-Hat, DeepSeek-Reasoner)
    # is opt-in and slow — it only runs if the user explicitly asks for it,
    # via a separate call to run_redhat_pipeline / /draft/redhat/stream.
    duration_ms = int((time.perf_counter() - start) * 1000)
    audit.log_audit(
        rid,
        project_id,
        "DRAFT_STREAM",
        success=True,
        duration_ms=duration_ms,
        details={
            "node_count": len(doc_dict.get("body") or []),
            "lock_count": len(locks),
            "model": model_id,
            "z3_status": z3_results.get("status"),
        },
    )
    yield _typed_sse(
        "complete",
        {
            "ok": True,
            "request_id": rid,
            "node_count": len(doc_dict.get("body") or []),
            "lock_count": len(locks),
        },
    )
    yield _done_sse()


class RedhatPayload(BaseModel):
    draft_text: str = Field(min_length=1)
    document: dict[str, Any]
    z3_results: dict[str, Any] | None = None
    target_node_id: str | None = None


def run_redhat_pipeline(
    project_id: str,
    *,
    draft_text: str,
    document: dict[str, Any],
    z3_results: dict[str, Any] | None = None,
    target_node_id: str | None = None,
    governor: CostGovernor | None = None,
    request_id: str | None = None,
    cancel_check: CancelCheck | None = None,
) -> Iterator[str]:
    """Opt-in Stage 4: DeepSeek-Reasoner adversarial critique.

    Two callers share this pipeline:
    - the hybrid compile gate (whole freshly-drafted document, no
      ``target_node_id``) — see the Generate view's "Run Stress Test" prompt.
    - the surgical canvas, on demand, scoped to either the full docked
      document or a single node via ``target_node_id`` — see the node
      context menu / "Run Red-Hat on Full Document" button.
    """
    rid = request_id or str(uuid.uuid4())
    start = time.perf_counter()
    audit = get_audit_logger()
    gov = governor or CostGovernor()
    z3_results = z3_results or {"status": "SKIPPED", "violations": [], "lock_results": []}

    try:
        parse_document(document)
    except Exception as exc:
        yield _typed_sse(
            "error", {"ok": False, "error": f"Invalid document: {exc}", "request_id": rid}
        )
        yield _done_sse()
        return

    yield _typed_sse("status", {"message": "Running Stress Test…"})

    redhat_critiques: list[dict[str, Any]] = []
    try:
        _check_cancel(cancel_check)
        redhat_critiques, red_usage = run_redhat_audit(
            project_id,
            draft_text,
            gov=gov,
            cancel_check=cancel_check,
        )
        if red_usage.get("model_id"):
            gov.record_usage(
                project_id,
                input_tokens=int(red_usage.get("input_tokens") or 0),
                output_tokens=int(red_usage.get("output_tokens") or 0),
                model_id=str(red_usage["model_id"]),
                task_type=TaskType.REDHAT,
                meta={"pipeline": "draft_redhat_audit"},
            )
            yield _typed_sse("usage", red_usage)
    except DraftCancelledError:
        audit.log_audit(
            rid,
            project_id,
            "DRAFT_STREAM_REDHAT",
            success=False,
            duration_ms=int((time.perf_counter() - start) * 1000),
            error_message="cancelled during redhat audit",
        )
        return
    except Exception as exc:
        _log.exception("Red-Hat audit failed for project %s", project_id)
        print(f"REDHAT_AUDIT_ERROR project={project_id} error={exc}", file=sys.stderr)
        redhat_critiques = [
            {
                "title": "Red-hat review",
                "content": f"Audit failed: {exc}. Check API keys and try again.",
                "model": "",
                "status": "error",
            }
        ]

    annotated = document
    if redhat_critiques:
        annotated = apply_redhat_critiques_to_tree(
            annotated, redhat_critiques, target_node_id=target_node_id
        )
    parse_document(annotated)

    audit_payload = build_audit_summary(
        z3_results=z3_results,
        redhat_critiques=redhat_critiques,
        document=annotated,
    )
    yield _typed_sse("audit_complete", audit_payload)

    duration_ms = int((time.perf_counter() - start) * 1000)
    audit.log_audit(
        rid,
        project_id,
        "DRAFT_STREAM_REDHAT",
        success=True,
        duration_ms=duration_ms,
        details={"redhat_count": len(redhat_critiques)},
    )
    yield _typed_sse(
        "complete",
        {"ok": True, "request_id": rid, "redhat_count": len(redhat_critiques)},
    )
    yield _done_sse()


def _make_cancel_check() -> CancelCheck:
    disconnected = {"flag": False}

    def cancel_check() -> bool:
        if disconnected["flag"]:
            return True
        try:
            if hasattr(request, "is_disconnected") and request.is_disconnected():
                disconnected["flag"] = True
                return True
        except Exception:
            pass
        return False

    return cancel_check


def register_draft_routes(app) -> None:
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

    @app.post("/api/projects/<project_id>/draft/stream")
    @limiter.limit("30 per minute")
    def draft_stream(project_id: str):
        data = request.get_json(silent=True) or {}
        try:
            payload = DraftPayload.model_validate(
                {
                    "intent": data.get("intent") or data.get("user_intent") or "",
                    "context": data.get("context"),
                    "substrate_file_ids": data.get("substrate_file_ids") or [],
                    "compileType": data.get("compileType") or data.get("compile_type") or "full",
                    "content": data.get("content"),
                }
            )
        except Exception as exc:
            return {"error": str(exc)}, 400

        intent = (payload.intent or "").strip()
        if payload.compile_type == "selection":
            excerpt = (payload.content or intent).strip()
            if not excerpt:
                return {"error": "content required for selection compile"}, 400
            intent = (
                "Compile this selected excerpt into a structured document. "
                "Preserve facts and numbers. Do not invent context that is not in the excerpt.\n\n"
                + excerpt
            )
        elif not intent:
            return {"error": "intent required"}, 400

        try:
            check_daily_compile_limit(project_id)
        except DailyCompileLimitError as exc:
            return {"error": str(exc)}, 429
        increment_daily_compile_limit(project_id)

        @stream_with_context
        def generate() -> Generator[str, None, None]:
            request_id = str(uuid.uuid4())
            cancel_check = _make_cancel_check()
            try:
                yield from run_draft_pipeline(
                    project_id,
                    intent=intent,
                    context=payload.context,
                    substrate_file_ids=payload.substrate_file_ids,
                    request_id=request_id,
                    cancel_check=cancel_check,
                )
            except GeneratorExit:
                return
            except DraftCancelledError:
                return
            except Exception as exc:
                yield _typed_sse(
                    "error", {"ok": False, "error": str(exc), "request_id": request_id}
                )
                yield _done_sse()

        headers = {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
        return Response(generate(), headers=headers)

    @app.post("/api/projects/<project_id>/draft/redhat/stream")
    @limiter.limit("30 per minute")
    def draft_redhat_stream(project_id: str):
        """Opt-in Stress Test — continuing an already-verified compile, not
        a new one, so this does not consume the daily compile limit."""
        data = request.get_json(silent=True) or {}
        try:
            payload = RedhatPayload.model_validate(
                {
                    "draft_text": data.get("draft_text") or "",
                    "document": data.get("document") or {},
                    "z3_results": data.get("z3_results"),
                    "target_node_id": data.get("target_node_id"),
                }
            )
        except Exception as exc:
            return {"error": str(exc)}, 400

        @stream_with_context
        def generate() -> Generator[str, None, None]:
            request_id = str(uuid.uuid4())
            cancel_check = _make_cancel_check()
            try:
                yield from run_redhat_pipeline(
                    project_id,
                    draft_text=payload.draft_text.strip(),
                    document=payload.document,
                    z3_results=payload.z3_results,
                    target_node_id=payload.target_node_id,
                    request_id=request_id,
                    cancel_check=cancel_check,
                )
            except GeneratorExit:
                return
            except DraftCancelledError:
                return
            except Exception as exc:
                yield _typed_sse(
                    "error", {"ok": False, "error": str(exc), "request_id": request_id}
                )
                yield _done_sse()

        headers = {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
        return Response(generate(), headers=headers)
