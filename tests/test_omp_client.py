"""OMP client talks to /v1/memories, not the guessed /api/memories paths."""

from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError

from prompt_matrix.omp_client import (
    omp_delete_memory,
    omp_list_memories,
    omp_recall,
    omp_remember,
    safe_omp_recall,
    safe_omp_remember,
)


class _Resp:
    def __init__(self, payload: dict, status: int = 200):
        self.status = status
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_remember_posts_v1_memories():
    captured: dict = {}

    def fake_urlopen(req, timeout=5):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _Resp({"id": "mem_test", "content": captured["body"]["content"]}, 201)

    with patch("prompt_matrix.omp_client.urllib.request.urlopen", side_effect=fake_urlopen):
        with patch(
            "prompt_matrix.omp_client._get_headers",
            return_value={"Content-Type": "application/json"},
        ):
            out = omp_remember("workspace", "PEM lives at /Users/og/Untitled", tags=["pem"])
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/v1/memories")
    assert "/api/memories" not in captured["url"]
    assert captured["body"]["content"].startswith("PEM lives")
    assert captured["body"]["type"] == "semantic"
    assert "workspace" in captured["body"]["tags"]
    assert "pem" in captured["body"]["tags"]
    assert out["id"] == "mem_test"


def test_recall_searches_not_get_by_key():
    captured: dict = {}

    def fake_urlopen(req, timeout=5):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _Resp({"memories": [], "total": 0})

    with patch("prompt_matrix.omp_client.urllib.request.urlopen", side_effect=fake_urlopen):
        with patch(
            "prompt_matrix.omp_client._get_headers",
            return_value={"Content-Type": "application/json"},
        ):
            omp_recall("TypeScript-style")
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/v1/memories/search")
    assert captured["body"]["q"] == "TypeScript-style"


def test_list_uses_query_tags():
    captured: dict = {}

    def fake_urlopen(req, timeout=5):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        return _Resp({"memories": [], "total": 0})

    with patch("prompt_matrix.omp_client.urllib.request.urlopen", side_effect=fake_urlopen):
        with patch(
            "prompt_matrix.omp_client._get_headers",
            return_value={"Content-Type": "application/json"},
        ):
            omp_list_memories(["pem", "omp"])
    assert captured["method"] == "GET"
    assert "/v1/memories?" in captured["url"]
    assert "tags=pem%2Comp" in captured["url"] or "tags=pem,omp" in captured["url"]


def test_http_error_returns_json_without_raising():
    err = HTTPError(
        "http://localhost:3456/v1/memories",
        401,
        "unauthorized",
        hdrs=None,
        fp=BytesIO(b'{"error":"unauthorized"}'),
    )

    with patch("prompt_matrix.omp_client.urllib.request.urlopen", side_effect=err):
        with patch(
            "prompt_matrix.omp_client._get_headers",
            return_value={"Content-Type": "application/json"},
        ):
            out = omp_remember("k", "secret-should-not-matter")
    assert out.get("error") == "unauthorized"
    assert out.get("status") == 401


def test_safe_omp_recall_returns_none_on_error():
    with patch("prompt_matrix.omp_client._request", return_value={"error": "timeout"}):
        assert safe_omp_recall("ast:default:abc") is None


def test_safe_omp_recall_parses_json_content():
    payload = {"document": {"body": []}, "verified": {"ok": True}}
    raw = {
        "memories": [
            {"content": json.dumps(payload), "tags": ["ast:default:abc123"]},
        ]
    }

    with patch("prompt_matrix.omp_client._request", return_value=raw):
        out = safe_omp_recall("ast:default:abc123")
    assert out == payload


def test_safe_omp_remember_never_raises():
    with patch("prompt_matrix.omp_client._request", side_effect=TimeoutError("slow")):
        safe_omp_remember("k", {"ok": True}, tags=["ast"])


def test_delete_memory_targets_the_row_id():
    """OMP deletes by id — the key this app writes is only a tag."""
    captured: dict = {}

    def fake_request(method, path, **_kwargs):
        captured["method"] = method
        captured["path"] = path
        return {"ok": True, "status": 204}

    with patch("prompt_matrix.omp_client._request", side_effect=fake_request):
        assert omp_delete_memory("mem_ab12") is True
    assert captured == {"method": "DELETE", "path": "/v1/memories/mem_ab12"}


def test_delete_memory_reports_failure():
    with patch(
        "prompt_matrix.omp_client._request",
        return_value={"error": "memory_not_found", "status": 404},
    ):
        assert omp_delete_memory("mem_gone") is False
    assert omp_delete_memory("") is False


def test_list_memories_pages_a_namespace():
    captured: dict = {}

    def fake_urlopen(req, timeout=5):
        captured["url"] = req.full_url
        return _Resp({"memories": [], "total": 0})

    with patch("prompt_matrix.omp_client.urllib.request.urlopen", side_effect=fake_urlopen):
        with patch(
            "prompt_matrix.omp_client._get_headers",
            return_value={"Content-Type": "application/json"},
        ):
            omp_list_memories(limit=200, offset=400)
    assert "limit=200" in captured["url"]
    assert "offset=400" in captured["url"]
    assert "namespace=project%3Aprompt-matrix" in captured["url"]
