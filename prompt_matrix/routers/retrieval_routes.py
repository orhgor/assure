"""The unanchored claim's routes: why it is unanchored, and what would ground it.

    POST /api/projects/<project_id>/nodes/<node_id>/gap-analysis      (2B)
    POST /api/projects/<project_id>/nodes/<node_id>/retrieval/search  (2C.3)
    POST /api/projects/<project_id>/nodes/<node_id>/retrieval/fetch   (2C.4)
    POST /api/projects/<project_id>/nodes/<node_id>/retrieval/reject  (2C.5)

The gap call's failure is a first-class state with its own status: a model that
cannot answer returns ``{ok: false, reason: "gap_analysis_unavailable"}`` with
503, and the drawer renders the first line and the upload button. Refusals from the retrieval path — a denied
host, a non-HTTPS URL, a page over the caps — carry the reason and are logged;
the fetch route answers 4xx/5xx for those, because nothing was collected.
"""

from __future__ import annotations

import uuid

from flask import jsonify, request
from pydantic import BaseModel, Field

try:
    from ..db.jdf_repository import fetch_latest_jdf_or_empty
    from ..db.substrate_repository import list_substrate_for_project
    from ..history import get_db
    from ..lib.logger import get_audit_logger
    from ..middleware import project_ownership_required
    from ..models.jdf import get_node_by_id
    from ..services.evidence_gap import analyze_gap, near_miss_sentences
    from ..services.web_retrieval import (
        RetrievalError,
        fetch_authoritative_page,
        ingest_fetched_source,
        reanchor,
        search_authoritative,
        tag_for,
    )
except ImportError:
    from db.jdf_repository import fetch_latest_jdf_or_empty
    from db.substrate_repository import list_substrate_for_project
    from history import get_db
    from lib.logger import get_audit_logger
    from middleware import project_ownership_required
    from models.jdf import get_node_by_id
    from services.evidence_gap import analyze_gap, near_miss_sentences
    from services.web_retrieval import (
        RetrievalError,
        fetch_authoritative_page,
        ingest_fetched_source,
        reanchor,
        search_authoritative,
        tag_for,
    )


class RetrievalPayload(BaseModel):
    """The drawer's request bodies. ``query`` is the gap call's third line."""

    query: str = ""
    url: str = ""
    host: str = ""
    title: str = ""
    reason: str = Field(default="", max_length=300)


def _audit(action: str, project_id: str, **details: object) -> None:
    audit = get_audit_logger()
    audit.log_audit(
        str(uuid.uuid4()),
        project_id,
        action,
        success=True,
        details={k: v for k, v in details.items() if v not in ("", None)},
    )


def _project_exists(project_id: str) -> bool:
    """True when the project row exists.

    Every route below checks this before it does anything else — before a search
    runs, before a page is fetched, before an audit row is written. A search for a
    project that is not there used to run to completion and record its result: an
    audit row naming a project that has never existed. On a database created
    before ``db/connection.py`` declared ``audit_log``'s foreign key, that row is
    an orphan (those databases still need
    ``scripts/aws/migrate_fk_constraints.py``); on one that declares the clause,
    the write fails instead and becomes a counted drop rather than a row. Either
    way the rejection belongs on this side of the write.
    """
    row = get_db().execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone()
    return row is not None


