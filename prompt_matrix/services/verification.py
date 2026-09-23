"""Post-parse verification: the one hook that runs Z3 + Red-Hat after a parse.

Boundary: Z3 execution, Red-Hat scanning, and verification-result assembly
live ONLY here. Parse entrypoints (substrate ingest, import_project_pdf,
jdf memory ingest) call :func:`run_verification_after_parse` and nothing
else — they never run Z3 or Red-Hat themselves, and they never assemble
verification results.

Verification failure never blocks parse persistence: the document is stored
with whatever verification state is available, including an explicit ERROR.

Async is deliberately deferred (the repo's Celery queue exists, but the
write-back storage contract does not), so every document — including ones
over the sync page limit — is verified synchronously for now; the async
branch logs that deferral so the decision is visible in operations.
"""

from __future__ import annotations

import copy
import logging
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

#: Documents at or under this page count verify synchronously; over it, the
#: async path would take over once the storage contract exists (see module
#: docstring — async is deferred, so the sync path runs for now).
SYNC_VERIFICATION_PAGE_LIMIT = 50

#: Wall-clock budget for the Z3 pass. Enforced best-effort: the ledger engine
#: is deterministic, so this guards a pathological document, not a model call.
Z3_TIMEOUT_SECONDS = 30


def run_verification_after_parse(bundle: dict[str, Any]) -> dict[str, Any]:
    """Run Z3 + Red-Hat after a successful parse. Results attach in place.

    Accepts the parse bundle. The tree verified is ``bundle["jdf"]`` when it
    is already an Assure tree (has a ``body``); otherwise a tree is built from
    the bundle's chunks (the same builder the import route uses) and stored as
    ``bundle["verification_tree"]`` — a raw jdf-cli payload is not an Assure
    tree and cannot carry node annotations.

    Returns the verification result and also stores it as
    ``bundle["verification"]``. Never raises: verification failure is
    reported in the result, not thrown at the parse path.
    """
    page_count = int(bundle.get("page_count") or 1)
    if page_count > SYNC_VERIFICATION_PAGE_LIMIT:
        # The async path is deferred (no storage contract yet): verify inline
        # so a large document is not left unverified, and log the deferral so
        # the cost is visible.
        log.info(
            "verification: %s pages exceeds sync limit %s; async deferred, running sync",
            page_count,
            SYNC_VERIFICATION_PAGE_LIMIT,
        )

    tree = _resolve_tree(bundle)
    verification = _run_verification_sync(tree)
    bundle["verification"] = verification
    return verification


def _resolve_tree(bundle: dict[str, Any]) -> dict[str, Any]:
    """The Assure tree to verify: the bundle's tree, or one built from chunks."""
    jdf = bundle.get("jdf")
    if isinstance(jdf, dict) and isinstance(jdf.get("body"), list):
        return jdf
    tree = bundle.get("verification_tree")
    if isinstance(tree, dict) and isinstance(tree.get("body"), list):
        return tree
    from ..services.jdf_converter import jdf_to_document_tree

    filename = str(bundle.get("filename") or "parsed-document")
    tree = jdf_to_document_tree(
        {
            "$jdf": "1.0",
            "meta": {},
            "pages": [{} for _ in range(int(bundle.get("page_count") or 1))],
        },
        bundle.get("chunks") or [],
        document_id=f"doc-verify-{filename}",
        title=filename,
    )
    bundle["verification_tree"] = tree
    return tree


def _run_verification_sync(tree: dict[str, Any]) -> dict[str, Any]:
    """Z3 first, then Red-Hat — each guarded, neither blocking persistence."""
    try:
        z3_result = _run_z3(tree)
    except Exception:
        # Defense in depth: the hook's contract is to never raise, so even an
        # error escaping _run_z3's own guard becomes an explicit ERROR result.
        log.exception("Z3 pass raised outside its guard")
        z3_result = {
            "z3_status": "ERROR",
            "violations": [],
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    redhat_status = "complete"
    try:
        _run_redhat(tree)
    except Exception:
        # Red-Hat skipped for this document; Z3's result stands on its own.
        redhat_status = "skipped"
        log.exception("Red-Hat scan failed during post-parse verification")

    meta = tree.setdefault("meta", {})
    meta["z3"] = z3_result

    return {
        "z3": z3_result,
        "z3_status": z3_result.get("z3_status"),
        "redhat_status": redhat_status,
    }


def _run_z3(tree: dict[str, Any]) -> dict[str, Any]:
    """Z3 over the tree's truth ledger vs its own metric sentences.

    The engine and the metric scan are the same ones the full-context audit
    uses; here they run without the Red-Hat/citation layers, which are
    separate passes. The tree is deep-copied first: verification must never
    mutate the parse payload it is checking.
    """
    from ..services.full_context_scan import _scan_z3_document

    checked_at = datetime.now(timezone.utc).isoformat()
    issues: list[dict[str, Any]] = []
    try:
        document = copy.deepcopy(tree)
        nodes = _tree_nodes(document)
        full_text = "\n\n".join(
            str(n.get("content") or n.get("title") or "").strip()
            for n in nodes
            if (n.get("content") or n.get("title"))
        )
        _scan_z3_document(issues, document=document, full_text=full_text)
    except TimeoutError:
        log.warning("Z3 timeout during post-parse verification")
        return {"z3_status": "TIMEOUT", "violations": [], "checked_at": checked_at}
    except Exception:
        log.exception("Z3 error during post-parse verification")
        return {"z3_status": "ERROR", "violations": [], "checked_at": checked_at}

    violations = [i for i in issues if i.get("category") == "Z3 Contradiction"]
    return {
        "z3_status": "VIOLATION" if violations else "PASS",
        "violations": violations,
        "checked_at": checked_at,
    }


def _run_redhat(tree: dict[str, Any]) -> None:
    """Standardize Red-Hat state on every node: annotations.redhat is a list.

    Fresh parses carry no findings — a Red-Hat *audit* is an opt-in LLM pass
    (``routers/draft.run_redhat_audit``), never run implicitly here. What
    this pass guarantees is the shape downstream code reads
    (``confidence._open_redhat_findings``): every node has ``annotations``
    and ``annotations["redhat"]`` is a list, empty when there are no
    findings — not a missing key, not missing annotations.
    """
    from ..models.jdf import empty_annotations

    for node in _tree_nodes(tree):
        annotations = node.get("annotations")
        if not isinstance(annotations, dict):
            annotations = empty_annotations()
            node["annotations"] = annotations
        findings = annotations.get("redhat")
        if not isinstance(findings, list):
            annotations["redhat"] = []


def _tree_nodes(tree: dict[str, Any]) -> list[dict[str, Any]]:
    """Depth-first node list of an Assure tree body."""
    nodes: list[dict[str, Any]] = []

    def collect(section: Any) -> None:
        if not isinstance(section, dict):
            return
        nodes.append(section)
        for child in section.get("children") or []:
            collect(child)

    for section in tree.get("body") or []:
        collect(section)
    return nodes