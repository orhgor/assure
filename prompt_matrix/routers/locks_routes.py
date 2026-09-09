"""Lock evidence API — founder workbench Evidence Inspector."""

from __future__ import annotations

from flask import jsonify

try:
    from ..services.lock_evidence import find_lock_evidence
except ImportError:
    from services.lock_evidence import find_lock_evidence


def register_locks_routes(app) -> None:
    @app.get("/api/locks/<lock_hash>/evidence")
    def get_lock_evidence(lock_hash: str):
        evidence = find_lock_evidence(lock_hash)
        if not evidence:
            return jsonify({"ok": False, "error": f"Lock {lock_hash} not found"}), 404
        return jsonify({"ok": True, **evidence})
