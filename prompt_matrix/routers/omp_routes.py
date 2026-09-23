"""Flask JSON wrappers around the local Open Memory Protocol client."""

from __future__ import annotations

from flask import jsonify, request

try:
    from ..lib.http_errors import first_line
    from ..omp_client import omp_list_memories, omp_recall, omp_remember
except ImportError:
    from lib.http_errors import first_line
    from omp_client import omp_list_memories, omp_recall, omp_remember


def register_omp_routes(app) -> None:
    @app.post("/api/omp/remember")
    def omp_remember_route():
        data = request.get_json(silent=True) or {}
        content = str(data.get("content") or "").strip()
        if not content:
            return jsonify({"error": "content is required"}), 400
        result = omp_remember(
            str(data.get("key") or ""),
            content,
            tags=data.get("tags") if isinstance(data.get("tags"), list) else None,
        )
        code = 201 if result.get("id") else (result.get("status") or 502)
        if code == 201:
            return jsonify(result), 201
        return jsonify(result), int(code) if isinstance(code, int) and code >= 400 else 502

    @app.get("/api/omp/recall")
    def omp_recall_route():
        key = str(request.args.get("key") or request.args.get("q") or "").strip()
        if not key:
            body = request.get_json(silent=True) or {}
            key = str(body.get("key") or body.get("q") or "").strip()
        if not key:
            return jsonify({"error": "key is required"}), 400
        result = omp_recall(key)
        if result.get("error") and not (result.get("memories") or result.get("results")):
            # The client swallows connection errors into ``{"error": ...}``; a
            # 200 here made an unreachable OMP look like an empty recall.
            return jsonify({"ok": False, "error": first_line(result["error"])}), 502
        return jsonify(result)

    @app.get("/api/omp/memories")
    def omp_list_route():
        raw = request.args.get("tags") or ""
        tags = [part.strip() for part in raw.split(",") if part.strip()] or None
        return jsonify(omp_list_memories(tags))
