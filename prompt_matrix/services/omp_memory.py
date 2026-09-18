"""PEM + OMP memory: compile cache, Red-Hat context, vault index.

OMP content is capped at ~10k characters, so large JDF ASTs live in SQLite
(``pipeline_cache``). OMP stores a queryable key plus a summary (or the full
JSON when it fits). Every OMP call is best-effort: downtime must not break
compile or Red-Hat streams.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

try:
    from ..db.pipeline_cache import fetch_pipeline_cache, save_pipeline_cache, sqlite_cache_expired
    from ..omp_client import (
        sanitize_omp_tag,
        safe_omp_recall,
        safe_omp_remember,
    )
except ImportError:
    from db.pipeline_cache import fetch_pipeline_cache, save_pipeline_cache, sqlite_cache_expired
    from omp_client import sanitize_omp_tag, safe_omp_recall, safe_omp_remember

CACHE_MARKER = "PEM_CACHE_V1"


def omp_cache_enabled() -> bool:
    return os.getenv("PEM_OMP_CACHE", "1").strip() not in {"0", "false", "no"}


# Unchanged by the ask-shaped compile prompt (services/answer_shape) on purpose:
# the version is inside the cache key, so bumping it would invalidate every entry
# — and a cold compile on a warm project persists a new revision. The shape is
# folded into the key where the prompt actually differs (routers/draft), which
# leaves every memo entry — the demo's warm compile among them — matching.
PIPELINE_VERSION = 3


def compile_cache_key(
    project_id: str,
    source_text: str,
    target_ai: str = "",
    version: int = PIPELINE_VERSION,
    prompt_material: str = "",
) -> str:
    # Stable order: project_id | source_text | target_ai | str(version)
    parts = [
        str(project_id or ""),
        str(source_text or ""),
        str(target_ai or ""),
        str(version),
    ]
    # The prompt is KEY MATERIAL, not a note beside the key. `prompt_material` is
    # "<version>:<sha256 of the prompt>[:8]": the hash is the half that matters,
    # because it moves the key *because the prompt changed* with nobody remembering
    # to bump anything. The version rides in front of it for readability only.
    #
    # Before this, nothing in the digest named the prompt, so an edit left every warm
    # entry warm and replayed a draft written under the old prompt — measured: an
    # edit moved the prompt's sha256 (c2b7926f -> 1cac8b13) and the key did not move
    # (ast:p:de9116cd).
    #
    # Empty for a frozen artifact (routers/draft._prompt_key_material), so the key it
    # was written under is the key it still composes and its document still replays.
    if prompt_material:
        parts.append(f"prompt:{prompt_material}")
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:8]
    pid = sanitize_omp_tag(project_id or "", max_len=32)
    return f"ast:{pid}:{digest}"


def redhat_cache_key(project_id: str) -> str:
    return f"redhat:{sanitize_omp_tag(project_id, max_len=32)}"


def file_cache_key(file_id: str) -> str:
    return f"file:{sanitize_omp_tag(file_id, max_len=40)}"


def _parse_cache_blob(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    text = raw.strip()
    if text.startswith(CACHE_MARKER):
        text = text[len(CACHE_MARKER) :].strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def load_ast_cache(cache_key: str) -> dict[str, Any] | None:
    if not omp_cache_enabled() or not cache_key:
        return None
    try:
        local = fetch_pipeline_cache(cache_key)
        if isinstance(local, dict) and local.get("compiled"):
            return local
        if sqlite_cache_expired(cache_key):
            return None
    except Exception:
        local = None
    try:
        recalled = safe_omp_recall(cache_key)
        if isinstance(recalled, dict):
            if recalled.get("compiled"):
                return recalled
            if recalled.get("sqlite"):
                return local if isinstance(local, dict) else None
            if recalled.get("document"):
                document = recalled.get("document") or {}
                locks = recalled.get("locks") or []
                return {
                    "compiled": {
                        "document": document,
                        "nodes": document.get("body") or recalled.get("nodes") or [],
                        "locks": locks,
                        "node_count": len(document.get("body") or []),
                        "lock_count": len(locks),
                        "draft_text": recalled.get("draft_text") or "",
                    },
                    "verified": recalled.get("verified") or {},
                }
            blob = recalled
        else:
            blob = _parse_cache_blob(recalled if isinstance(recalled, str) else None)
        if not blob:
            return local if isinstance(local, dict) else None
        if blob.get("sqlite"):
            return local if isinstance(local, dict) else None
        if blob.get("compiled"):
            return blob
    except Exception:
        pass
    return local if isinstance(local, dict) else None


def save_ast_cache(cache_key: str, project_id: str, payload: dict[str, Any]) -> None:
    if not omp_cache_enabled() or not cache_key or not isinstance(payload, dict):
        return
    try:
        save_pipeline_cache(cache_key, project_id, "ast", payload)
    except Exception:
        pass
    summary = {
        "k": cache_key,
        "node_count": (payload.get("compiled") or {}).get("node_count"),
        "lock_count": (payload.get("compiled") or {}).get("lock_count"),
    }
    body = json.dumps(payload, ensure_ascii=False)
    if len(CACHE_MARKER) + 1 + len(body) > 8800:
        body = json.dumps({**summary, "sqlite": True}, ensure_ascii=False)
    try:
        safe_omp_remember(
            cache_key,
            f"{CACHE_MARKER}\n{body}",
            tags=["ast", sanitize_omp_tag(project_id, max_len=32)],
        )
    except Exception:
        pass


def load_redhat_critique(project_id: str) -> str | None:
    if not omp_cache_enabled():
        return None
    key = redhat_cache_key(project_id)
    try:
        local = fetch_pipeline_cache(key)
        if isinstance(local, dict):
            text = str(local.get("critique") or "").strip()
            if text:
                return text
        if sqlite_cache_expired(key):
            return None
    except Exception:
        pass
    try:
        recalled = safe_omp_recall(key)
        if isinstance(recalled, dict):
            text = str(recalled.get("critique") or recalled.get("content") or "").strip()
            return text or None
        blob = _parse_cache_blob(recalled if isinstance(recalled, str) else None)
        if blob and blob.get("critique"):
            return str(blob["critique"]).strip() or None
        if isinstance(recalled, str) and recalled and not recalled.startswith(CACHE_MARKER):
            return recalled.strip() or None
    except Exception:
        pass
    return None


def save_redhat_critique(project_id: str, critique: str) -> None:
    if not omp_cache_enabled():
        return
    text = str(critique or "").strip()
    if not text:
        return
    key = redhat_cache_key(project_id)
    payload = {"critique": text}
    try:
        save_pipeline_cache(key, project_id, "redhat", payload)
    except Exception:
        pass
    try:
        safe_omp_remember(
            key,
            f"{CACHE_MARKER}\n{json.dumps(payload, ensure_ascii=False)}",
            tags=["redhat", sanitize_omp_tag(project_id, max_len=32)],
        )
    except Exception:
        pass


def remember_vault_file(
    project_id: str,
    file_id: str,
    *,
    filename: str = "",
    text: str = "",
) -> None:
    excerpt = str(text or "").strip()
    if not excerpt:
        return
    key = file_cache_key(file_id)
    header = f"{filename or 'vault'} [{file_id}] project={project_id}\n"
    body = header + excerpt
    try:
        safe_omp_remember(
            key,
            body,
            tags=["vault", sanitize_omp_tag(project_id, max_len=32)],
        )
    except Exception:
        pass


def remember_refine_diff(
    project_id: str,
    node_id: str,
    *,
    before: str = "",
    after: str = "",
) -> None:
    key = (
        f"refine:{sanitize_omp_tag(project_id, max_len=16)}:{sanitize_omp_tag(node_id, max_len=20)}"
    )
    body = (
        f"node={node_id}\n"
        f"before: {str(before or '')[:2000]}\n"
        f"after: {str(after or '')[:2000]}"
    )
    try:
        safe_omp_remember(
            key,
            body,
            tags=["refine", sanitize_omp_tag(project_id, max_len=32)],
        )
    except Exception:
        pass
