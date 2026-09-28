"""Source JDF routes: the stored jdf-cli document of a project's document.

``GET /api/projects/<id>/documents/<doc>/source.jdf`` streams the JSON the
ingest stored (``services/source_jdf.persist_source_jdf``) so the browser can
render it with ``@uurtech/jdf`` (``<jdf src="…/source.jdf">``);
``…/source.json`` describes it, and with ``?text=`` maps a selection made on
the rendered page back to element ids and a bbox. The web tier streams bytes
and reads the JSON it already stored — it parses no document here.

Resolution is by the project's ingest jobs (``source_jdf_key``, newest first),
then the project's intake reports; every key is checked against the project
prefix before a byte is read, so a document id from another project answers
404, not another tenant's pages.
"""

from __future__ import annotations

import logging

from flask import Response, jsonify, request

try:
    from ..middleware import project_ownership_required
    from ..rbac import requires
    from ..services.object_store import get_object_store
    from ..services.source_jdf import (
        describe_source_jdf,
        find_elements_for_text,
        key_in_project,
        load_source_jdf,
        resolve_source_jdf_key,
        source_jdf_url,
    )
except ImportError:
    from middleware import project_ownership_required  # type: ignore
    from rbac import requires  # type: ignore
    from services.object_store import get_object_store  # type: ignore
    from services.source_jdf import (  # type: ignore
        describe_source_jdf,
        find_elements_for_text,
        key_in_project,
        load_source_jdf,
        resolve_source_jdf_key,
        source_jdf_url,
    )

log = logging.getLogger(__name__)

_NOT_STORED = "no source document is stored for this document"


def _resolve(project_id: str, document_id: str) -> str | None:
    revision = (request.args.get("revision") or "").strip() or None
    key = resolve_source_jdf_key(project_id, document_id, revision=revision)
    if not key or not key_in_project(key, project_id):
        return None
    return key


def register_documents_routes(app) -> None:
    @app.get("/api/projects/<project_id>/documents/<document_id>/source.jdf")
    @requires("projects.read")
    @project_ownership_required
    def document_source_jdf(project_id: str, document_id: str):
        """Stream the stored source JDF. ``?revision=`` selects one stored
        revision (the default is the newest). Private, cacheable for an hour,
        ETag = the object key: a stored revision never changes under its key,
        so a matching ``If-None-Match`` is a 304 without a store read."""
        key = _resolve(project_id, document_id)
        if not key:
            return jsonify({"ok": False, "error": _NOT_STORED}), 404
        etag = f'"{key}"'
        if etag in [t.strip() for t in (request.headers.get("If-None-Match") or "").split(",")]:
            resp = Response(status=304)
            resp.headers["ETag"] = etag
            resp.headers["Cache-Control"] = "private, max-age=3600"
            return resp
        store = get_object_store()
        try:
            if not store.exists(key):
                return jsonify({"ok": False, "error": _NOT_STORED}), 404
            data = store.get_bytes(key)
        except Exception:  # noqa: BLE001 — a store that cannot be read is "not stored", logged
            log.exception("source JDF %s could not be read", key)
            return jsonify({"ok": False, "error": _NOT_STORED}), 404
        resp = Response(data, mimetype="application/json")
        resp.headers["Cache-Control"] = "private, max-age=3600"
        resp.headers["ETag"] = etag
        resp.headers["X-Source-Jdf-Key"] = key
        return resp

    @app.get("/api/projects/<project_id>/documents/<document_id>/source.json")
    @requires("projects.read")
    @project_ownership_required
    def document_source_json(project_id: str, document_id: str):
        """Describe the stored source JDF, or with ``?text=…[&page=N]`` map a
        text selection to element ids: ``{ok, element_ids, page, bbox_rel,
        found}``. ``found: false`` with no ids when the text is not verbatim in
        the page — the nearest element is never substituted."""
        key = _resolve(project_id, document_id)
        if not key:
            return jsonify({"ok": False, "error": _NOT_STORED}), 404
        doc = load_source_jdf(key)
        if doc is None:
            return jsonify({"ok": False, "error": _NOT_STORED}), 404
        text = request.args.get("text")
        if text is not None:
            page_raw = (request.args.get("page") or "").strip()
            page = None
            if page_raw:
                try:
                    page = int(page_raw)
                except ValueError:
                    return jsonify({"ok": False, "error": "page must be an integer"}), 400
                if page < 1:
                    return jsonify({"ok": False, "error": "page must be 1 or greater"}), 400
            if len(text) > 4000:
                return jsonify({"ok": False, "error": "text is longer than 4000 characters"}), 400
            hit = find_elements_for_text(doc, text, page=page)
            return jsonify({"ok": True, "key": key, "url": source_jdf_url(project_id, document_id), **hit})
        info = describe_source_jdf(key, doc)
        return jsonify({"ok": True, "url": source_jdf_url(project_id, document_id), **info})
