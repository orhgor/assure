"""CRUD /api/prompts — SQLite prompt library."""

from __future__ import annotations

from flask import jsonify, request
from pydantic import BaseModel, Field

try:
    from ..db.prompts_repository import (
        create_prompt,
        delete_prompt,
        fetch_prompt,
        list_prompts,
        update_prompt,
    )
except ImportError:
    from db.prompts_repository import (
        create_prompt,
        delete_prompt,
        fetch_prompt,
        list_prompts,
        update_prompt,
    )


def _current_user_id() -> str | None:
    try:
        from ..cloud_auth import current_user_id
    except ImportError:
        try:
            from cloud_auth import current_user_id
        except ImportError:
            return None
    return current_user_id()


class PromptCreatePayload(BaseModel):
    name: str
    content: str
    class_: str = Field(default="research", alias="class")
    tags: list[str] = Field(default_factory=list)
    is_global: bool = False


class PromptUpdatePayload(BaseModel):
    name: str | None = None
    content: str | None = None
    class_: str | None = Field(default=None, alias="class")
    tags: list[str] | None = None


def register_prompt_routes(app) -> None:
    @app.get("/api/prompts")
    def get_prompts():
        user_id = _current_user_id()
        prompts = list_prompts(user_id=user_id, include_global=True)
        return jsonify({"ok": True, "prompts": prompts, "count": len(prompts)})

    @app.get("/api/prompts/<prompt_id>")
    def get_prompt(prompt_id: str):
        row = fetch_prompt(prompt_id)
        if not row:
            return jsonify({"error": "not found"}), 404
        return jsonify({"ok": True, "prompt": row})

    @app.post("/api/prompts")
    def post_prompt():
        data = request.get_json(silent=True) or {}
        try:
            payload = PromptCreatePayload.model_validate(data)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400
        created = create_prompt(
            name=payload.name.strip(),
            content=payload.content,
            prompt_class=payload.class_,
            tags=payload.tags,
            user_id=_current_user_id(),
            is_global=bool(payload.is_global),
        )
        return jsonify({"ok": True, "prompt": created}), 201

    @app.put("/api/prompts/<prompt_id>")
    def put_prompt(prompt_id: str):
        data = request.get_json(silent=True) or {}
        try:
            payload = PromptUpdatePayload.model_validate(data)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400
        updated = update_prompt(
            prompt_id,
            name=payload.name,
            content=payload.content,
            prompt_class=payload.class_,
            tags=payload.tags,
        )
        if not updated:
            return jsonify({"error": "not found"}), 404
        return jsonify({"ok": True, "prompt": updated})

    @app.delete("/api/prompts/<prompt_id>")
    def remove_prompt(prompt_id: str):
        if not delete_prompt(prompt_id):
            return jsonify({"error": "not found"}), 404
        return jsonify({"ok": True})
