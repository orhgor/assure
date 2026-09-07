"""Application-level TF-IDF cache for vault source ranking."""

from __future__ import annotations

import math
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())


@dataclass
class TfidfIndex:
    workspace_id: str
    source_fingerprint: tuple[str, ...]
    idf: dict[str, float] = field(default_factory=dict)
    doc_vectors: dict[str, dict[str, float]] = field(default_factory=dict)
    sources: list[dict[str, Any]] = field(default_factory=list)
    built_at: float = 0.0


_CACHE: dict[str, TfidfIndex] = {}
_CACHE_LOCK = threading.Lock()


def invalidate_workspace_cache(workspace_id: str) -> None:
    with _CACHE_LOCK:
        _CACHE.pop(workspace_id or "default", None)


def source_fingerprint(rows: list[dict[str, Any]]) -> tuple[str, ...]:
    return tuple(sorted(str(r.get("id") or "") for r in rows))


def build_tfidf_index(rows: list[dict[str, Any]], *, workspace_id: str) -> TfidfIndex:
    docs: list[tuple[str, str, dict[str, Any]]] = []
    for row in rows:
        sid = str(row.get("id") or "")
        text = str(row.get("extracted_text") or row.get("content") or "")
        docs.append((sid, text, row))

    df: dict[str, int] = {}
    doc_tokens: list[tuple[str, dict[str, float], dict[str, Any]]] = []
    for sid, text, row in docs:
        counts: dict[str, int] = {}
        for tok in _tokenize(text):
            counts[tok] = counts.get(tok, 0) + 1
        for tok in counts:
            df[tok] = df.get(tok, 0) + 1
        tf = {tok: count / max(len(_tokenize(text)), 1) for tok, count in counts.items()}
        doc_tokens.append((sid, tf, row))

    n_docs = max(len(doc_tokens), 1)
    idf = {tok: math.log((1 + n_docs) / (1 + freq)) + 1.0 for tok, freq in df.items()}
    vectors: dict[str, dict[str, float]] = {}
    sources: list[dict[str, Any]] = []
    for sid, tf, row in doc_tokens:
        vec = {tok: weight * idf.get(tok, 0.0) for tok, weight in tf.items()}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        vectors[sid] = {tok: v / norm for tok, v in vec.items()}
        sources.append(
            {
                "id": sid,
                "name": str(row.get("filename") or row.get("name") or sid),
                "excerpt": str(row.get("extracted_text") or row.get("content") or "")[:4000],
                "web": bool(row.get("web")),
            }
        )
    return TfidfIndex(
        workspace_id=workspace_id,
        source_fingerprint=source_fingerprint(rows),
        idf=idf,
        doc_vectors=vectors,
        sources=sources,
        built_at=time.perf_counter(),
    )


def get_cached_index(workspace_id: str, rows: list[dict[str, Any]]) -> TfidfIndex:
    ws = workspace_id or "default"
    fingerprint = source_fingerprint(rows)
    with _CACHE_LOCK:
        cached = _CACHE.get(ws)
        if cached and cached.source_fingerprint == fingerprint:
            return cached
        index = build_tfidf_index(rows, workspace_id=ws)
        _CACHE[ws] = index
        return index


def query_vector(tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
    if not tokens:
        return {}
    counts: dict[str, int] = {}
    for tok in tokens:
        counts[tok] = counts.get(tok, 0) + 1
    tf = {tok: count / len(tokens) for tok, count in counts.items()}
    vec = {tok: weight * idf.get(tok, 0.0) for tok, weight in tf.items()}
    norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
    return {tok: v / norm for tok, v in vec.items()}


def cosine_similarity(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    shared = set(a) & set(b)
    return sum(a[t] * b[t] for t in shared)
