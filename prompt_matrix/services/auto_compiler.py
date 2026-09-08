"""Auto-Compiler orchestration — Fast Router → Prompt Compiler → LLM + groundrails SSE."""

from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from typing import Any, Callable, Generator, Iterator

try:
    from ..db.runs_repository import insert_run
    from ..models.jdf import build_document_from_draft, document_to_dict
    from ..services.compiler import PromptCompiler
    from ..services.fast_router import classify_intent, detect_sources
    from ..services.lock_inference import infer_lock_candidates
    from ..services.lock_metadata import enrich_extracted_locks
    from ..services.orchestrator import done_sse, orchestrate_sourced_run, typed_sse
    from ..services.parser import extract_claims_from_stream
    from ..services.perplexity_agent import (
        stream_perplexity_agent,
        web_sources_from_text,
    )
    from ..services.prompt_compiler import compile_prompt
    from ..services.router import route_intent
    from ..services.verifier import claims_from_text, verify_claims
except ImportError:
    from db.runs_repository import insert_run
    from models.jdf import build_document_from_draft, document_to_dict
    from services.compiler import PromptCompiler
    from services.fast_router import classify_intent, detect_sources
    from services.lock_inference import infer_lock_candidates
    from services.lock_metadata import enrich_extracted_locks
    from services.orchestrator import done_sse, orchestrate_sourced_run, typed_sse
    from services.parser import extract_claims_from_stream
    from services.perplexity_agent import stream_perplexity_agent, web_sources_from_text
    from services.prompt_compiler import compile_prompt
    from services.router import route_intent
    from services.verifier import claims_from_text, verify_claims

CancelCheck = Callable[[], bool]

# Re-export for routes that import from auto_compiler
__all__ = ["done_sse", "run_auto_compiler_pipeline", "create_run_from_directive", "typed_sse"]


def _run_lock_inference(text: str) -> tuple[list[dict[str, Any]], str]:
    result = infer_lock_candidates(text)
    return result.candidates, result.model


def _verify_locks(locks: list[dict[str, Any]], draft_text: str) -> dict[str, Any]:
    try:
        from ..ledger.truth_engine import TruthLedgerEngine
        from ..routers.inquire_stream import _parse_metrics
    except ImportError:
        from ledger.truth_engine import TruthLedgerEngine
        from routers.inquire_stream import _parse_metrics

    truth = TruthLedgerEngine()
    for lock in locks:
        key = str(lock.get("canonical_key") or lock.get("metric") or "").strip()
        if not key:
            continue
        try:
            truth.lock_metric(key, float(lock.get("value")), "==")
        except (TypeError, ValueError):
            pass
    metrics = _parse_metrics(draft_text)
    ok, violations = truth.validate_entities(metrics) if metrics else (True, [])
    return {"status": "PASS" if ok else "VIOLATION", "violations": violations}


def _litellm_model_name(model: str) -> str:
    mapping = {
        "gemini": "gemini/gemini-1.5-pro",
        "claude": "anthropic/claude-sonnet-4-5",
        "deepseek": "deepseek/deepseek-chat",
    }
    return mapping.get(model, model)


def _claims_for_verification(text: str) -> list[str]:
    tagged = extract_claims_from_stream(text)
    if tagged:
        return [str(item.get("text") or "").strip() for item in tagged if item.get("text")]
    return claims_from_text(text)


def _stream_litellm(prompt: str, *, model: str) -> Iterator[str]:
    import litellm

    try:
        from ..keys import litellm_kwargs_for
    except ImportError:
        from keys import litellm_kwargs_for

    messages = [
        {"role": "system", "content": "Follow the compiled prompt exactly."},
        {"role": "user", "content": prompt},
    ]
    kwargs: dict[str, Any] = {}
    try:
        kwargs = litellm_kwargs_for(model.split("/")[-1])
    except Exception:
        pass
    stream = litellm.completion(
        model=_litellm_model_name(model),
        messages=messages,
        max_tokens=2048,
        temperature=0.3,
        stream=True,
        **kwargs,
    )
    for chunk in stream:
        choice = chunk.choices[0] if chunk.choices else None
        delta = ""
        if choice is not None:
            content = getattr(getattr(choice, "delta", None), "content", None)
            if content:
                delta = str(content)
        if delta:
            yield delta