def register_retrieval_routes(app) -> None:
    @app.post("/api/projects/<project_id>/nodes/<node_id>/gap-analysis")
    @project_ownership_required
    def node_gap_analysis(project_id: str, node_id: str):
        if not _project_exists(project_id):
            return jsonify({"ok": False, "error": "project not found"}), 404
        document = fetch_latest_jdf_or_empty(project_id)
        node = get_node_by_id(document, node_id)
        if node is None:
            return jsonify({"ok": False, "error": "node not found"}), 404
        claim = str(node.get("content") or "").strip()
        if not claim:
            return jsonify({"ok": False, "reason": "no_claim", "error": "This paragraph is empty."}), 400
        rows = list_substrate_for_project(project_id, with_text=True)
        included = [row for row in rows if row.get("included") is not False]
        analysis = analyze_gap(claim, included, project_id=project_id)
        if analysis is None:
            # The drawer's failure path: the statement and the upload button, no
            # invented reason. Near-misses are still reported so the caller can
            # say whether the source came close at all. 503, not 200: the
            # analysis is a dependency that was unavailable, and a monitor
            # reading status codes must see that.
            return (
                jsonify(
                    {
                        "ok": False,
                        "reason": "gap_analysis_unavailable",
                        "error": "Gap analysis is unavailable (no model answer).",
                        "near_miss_count": len(near_miss_sentences(claim, included)),
                    }
                ),
                503,
            )
        return jsonify({"ok": True, **analysis.as_payload()})

    @app.post("/api/projects/<project_id>/nodes/<node_id>/retrieval/search")
    @project_ownership_required
    def node_retrieval_search(project_id: str, node_id: str):
        if not _project_exists(project_id):
            return jsonify({"ok": False, "error": "project not found"}), 404
        payload = RetrievalPayload.model_validate(request.get_json(silent=True) or {})
        try:
            result = search_authoritative(payload.query)
        except RetrievalError as exc:
            _audit("RETRIEVAL_SEARCH_REFUSED", project_id, node_id=node_id, reason=exc.reason)
            return jsonify({"ok": False, "reason": exc.reason, "error": exc.message}), exc.status
        _audit(
            "RETRIEVAL_SEARCH",
            project_id,
            node_id=node_id,
            query=result["query"],
            cards=len(result["cards"]),
            rejected=len(result["hosts_rejected"]),
        )
        return jsonify(result)

    @app.post("/api/projects/<project_id>/nodes/<node_id>/retrieval/fetch")
    @project_ownership_required
    def node_retrieval_fetch(project_id: str, node_id: str):
        if not _project_exists(project_id):
            return jsonify({"ok": False, "error": "project not found"}), 404
        payload = RetrievalPayload.model_validate(request.get_json(silent=True) or {})
        try:
            # The allowlist decides before any request is made.
            page = fetch_authoritative_page(payload.url)
        except RetrievalError as exc:
            _audit(
                "RETRIEVAL_FETCH_REFUSED",
                project_id,
                node_id=node_id,
                url=payload.url,
                host=payload.host,
                reason=exc.reason,
            )
            return jsonify({"ok": False, "reason": exc.reason, "error": exc.message}), exc.status

        try:
            entry = ingest_fetched_source(project_id, page)
        except Exception as exc:   # storage failure: nothing was collected into the vault
            _audit("RETRIEVAL_FETCH_FAILED", project_id, node_id=node_id, url=page.url, reason=str(exc))
            return jsonify({"ok": False, "reason": "ingest_failed", "error": str(exc)}), 502

        gate = reanchor(project_id, node_id=node_id)
        _audit(
            "RETRIEVAL_FETCH",
            project_id,
            node_id=node_id,
            url=page.url,
            fetched_url=page.host,
            source_id=entry.get("id"),
            instruction_like=page.instruction_like,
            anchored=gate["anchored"],
            persisted=gate["persisted"],
        )
        return jsonify(
            {
                "ok": True,
                "tag": tag_for(page),
                "source": {
                    "id": entry.get("id"),
                    "label": entry.get("label"),
                    "fetched_url": entry.get("fetched_url"),
                },
                "page": page.as_payload(),
                "anchored": gate["anchored"],
                "origin": gate["origin"],
                "entailment": gate["entailment"],
                "stats": gate["stats"],
                "origins": gate["origins"],
                "document": gate["document"],
                "persisted": gate["persisted"],
            }
        )

    @app.post("/api/projects/<project_id>/nodes/<node_id>/retrieval/reject")
    @project_ownership_required
    def node_retrieval_reject(project_id: str, node_id: str):
        if not _project_exists(project_id):
            return jsonify({"ok": False, "error": "project not found"}), 404
        payload = RetrievalPayload.model_validate(request.get_json(silent=True) or {})
        _audit(
            "RETRIEVAL_REJECT",
            project_id,
            node_id=node_id,
            url=payload.url,
            host=payload.host,
            title=payload.title,
            reason=payload.reason or "rejected by user",
        )
        return jsonify({"ok": True, "host": payload.host})
