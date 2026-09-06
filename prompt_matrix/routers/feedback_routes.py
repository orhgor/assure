"""User feedback via SQLite inbox (optional Resend)."""

from __future__ import annotations

import html
import os
import uuid
from typing import Any

from flask import current_app, jsonify, request

try:
    from ..cloud_auth import current_user_id
    from ..db.feedback_repository import list_feedback, save_feedback
    from ..github_actions_budget import actions_budget
    from ..lib.logger import get_audit_logger
except ImportError:
    from cloud_auth import current_user_id
    from db.feedback_repository import list_feedback, save_feedback
    from github_actions_budget import actions_budget
    from lib.logger import get_audit_logger


def _truthy(raw: Any) -> bool:
    return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}


def _resend_configured() -> bool:
    return bool((current_app.config.get("RESEND_API_KEY") or "").strip())


def _send_email_enabled() -> bool:
    flag = current_app.config.get("FEEDBACK_SEND_EMAIL")
    if flag is None:
        flag = os.environ.get("FEEDBACK_SEND_EMAIL", "")
    return _truthy(flag) and _resend_configured()


def _send_feedback_email(
    *,
    user_email: str,
    message: str,
    rating: int | None,
    url: str | None,
) -> None:
    import resend

    resend.api_key = current_app.config["RESEND_API_KEY"]
    safe_message = html.escape(message).replace("\n", "<br>")
    safe_email = html.escape(user_email)
    safe_url = html.escape(url or "Unknown")
    rating_label = str(rating) if rating is not None else "Not provided"
    resend.Emails.send(
        {
            "from": current_app.config["RESEND_FROM_EMAIL"],
            "to": [current_app.config["FEEDBACK_EMAIL"]],
            "subject": f"Assure AI Feedback from {user_email}",
            "html": (
                f"<p><strong>From:</strong> {safe_email}</p>"
                f"<p><strong>Rating:</strong> {rating_label}</p>"
                f"<p><strong>Page:</strong> {safe_url}</p>"
                "<hr>"
                "<p><strong>Message:</strong></p>"
                f"<p>{safe_message}</p>"
            ),
        }
    )


def _resolve_user_email(data: dict[str, Any]) -> str:
    explicit = str(data.get("user_email") or "").strip()
    if explicit:
        return explicit[:320]
    try:
        user_id = current_user_id()
        if user_id:
            return str(user_id)[:320]
    except RuntimeError:
        pass
    return "anonymous@getassureai.com"


def _parse_rating(raw: Any) -> int | None:
    if raw in (None, ""):
        return None
    try:
        rating = int(raw)
    except (TypeError, ValueError):
        return None
    if 1 <= rating <= 5:
        return rating
    return None


def submit_user_feedback(data: dict[str, Any]) -> tuple[Any, int]:
    message = str(data.get("message") or data.get("text") or "").strip()
    if not message:
        return jsonify({"error": "Message is required"}), 400
    if len(message) > 4000:
        return jsonify({"error": "Message too long (max 4000 chars)."}), 400

    user_email = _resolve_user_email(data)
    rating = _parse_rating(data.get("rating"))
    url = str(data.get("url") or data.get("page") or request.referrer or "")[:500]

    row = save_feedback(
        user_email=user_email,
        message=message,
        rating=rating,
        url=url,
    )

    get_audit_logger().log_audit(
        str(uuid.uuid4()),
        None,
        "USER_FEEDBACK",
        success=True,
        details={
            "feedback_id": row["id"],
            "user_email": user_email,
            "rating": rating,
            "url": url,
            "message_preview": message[:240],
        },
    )

    if _send_email_enabled():
        try:
            _send_feedback_email(
                user_email=user_email,
                message=message,
                rating=rating,
                url=url,
            )
        except Exception as exc:
            current_app.logger.error("Feedback email failed: %s", exc)

    return jsonify({"status": "ok", "message": "Feedback sent", "id": row["id"]}), 200


def register_feedback_routes(app, page_renderer=None) -> None:
    app.config.setdefault("RESEND_API_KEY", os.environ.get("RESEND_API_KEY", ""))
    app.config.setdefault(
        "RESEND_FROM_EMAIL",
        os.environ.get("RESEND_FROM_EMAIL", "notifications@getassureai.com"),
    )
    app.config.setdefault(
        "FEEDBACK_EMAIL",
        os.environ.get("FEEDBACK_EMAIL", "feedback@getassureai.com"),
    )

    @app.post("/api/feedback")
    def handle_feedback():
        data = request.get_json(silent=True) or {}
        if data.get("message") or data.get("text"):
            return submit_user_feedback(data)

        try:
            from ..cloud_auth import protect_request
        except ImportError:
            from cloud_auth import protect_request
        blocked = protect_request()
        if blocked is not None:
            return blocked

        run_hash = str(data.get("run_hash") or "").strip()
        if not run_hash:
            return jsonify({"error": "Missing run or message."}), 400
        rating_raw = data.get("rating")
        variation_id = data.get("variation_id")
        try:
            rating = int(rating_raw)
        except (TypeError, ValueError):
            return jsonify({"error": "Rating must be 0 or 1."}), 400
        if rating not in (0, 1):
            return jsonify({"error": "Rating must be 0 or 1."}), 400
        vid = None
        if variation_id not in (None, ""):
            try:
                vid = int(variation_id)
            except (TypeError, ValueError):
                vid = None
        try:
            from ..template_library import apply_feedback
        except ImportError:
            from template_library import apply_feedback
        out = apply_feedback(run_hash, rating, vid)
        if not out.get("ok"):
            return jsonify({"error": out.get("error") or "Could not save feedback."}), 400
        return jsonify({**out, "status": "ok"}), 200

    @app.post("/api/tester-feedback")
    def handle_tester_feedback():
        return submit_user_feedback(request.get_json(silent=True) or {})

    @app.get("/api/backstage/feedback")
    def backstage_feedback():
        rows = list_feedback(limit=200)
        return jsonify({"ok": True, "count": len(rows), "rows": rows})

    @app.get("/api/backstage/actions-budget")
    def backstage_actions_budget():
        return jsonify({"ok": True, **actions_budget()})

    @app.get("/backstage")
    def backstage_page():
        if page_renderer is not None:
            return page_renderer("backstage.html", "backstage")
        from flask import render_template

        return render_template("backstage.html")
