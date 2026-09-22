"""Integrations: AWS (S3 / IAM) configured from the workbench.

The Sources panel asks ``GET /api/integrations/aws`` and shows a warning when
uploads would stay on local disk or the bucket is unreachable; the form posts
``PUT /api/integrations/aws``, which saves (secret encrypted) and immediately
probes the bucket with the entered identity, so a wrong key is reported on the
spot rather than on the first upload.
"""

from __future__ import annotations

import logging

from flask import jsonify, request

try:
    from ..cloud_auth import login_required, role_required
    from ..services import aws_integration
except ImportError:
    from cloud_auth import login_required, role_required
    from services import aws_integration

log = logging.getLogger(__name__)


def register_integrations_routes(app) -> None:
    @app.get("/api/integrations/aws")
    @login_required
    def aws_integration_status():
        probe = (request.args.get("probe") or "1").strip().lower() not in ("0", "false", "no")
        try:
            return jsonify({"ok": True, **aws_integration.status(probe=probe)})
        except Exception as exc:
            log.exception("aws integration status failed")
            return jsonify({"ok": False, "error": f"{exc.__class__.__name__}: {str(exc).splitlines()[0][:200]}"}), 500

    @app.put("/api/integrations/aws")
    @login_required
    @role_required("admin")
    def aws_integration_save():
        data = request.get_json(silent=True) or {}
        bucket = str(data.get("bucket") or "").strip()
        region = str(data.get("region") or "").strip()
        access_key_id = str(data.get("access_key_id") or "").strip()
        secret = str(data.get("secret_access_key") or "")
        prefix = str(data.get("prefix") or "assure/").strip() or "assure/"
        if not bucket:
            return jsonify({"ok": False, "error": "bucket is required"}), 400
        if not region:
            return jsonify({"ok": False, "error": "region is required (e.g. eu-central-1)"}), 400
        if access_key_id and not access_key_id.startswith(("AKIA", "ASIA")):
            return jsonify({"ok": False, "error": "access_key_id does not look like an AWS access key id"}), 400
        if secret.strip() and not access_key_id:
            return jsonify({"ok": False, "error": "access_key_id is required with a secret"}), 400
        try:
            from ..cloud_auth import current_user_id
        except ImportError:
            from cloud_auth import current_user_id
        try:
            result = aws_integration.save(
                access_key_id=access_key_id,
                secret_access_key=secret,
                region=region,
                bucket=bucket,
                prefix=prefix,
                updated_by=current_user_id() or None,
            )
        except Exception as exc:
            log.exception("aws integration save failed")
            return jsonify({"ok": False, "error": f"{exc.__class__.__name__}: {str(exc).splitlines()[0][:200]}"}), 500
        code = 200 if result.get("reachable") else 202
        return jsonify({"ok": True, **result}), code

    @app.delete("/api/integrations/aws")
    @login_required
    @role_required("admin")
    def aws_integration_clear():
        aws_integration.clear()
        return jsonify({"ok": True, **aws_integration.status(probe=False)})
