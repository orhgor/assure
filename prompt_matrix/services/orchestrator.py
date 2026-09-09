"""Orchestrator — stream LLM tokens, parse [claim: N], verify via groundrails, SSE to UI."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Generator, Iterator
from typing import Any

try:
    from ..db.runs_repository import insert_run
    from ..models.jdf import build_document_from_draft, document_to_dict
    from ..services.compiler import PromptCompiler
    from ..services.lock_metadata import enrich_extracted_locks, lock_hash
    from ..services.parser import extract_claims_from_stream
    from ..services.verifier import ClaimVerifier
except ImportError:
    from db.runs_repository import insert_run
    from models.jdf import build_document_from_draft, document_to_dict
    from services.compiler import PromptCompiler
    from services.lock_metadata import enrich_extracted_locks, lock_hash
    from services.parser import extract_claims_from_stream
    from services.verifier import ClaimVerifier

StreamFn = Callable[[list[dict[str, str]], str], Iterator[str]]


def typed_sse(event_type: str, payload: dict[str, Any] | None = None) -> str:
    data: dict[str, Any] = {"type": event_type}
    if payload:
        data.update(payload)
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def done_sse() -> str:
    return "data: [DONE]\n\n"


def _litellm_model_name(model: str) -> str:
    mapping = {
        "gemini": "gemini/gemini-1.5-pro",
        "claude": "anthropic/claude-sonnet-4-5",
        "deepseek": "deepseek/deepseek-chat",
    }
    return mapping.get(model, model)


def default_token_stream(messages: list[dict[str, str]], model: str) -> Iterator[str]:
    import litellm

    try:
        from ..keys import litellm_kwargs_for
    except ImportError:
        from keys import litellm_kwargs_for

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
        if choice is None:
            continue
        content = getattr(getattr(choice, "delta", None), "content", None)
        if content:
            yield str(content)


def _locks_from_verdicts(
    extracted_claims: list[dict[str, str]],
    verdicts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    locks: list[dict[str, Any]] = []
    for i, verdict in enumerate(verdicts):
        claim_meta = (
            extracted_claims[i] if i < len(extracted_claims) else {"claim_id": f"claim_{i + 1}"}
        )
        locks.append(
            {
                "claim_id": claim_meta.get("claim_id") or f"claim_{i + 1}",
                "status": "grounded" if verdict.get("grounded") else "amber",
                "confidence_score": float(verdict.get("score") or 0.0),
                "engine": verdict.get("engine") or "unknown",
                "evidence": verdict.get("support") or {},
                "text": str(verdict.get("claim") or claim_meta.get("text") or ""),
            }
        )
    return locks


def _verification_complete_payload(run_id: str, locks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "event": "verification_complete",
        "data": {
            "run_id": run_id,
            "locks": locks,
        },
    }


def generate_run_stream(
    directive: str,
    sources: str,
    *,
    model: str = "gemini",
    run_id: str | None = None,
    stream_fn: StreamFn | None = None,
) -> Generator[str, None, None]:
    """
    Stream tokens to the client, then parse [claim: N] tags and verify after the
    text stream completes (verification never blocks token delivery).
    """
    rid = run_id or f"run_{uuid.uuid4().hex[:12]}"
    compiler = PromptCompiler()
    messages = compiler.compile(directive, sources)
    stream = stream_fn or default_token_stream

    full_output_text = ""
    for chunk in stream(messages, model):
        full_output_text += chunk
        yield f"data: {json.dumps({'token': chunk}, ensure_ascii=False)}\n\n"
        yield typed_sse("token", {"delta": chunk, "token": chunk})

    extracted_claims = extract_claims_from_stream(full_output_text)
    claim_texts = [
        str(item.get("text") or "").strip() for item in extracted_claims if item.get("text")
    ]

    verifier = ClaimVerifier()
    verdicts = verifier.verify_claims(claim_texts, sources) if claim_texts else []
    locks = _locks_from_verdicts(extracted_claims, verdicts)

    final_payload = _verification_complete_payload(rid, locks)
    yield f"data: {json.dumps(final_payload, ensure_ascii=False)}\n\n"
    yield typed_sse("verification_complete", final_payload["data"])


def orchestrate_sourced_run(
    directive: str,
    *,
    sources: list[dict[str, Any]],
    sources_block: str,
    workspace_id: str,
    model: str = "gemini",
    intent_type: str = "draft",
    router_ms: int = 0,
    request_id: str | None = None,
    stream_fn: StreamFn | None = None,
) -> Generator[str, None, None]:
    """Full sourced run: stream → verify → persist → complete."""
    rid = request_id or str(uuid.uuid4())
    run_id = f"run_{uuid.uuid4().hex[:12]}"

    full_output_text = ""
    verification_locks: list[dict[str, Any]] = []

    for frame in generate_run_stream(
        directive,
        sources_block,
        model=model,
        run_id=run_id,
        stream_fn=stream_fn,
    ):
        if frame.startswith("event: token"):
            for line in frame.splitlines():
                if line.startswith("data: "):
                    payload = json.loads(line[6:])
                    full_output_text += str(payload.get("delta") or payload.get("token") or "")
        elif '"verification_complete"' in frame or frame.startswith("event: verification_complete"):
            for line in frame.splitlines():
                if line.startswith("data: "):
                    payload = json.loads(line[6:])
                    inner = (
                        payload.get("data") if isinstance(payload.get("data"), dict) else payload
                    )
                    verification_locks = list(inner.get("locks") or [])
        yield frame

    sources_used = [
        {
            "id": str(s.get("id") or ""),
            "name": str(s.get("name") or s.get("id") or ""),
            "web": bool(s.get("web")),
        }
        for s in sources
    ]
    default_source_id = str((sources[0] if sources else {}).get("id") or "")

    extracted_locks: list[dict[str, Any]] = []
    for i, lock in enumerate(verification_locks):
        if lock.get("status") != "grounded":
            continue
        text = str(lock.get("text") or lock.get("claim_id") or "")
        enriched = {
            "canonical_key": text[:64],
            "metric": text[:64],
            "value": 1,
            "confidence": float(lock.get("confidence_score") or 0.0),
            "source_id": default_source_id,
            "claim_id": lock.get("claim_id"),
            "verification_engine": lock.get("engine") or "groundrails",
            "page_coordinates": {"page": 1, "x": 0, "y": 0, "width": 100, "height": 24},
        }
        evidence = lock.get("evidence") or {}
        if isinstance(evidence, dict):
            enriched["quoted"] = str(evidence.get("passage") or "")
        enriched["lock_index"] = i + 1
        enriched["lock_hash"] = lock_hash(enriched)
        extracted_locks.append(enriched)

    extracted_locks = enrich_extracted_locks(extracted_locks, sources_used)

    for lock in extracted_locks:
        yield typed_sse(
            "lock",
            {
                "claim_id": lock.get("claim_id"),
                "lock_hash": lock.get("lock_hash"),
                "source_id": lock.get("source_id"),
                "page_coordinates": lock.get("page_coordinates"),
                "metric": lock.get("metric"),
                "lock_index": lock.get("lock_index"),
            },
        )

    truth_ledger: dict[str, float] = {}
    for lock in extracted_locks:
        key = str(lock.get("canonical_key") or "").strip()
        if key:
            truth_ledger[key] = float(lock.get("value") or 1)

    doc = build_document_from_draft(
        workspace_id, full_output_text or directive, truth_ledger=truth_ledger
    )
    content = document_to_dict(doc)
    status = "stamped" if extracted_locks else "draft"

    run = insert_run(
        directive=directive,
        content=content,
        model=model,
        sources_used=sources_used,
        extracted_locks=extracted_locks,
        status=status,
        workspace_id=workspace_id,
    )
    run["lock_count"] = len(extracted_locks)
    run["title"] = directive[:72] + ("…" if len(directive) > 72 else "")
    run["intent_type"] = intent_type

    yield typed_sse(
        "complete",
        {
            "ok": True,
            "run": run,
            "intent_type": intent_type,
            "router_ms": router_ms,
            "draft_text": full_output_text,
            "request_id": rid,
        },
    )
    yield done_sse()