def _verification_worker(
    *,
    text_holder: list[str],
    sources: list[dict[str, Any]],
    seen_claims: set[str],
    lock_queue: queue.Queue[dict[str, Any]],
    stop_event: threading.Event,
) -> None:
    last_len = 0
    while not stop_event.is_set():
        text = text_holder[0]
        if len(text) > last_len:
            for claim in _claims_for_verification(text):
                key = claim.lower()
                if key in seen_claims:
                    continue
                seen_claims.add(key)
                for lock in verify_claims([claim], sources):
                    lock_queue.put(lock)
            last_len = len(text)
        time.sleep(0.05)


def run_auto_compiler_pipeline(
    directive: str,
    *,
    workspace_id: str | None = None,
    source_ids: list[str] | None = None,
    model: str = "gemini",
    cancel_check: CancelCheck | None = None,
    request_id: str | None = None,
) -> Generator[str, None, None]:
    """SSE multiplex: status → token → lock → complete."""
    rid = request_id or str(uuid.uuid4())
    ws = workspace_id or "default"
    text = (directive or "").strip()
    if not text:
        yield typed_sse("error", {"ok": False, "error": "directive is required", "request_id": rid})
        yield done_sse()
        return

    t0 = time.perf_counter()
    intent_type = classify_intent(text)
    sources = detect_sources(text, ws, explicit_source_ids=source_ids or None)
    router_ms = int((time.perf_counter() - t0) * 1000)
    routing = route_intent(text, has_sources=bool(sources))

    yield typed_sse(
        "status",
        {
            "stage": "router",
            "intent_type": intent_type,
            "task": routing.get("task"),
            "has_sources": routing.get("has_sources"),
            "source_count": len(sources),
            "router_ms": router_ms,
            "request_id": rid,
        },
    )

    compiled_prompt = compile_prompt(text, intent_type, sources)
    sources_block = PromptCompiler.format_sources_from_rows(sources)
    use_web = not sources and not (source_ids or [])
    model_used = "perplexity-agent" if use_web else model

    yield typed_sse(
        "status",
        {
            "stage": "compile_prompt",
            "intent_type": intent_type,
            "model": model_used,
            "web_fallback": use_web,
        },
    )

    if sources and not use_web:
        yield from orchestrate_sourced_run(
            text,
            sources=sources,
            sources_block=sources_block,
            workspace_id=ws,
            model=model,
            intent_type=intent_type,
            router_ms=router_ms,
            request_id=rid,
        )
        return

    full_text = ""
    text_holder = [""]
    lock_queue: queue.Queue[dict[str, Any]] = queue.Queue()
    seen_claims: set[str] = set()
    stop_event = threading.Event()
    verify_sources = list(sources)

    worker = threading.Thread(
        target=_verification_worker,
        kwargs={
            "text_holder": text_holder,
            "sources": verify_sources,
            "seen_claims": seen_claims,
            "lock_queue": lock_queue,
            "stop_event": stop_event,
        },
        daemon=True,
    )
    worker.start()

    try:
        stream: Iterator[str]
        if use_web:
            stream = stream_perplexity_agent(compiled_prompt)
        else:
            stream = _stream_litellm(compiled_prompt, model=model)

        for delta in stream:
            if cancel_check and cancel_check():
                yield typed_sse("error", {"ok": False, "error": "cancelled", "request_id": rid})
                yield done_sse()
                return
            full_text += delta
            text_holder[0] = full_text
            yield typed_sse("token", {"delta": delta})
            while True:
                try:
                    lock = lock_queue.get_nowait()
                    yield typed_sse(
                        "lock",
                        {
                            "lock_hash": lock.get("lock_hash"),
                            "source_id": lock.get("source_id"),
                            "page_coordinates": lock.get("page_coordinates"),
                            "web": bool(lock.get("web")),
                            "pill": lock.get("pill") or ("🌐" if lock.get("web") else None),
                            "metric": lock.get("metric"),
                        },
                    )
                except queue.Empty:
                    break
    except Exception as exc:
        yield typed_sse("error", {"ok": False, "error": str(exc), "request_id": rid})
        yield done_sse()
        return
    finally:
        stop_event.set()
        worker.join(timeout=2.0)

    if use_web and full_text.strip():
        verify_sources = web_sources_from_text(full_text)
        for claim in claims_from_text(full_text):
            for lock in verify_claims([claim], verify_sources):
                yield typed_sse(
                    "lock",
                    {
                        "lock_hash": lock.get("lock_hash"),
                        "source_id": lock.get("source_id"),
                        "page_coordinates": lock.get("page_coordinates"),
                        "web": True,
                        "pill": "🌐",
                        "metric": lock.get("metric"),
                    },
                )

    # Drain remaining queued locks
    while not lock_queue.empty():
        lock = lock_queue.get_nowait()
        yield typed_sse(
            "lock",
            {
                "lock_hash": lock.get("lock_hash"),
                "source_id": lock.get("source_id"),
                "page_coordinates": lock.get("page_coordinates"),
                "web": bool(lock.get("web")),
                "pill": lock.get("pill"),
                "metric": lock.get("metric"),
            },
        )

    sources_used = [
        {
            "id": str(s.get("id") or ""),
            "name": str(s.get("name") or s.get("id") or ""),
            "excerpt_chars": len(str(s.get("excerpt") or "")),
            "web": bool(s.get("web")),
        }
        for s in (verify_sources or sources)
    ]

    locks: list[dict[str, Any]] = []
    if full_text.strip():
        locks, _ = _run_lock_inference(full_text)
        locks = enrich_extracted_locks(locks, sources_used)
        for lock in locks:
            if any(s.get("web") for s in sources_used):
                lock.setdefault("web", True)
                lock.setdefault("pill", "🌐")

    truth_ledger: dict[str, float] = {}
    for lock in locks:
        key = str(lock.get("canonical_key") or lock.get("metric") or "").strip()
        if not key:
            continue
        try:
            truth_ledger[key] = float(lock.get("value"))
        except (TypeError, ValueError):
            continue

    status = "draft"
    if sources_used:
        z3 = _verify_locks(locks, full_text)
        status = "contradiction" if z3.get("status") == "VIOLATION" else "stamped"
    elif use_web:
        status = "stamped" if locks else "draft"

    doc = build_document_from_draft(ws, full_text or text, truth_ledger=truth_ledger)
    content = document_to_dict(doc)

    run = insert_run(
        directive=text,
        content=content,
        model=model_used,
        sources_used=sources_used,
        extracted_locks=locks,
        status=status,
        workspace_id=ws,
    )
    run["lock_count"] = len(locks)
    run["title"] = text[:72] + ("…" if len(text) > 72 else "")
    run["intent_type"] = intent_type

    yield typed_sse(
        "complete",
        {
            "ok": True,
            "run": run,
            "intent_type": intent_type,
            "router_ms": router_ms,
            "draft_text": full_text,
        },
    )
    yield done_sse()


def create_run_from_directive(
    directive: str,
    *,
    workspace_id: str | None = None,
    source_ids: list[str] | None = None,
    model: str = "gemini",
) -> dict[str, Any]:
    """Sync wrapper — drains SSE pipeline and returns persisted run."""
    run: dict[str, Any] | None = None
    for frame in run_auto_compiler_pipeline(
        directive,
        workspace_id=workspace_id,
        source_ids=source_ids,
        model=model,
    ):
        if frame.startswith("event: complete"):
            for line in frame.splitlines():
                if line.startswith("data: "):
                    payload = json.loads(line[6:])
                    run = payload.get("run")
                    break
        if frame.startswith("event: error"):
            for line in frame.splitlines():
                if line.startswith("data: "):
                    payload = json.loads(line[6:])
                    raise RuntimeError(payload.get("error") or "Auto-Compiler failed")
    if not run:
        raise RuntimeError("Auto-Compiler did not return a run")
    return run
