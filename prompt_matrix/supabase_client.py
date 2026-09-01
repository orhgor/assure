"""Server-side Supabase REST. No official SDK. Service role never goes to the browser.

Uses the same urllib helpers as cloud_billing so CLI and tests stay dependency-light.
"""

from __future__ import annotations

from typing import Any


def get_supabase():
    """Return a REST handle, or None when URL/key are missing (source-install)."""
    try:
        from .cloud_billing import supabase_configured
    except ImportError:
        from cloud_billing import supabase_configured
    if not supabase_configured():
        return None
    return SupabaseRest()


class SupabaseRest:
    def request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        extra: dict | None = None,
    ) -> Any:
        try:
            from .cloud_billing import supabase_request
        except ImportError:
            from cloud_billing import supabase_request
        return supabase_request(method, path, body, extra)

    def rpc(self, name: str, params: dict[str, Any]) -> Any:
        return self.request("POST", "/rest/v1/rpc/" + name, params)
