"""SSE streaming endpoint for JDF document engineering inquire pipeline."""

from __future__ import annotations

import contextvars
import copy
import json
import os
import re
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
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
        answer_refusal_reason,
    )
    from ..ledger.truth_engine import Z3Timeout as _Z3Timeout
    from ..ledger.truth_engine import TruthLedgerEngine
    from ..lib.http_errors import clean_error_message
    from ..lib.logger import get_audit_logger
    from ..models.jdf import (
        JDFDocumentTree,
        attach_substrate_provenance_to_tree,
        document_to_dict,
        empty_annotations,
        get_node_by_id,
        new_node_id,
        node_text,
        parse_document,
        splice_node,
    )
    from ..services.entailment import attach_entailment_to_tree
    from ..services.provenance_meta import build_node_provenance_meta
    from ..services.source_jdf import SelectionAnchor, selection_anchor_meta
    from ..services.redhat_verbatim import (
        anchored_quotes,
        anchored_source_texts,
        format_source_block,
        verbatim_gate,
    )
except ImportError:
    from compiler.aperture import build_aperture_context
    from cost_governance import (
        BudgetExhaustedError,
        CostGovernor,
        QuotaExceededError,
        TaskType,
        TokenLimitExceededError,
        answer_refusal_reason,
    )
    from ledger.truth_engine import Z3Timeout as _Z3Timeout
    from ledger.truth_engine import TruthLedgerEngine
    from lib.http_errors import clean_error_message
    from lib.logger import get_audit_logger
    from models.jdf import (
        JDFDocumentTree,
        attach_substrate_provenance_to_tree,
        document_to_dict,
        empty_annotations,
        get_node_by_id,
        new_node_id,
        node_text,
        parse_document,
        splice_node,
    )
    from services.entailment import attach_entailment_to_tree
    from services.provenance_meta import build_node_provenance_meta
    from services.source_jdf import SelectionAnchor, selection_anchor_meta
    from services.redhat_verbatim import (
        anchored_quotes,
        anchored_source_texts,
        format_source_block,
        verbatim_gate,
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
    #: A text selection made on the rendered source JDF (services/source_jdf,
    #: 2026-09-28), recorded on the rewritten node as ``meta.selection_anchor``.
    selection: SelectionAnchor | None = None


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

    The findings that were open on the paragraph this rewrite replaces are carried
    onto the node it writes, marked ``resolved`` and naming the revision (and its
    mutation type) that closed them — see ``resolve_findings_on_rewrite``. The
    revision's id and version are read before the write and the id is passed to
    ``save_jdf_revision``, because the node naming the revision is the node that
    same write serializes; ``expected_version`` is pinned to the version just read
    so the recorded version cannot be a guess, and a write that lost a race is
    retried once against the version that actually won rather than recording a
    revision number that is not the one that acted.

    Returns the node as written under ``"node"``, so the caller can show what the
    document now holds: a failed write returns no ``"node"`` and the caller keeps
    the one it passed (findings still open).
    """
    try:
        from ..db.jdf_repository import (
            RevisionConflict,
            close_findings_for_revision,
            save_jdf_revision,
        )
    except ImportError:
        from db.jdf_repository import (
            RevisionConflict,
            close_findings_for_revision,
            save_jdf_revision,
        )

    resolved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _write_once() -> dict[str, Any]:
        """Read the next version, stamp the findings against it, write the revision."""
        closed = close_findings_for_revision(
            project_id,
            tree,
            node_id,
            node,
            mutation_type="surgical_rewrite",
            resolved_at=resolved_at,
        )
        written = closed["node"]
        mutated, found = _mutate_node_in_tree(tree, node_id, written)
        if not found:
            return {"persisted": False, "persist_error": f"target node not found: {node_id}"}
        result = save_jdf_revision(
            project_id,
            mutated,
            mutation_type="surgical_rewrite",
            target_node_id=node_id,
            change_summary=change_summary,
            expected_version=(
                closed["version"] - 1 if expected_version is None else expected_version
            ),
            revision_id=closed["revision_id"],
        )
        return {
            "persisted": True,
            "version": result.get("version"),
            "revision_id": result.get("revision_id"),
            "node": written,
        }

    try:
        return _write_once()
    except RevisionConflict as exc:
        # Someone wrote between the read and the write: the revision the node
        # names would not be the one that lands. Re-read and re-stamp once — the
        # stamp is the record, and a wrong revision number in it is worse than a
        # failed write the stream reports.
        try:
            return _write_once()
        except RevisionConflict as second:
            return {
                "persisted": False,
                "persist_conflict": True,
                "persist_error": str(second),
                "latest_version": second.latest_version,
            }
        except Exception as retry_exc:  # a persistence failure must not abort the stream
            return {"persisted": False, "persist_error": str(retry_exc)}
    except Exception as exc:  # a persistence failure must not abort the stream
        return {"persisted": False, "persist_error": str(exc)}


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


def _substrate_rows(project_id: str) -> list[dict[str, Any]]:
    """The project's Sources rows with text, for anchoring a rewrite. Never
    raises: a vault that cannot be read leaves the node unanchored, which the
    node then says (``provenance: []``)."""
    try:
        from ..db.substrate_repository import list_substrate_for_project
    except ImportError:
        from db.substrate_repository import list_substrate_for_project
    try:
        rows = list_substrate_for_project(project_id, with_text=True)
    except Exception:  # noqa: BLE001
        return []
    return [r for r in rows if isinstance(r, dict) and str(r.get("extracted_text") or "").strip()]


def verify_rewritten_node(
    project_id: str,
    node: dict[str, Any],
    *,
    truth_ledger: dict[str, Any] | None = None,
    substrate_rows: list[dict[str, Any]] | None = None,
    entailment_checker: Callable[..., dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Anchor and entail a surgically rewritten paragraph before it is shown.

    Until 2026-09-27 a rewrite persisted with ``provenance: []`` and no
    entailment verdict: the compile's anchoring ran on the original text only,
    so the replacement — the text the reader actually keeps — was the one
    paragraph in the document nothing had checked. This runs the same lexical
    anchoring the compile runs (``models/jdf.attach_substrate_provenance_to_tree``
    against the project's Sources rows) and the same entailment pass
    (``services/entailment.attach_entailment_to_tree``) on the rewritten node
    alone, then writes ``meta.provenance`` the way the compile does. When
    ``services/claim_policy.derive_claim`` exists it is called for the ``claim``
    block; its absence is not an error.

    Returns ``(node, info)``; ``info`` reports what happened (``anchored``,
    ``entailment``, ``sources``) and never claims a check that did not run.
    """
    info: dict[str, Any] = {"anchored": False, "citations": 0, "entailment": None, "sources": 0}
    if str(node.get("type") or "") != "paragraph" or not str(node.get("content") or "").strip():
        info["reason"] = "not a paragraph"
        return node, info
    rows = substrate_rows if substrate_rows is not None else _substrate_rows(project_id)
    info["sources"] = len(rows)
    node = dict(node)
    node["provenance"] = []
    if not rows:
        info["reason"] = "no source text in the project's Sources"
        return node, info
    mini = {
        "document_id": f"rewrite-{node.get('id') or 'node'}",
        "meta": {},
        "truth_ledger": dict(truth_ledger or {}),
        "body": [{"type": "section", "id": "rewrite-scope", "title": "", "children": [node]}],
    }
    try:
        mini = attach_substrate_provenance_to_tree(mini, [], rows)
    except Exception as exc:  # noqa: BLE001
        info["reason"] = f"anchoring failed: {type(exc).__name__}"
        return node, info
    anchored = (mini.get("body") or [{}])[0].get("children") or [node]
    node = anchored[0] if isinstance(anchored[0], dict) else node
    rows_with_quote = [
        r for r in node.get("provenance") or []
        if isinstance(r, dict) and str(r.get("extracted_quote") or "").strip()
    ]
    info["citations"] = len(rows_with_quote)
    info["anchored"] = bool(rows_with_quote)
    if rows_with_quote:
        try:
            attach_entailment_to_tree(mini, project_id=project_id, checker=entailment_checker)
        except Exception as exc:  # noqa: BLE001
            info["entailment_error"] = f"{type(exc).__name__}: {exc}"[:200]
        verdict = ((node.get("meta") or {}).get("provenance") or {}).get("entailment")
        if isinstance(verdict, dict):
            info["entailment"] = verdict.get("verdict")
    summary = build_node_provenance_meta(node, ledger=truth_ledger or {})
    meta = dict(node.get("meta") or {})
    if summary:
        meta["provenance"] = summary
    else:
        meta.pop("provenance", None)
    node["meta"] = meta
    # The claim policy (services/claim_policy.derive_claim) writes the ``claim``
    # block at meta.provenance.claim from the node's evidence and the rows the
    # rewrite was anchored against; it runs after meta.provenance is rebuilt so
    # the block is not replaced. Imported defensively: its absence is not an
    # error, and nothing here asserts a claim verdict of its own.
    try:
        try:
            from ..services.claim_policy import derive_claim  # type: ignore
        except ImportError:
            from services.claim_policy import derive_claim  # type: ignore
    except ImportError:
        derive_claim = None  # type: ignore
    if derive_claim is not None and rows_with_quote:
        try:
            claim = derive_claim(node, sources=rows)
            if isinstance(claim, dict):
                info["claim"] = claim.get("verdict")
        except Exception as exc:  # noqa: BLE001
            info["claim_error"] = f"{type(exc).__name__}: {exc}"[:200]
    return node, info


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


# Request-scoped Z3 ledgers, held for as long as the stream that owns them lives.
_LIVE_LEDGERS: set[TruthLedgerEngine] = set()


@contextmanager
def _open_ledger() -> Iterator[TruthLedgerEngine]:
    """Request-scoped Z3 ledger, released by the index instead of by the collector.

    `ledger.truth_engine` pools its solvers: close() resets the solver and appends it to a
    module-level pool for the next request. A pooled solver is only sound while its wrapper
    is still reachable, and CPython runs the finalizers of a whole cyclic-garbage set: an
    engine closed from __del__ can hand its solver to the pool, and that solver's own
    __del__ still runs in the same pass, dec-refing the native solver the pool now points
    at. The next borrow then resets freed memory (SIGSEGV). A streaming response is the
    worst case, because an aborted stream finalizes its generator through the cycle
    collector. Keeping the ledger reachable here means close() always runs from a plain
    finally: on stream end, on error, and on client disconnect alike.
    """
    ledger = TruthLedgerEngine()
    _LIVE_LEDGERS.add(ledger)
    try:
        yield ledger
    finally:
        try:
            ledger.close()
        finally:
            _LIVE_LEDGERS.discard(ledger)


def run_inquire_pipeline(
    project_id: str,
    *,
    user_intent: str,
    target_node_id: str | None = None,
    run_redhat: bool = True,
    document: JDFDocumentTree | dict[str, Any] | None = None,
    incoming_metrics: list[list[Any]] | None = None,
    governor: CostGovernor | None = None,
    ledger: TruthLedgerEngine | None = None,
    request_id: str | None = None,
    entailment_checker: Callable[..., dict[str, Any]] | None = None,
    selection: dict[str, Any] | None = None,
) -> Iterator[str]:
    """Yield SSE frames for the inquire pipeline. No blocking sleep.

    ``entailment_checker`` replaces ``services.entailment.check_entailment`` for
    the rewritten node's verification (tests inject one; production uses the
    SEMANTIC_VALIDATION policy model).

    `ledger` is owned by the caller (`_open_ledger`): it has to stay reachable for the
    whole stream and be closed from a plain finally, never from a GC finalizer.
    """
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

    truth = ledger or TruthLedgerEngine()
    truth.load_from_document(tree)
    for pair in incoming_metrics or []:
        if len(pair) >= 2:
            try:
                truth.lock_metric(str(pair[0]), float(pair[1]), "==")
            except (TypeError, ValueError):
                continue

    original_content = ""
    is_mutation = bool(target_node_id)
    original_node: dict[str, Any] | None = None
    if target_node_id:
        hit = get_node_by_id(tree, target_node_id)
        if hit:
            original_node = hit
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

    def _rewritten_node(text: str) -> dict[str, Any]:
        """The node a surgical rewrite yields: new text, same findings.

        The paragraph's own grounding does not survive — its citations belong to
        the text that was replaced — but a Red-Hat finding is evidence about the
        paragraph rather than about its wording, and the node it hangs on is the
        only place the document keeps it. Rebuilding the node from
        ``_paragraph_node`` dropped it, which is how applying a finding destroyed
        the finding. Here it comes across untouched, still ``open``:
        ``persist_surgical_rewrite`` closes it, naming the revision, only when the
        write actually lands.
        """
        built = _paragraph_node(node_id, text)
        if original_node:
            built["annotations"]["redhat"] = copy.deepcopy(
                (original_node.get("annotations") or {}).get("redhat") or []
            )
        return built

    yield _sse("status", _status_payload("model", task_type=task_type.value))

    result = yield from _blocking_with_keepalive(
        lambda: gov.execute_with_retry_budget(
            project_id,
            task_type,
            messages,
            validate_fn=_validate,
            build_node_fn=_rewritten_node,
            defer_budget_record=True,
        ),
        # Fresh budget per phase: model and Red-Hat each get the deadline, so a
        # legit 90-110s total (each call under its own cap, total under the
        # edge's ~100s idle timer because keepalives flow) no longer dies.
        deadline_at=time.monotonic() + _stream_deadline_seconds(),
        phase="model",
    )

    text = result.text or ""

    # The governor's executor swallows provider exceptions and returns the
    # message as the text (`cost_governance._default_executor`: ``"ERROR: {exc}"``
    # with ``ok=True``). Treated as prose, that string was Z3-verified (no
    # metrics, so PASS), persisted as a surgical rewrite and shown as the node
    # — a paragraph reading "ERROR: AuthenticationError ..." with a green check.
    # Same guard the Red-Hat critique below and `draft.py` / `refine_node.py`
    # apply to their own model text: nothing downstream sees an error string.
    if text.strip().startswith("ERROR:"):
        clean = clean_error_message(text.strip()[len("ERROR:"):].strip()) or "model call failed"
        audit.log_audit(
            rid,
            project_id,
            "INQUIRE_STREAM",
            target_node_id=target_node_id,
            success=False,
            duration_ms=_duration_ms(),
            error_message=clean,
            details={"model": result.model_id, "task_type": task_type.value},
        )
        _record_llm_usage(
            gov,
            project_id,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            model_id=result.model_id,
            task_type=task_type,
        )
        yield _sse("error", {"ok": False, "error": clean, "request_id": rid})
        yield _sse(
            "complete",
            {
                "ok": False,
                "persisted": False,
                "error": clean,
                "model_id": result.model_id,
                "request_id": rid,
            },
        )
        return

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
        if not metrics:
            # No ``key: value`` figure in the text means Z3 had nothing to check.
            # Until 2026-09-27 this case was reported as PASS / "Z3 Verified" — a
            # verdict for a check that never ran. SKIPPED says what happened.
            yield _sse(
                "truth_check",
                {"status": "SKIPPED", "violations": [], "detail": "no figures to check"},
            )
        else:
            z3_timed_out = False
            try:
                ok, viol = truth.validate_entities(metrics)
            except _Z3Timeout as exc:
                # `unknown` from the solver is not a verdict; it was reported as
                # PASS before 2026-09-23.
                ok, viol, z3_timed_out = False, [str(exc)], True
            if not ok and not z3_timed_out:
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
                    "status": "TIMEOUT" if z3_timed_out else ("PASS" if ok else "VIOLATION"),
                    "violations": [] if z3_timed_out else viol,
                    "detail": (
                        "Z3 timed out — not verified"
                        if z3_timed_out
                        else ("Z3 Conflict" if viol else f"Z3: {len(metrics)} figure(s) consistent with the ledger")
                    ),
                    "checked": len(metrics),
                },
            )

    node = result.node or _paragraph_node(
        node_id,
        text,
        status=result.status,
        z3_error=result.error,
    )

    # The rewritten text is verified against the project's sources the way the
    # compile verifies every paragraph: lexical anchoring, then entailment on the
    # anchored sentences. Runs before Red-Hat so the critique sees the same
    # quotes, and before the persist so what is written is what was checked.
    anchor_info: dict[str, Any] = {}
    if result.ok:
        yield _sse("status", _status_payload("anchor"))
        node, anchor_info = verify_rewritten_node(
            project_id,
            node,
            truth_ledger=tree.truth_ledger if hasattr(tree, "truth_ledger") else {},
            entailment_checker=entailment_checker,
        )
        yield _sse("anchoring", {"node_id": node_id, **anchor_info})

    # The selection this rewrite was asked from, as the browser sent it, plus
    # ``verbatim`` — re-found in the project's Sources or not. Set before the
    # persist so the node written is the node shown; the verdicts above and
    # the critique below do not read it.
    if selection:
        node = dict(node)
        node["meta"] = {
            **(node.get("meta") or {}),
            "selection_anchor": selection_anchor_meta(
                selection, [str(r.get("extracted_text") or "") for r in _substrate_rows(project_id)]
            ),
        }

    if run_redhat and result.ok:
        yield _sse("status", _status_payload("redhat"))
        quotes = anchored_quotes(node)
        source_texts = anchored_source_texts(quotes)
        if quotes:
            source_part = (
                f"Source sentences anchored to this node ({len(quotes)}) — the only source "
                f"available:\n{format_source_block(quotes)}\n\n"
                "When you quote the source, copy the sentence exactly as listed, inside “ ” "
                "quotes; a quote that is not verbatim will be discarded."
            )
        else:
            source_part = (
                "No source sentence is anchored to this node; do not report \"no source\" as "
                "a finding, and quote nothing as the source."
            )
        red_messages = [
            {
                "role": "user",
                "content": (
                    f"Red-hat critique this node:\n\n{node.get('content', '')}\n\n"
                    f"{source_part}\n\nIntent: {user_intent}"
                ),
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
        # A response cut off at the output ceiling is a partial review, and a
        # partial review stored on the paragraph reaches a client as if it were
        # a finding. Refused here; the node keeps no annotation for it.
        refusal = answer_refusal_reason(red, TaskType.REDHAT)
        if refusal:
            print(
                f"REDHAT_AUDIT_REFUSED project={project_id} node={node_id} "
                f"detail={refusal[:240]}",
                file=sys.stderr,
            )
        elif critique_text and not critique_text.startswith("ERROR:"):
            # A critique that quotes the source is kept only when the quote is
            # in the anchored sentences verbatim; otherwise it is dropped and
            # the drop is reported, not the critique.
            kept, notes = verbatim_gate(
                [{"title": "Red-hat critique", "content": critique_text}], source_texts
            )
            if not kept:
                yield _sse(
                    "redhat_dropped",
                    {"node_id": node_id, "reason": "quote not verbatim in the source", "notes": notes},
                )
            else:
                node = _ensure_node_annotations(dict(node))
                annotation = {
                    "id": new_node_id("crit"),
                    "node_id": node_id,
                    "text": critique_text,
                    "status": "open",
                    "quote": kept[0].get("quote"),
                    "quote_verbatim": bool(kept[0].get("quote_verbatim")),
                    "evidence_kind": kept[0].get("evidence_kind") or "observation",
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

    # The write lands before the frame that shows it, so the node on screen is the
    # node the document holds — the finding it closed, and the revision that
    # closed it, arrive with the new text rather than a reload later. Ordering is
    # all this changes: `persist_surgical_rewrite` never raises, and a write that
    # fails returns the node it was given, its findings still open, which is what
    # the document still says.
    persist_info: dict[str, Any] = {}
    if is_mutation and result.ok:
        persist_info = persist_surgical_rewrite(
            project_id,
            tree,
            node_id=node_id,
            node=node,
            change_summary=f"Surgical rewrite: {user_intent.strip()[:120]}",
        )
        node = persist_info.pop("node", None) or node

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
        from ..rbac import requires
    except ImportError:
        from middleware import project_ownership_required
        from rbac import requires

    @app.post("/api/projects/<project_id>/inquire/stream")
    @limiter.limit("30 per minute")
    @requires("compile.run")
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
                    "selection": data.get("selection"),
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
                with _open_ledger() as ledger:
                    yield from run_inquire_pipeline(
                        project_id,
                        user_intent=payload.user_intent.strip(),
                        target_node_id=(payload.target_node_id or "").strip() or None,
                        run_redhat=payload.run_redhat,
                        document=payload.document,
                        incoming_metrics=payload.incoming_metrics,
                        ledger=ledger,
                        request_id=request_id,
                        selection=payload.selection.model_dump() if payload.selection else None,
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
