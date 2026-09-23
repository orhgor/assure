import hmac
import os

try:
    from flask import request
except Exception:
    request = None

SERVICE_API_PATH_PREFIX = "/api/projects/"
SERVICE_API_PATH_SUFFIX = "/ingest-and-verify"


def is_service_api_request(path: str | None = None) -> bool:
    p = path
    if p is None and request is not None:
        p = request.path
    p = p or ""
    return p.startswith(SERVICE_API_PATH_PREFIX) and p.endswith(SERVICE_API_PATH_SUFFIX)


def service_api_token() -> str:
    return (os.environ.get("ASSURE_SERVICE_API_TOKEN") or "").strip()


def service_api_authorized(req=None) -> bool:
    token = service_api_token()
    if not token:
        return False
    r = req or request
    if r is None:
        return False

    auth = (r.headers.get("Authorization") or "").strip()
    presented = ""
    if auth.startswith("Bearer "):
        presented = auth[len("Bearer "):].strip()

    if not presented:
        presented = (r.headers.get("X-Assure-Service-Token") or "").strip()

    return bool(presented) and hmac.compare_digest(presented, token)
