"""Perplexity Agent API — web-grounded research when vault is empty."""

from __future__ import annotations

import os
from typing import Any, Iterator

PERPLEXITY_AGENT_MODEL = os.environ.get(
    "PERPLEXITY_AGENT_MODEL",
    "perplexity/openai/gpt-5.6-sol",
)
PERPLEXITY_AGENT_PRESET = os.environ.get("PERPLEXITY_AGENT_PRESET", "low")


def perplexity_available() -> bool:
    return bool(os.environ.get("PERPLEXITY_API_KEY") or os.environ.get("PERPLEXITYAI_API_KEY"))


def _agent_tools() -> list[dict[str, str]]:
    return [
        {
            "type": "web_search",
            "search_context_size": PERPLEXITY_AGENT_PRESET,
        },
        {"type": "fetch_url"},
    ]


def stream_perplexity_agent(prompt: str) -> Iterator[str]:
    """Stream text deltas from Perplexity Agent API (web_search + fetch_url)."""
    if not perplexity_available():
        yield (
            "[Web research unavailable — set PERPLEXITY_API_KEY. "
            "Drafting from intent only; attach vault sources for grounding.]\n\n"
        )
        yield prompt[:1200]
        return

    try:
        from litellm import responses
    except ImportError as exc:
        yield f"[Perplexity Agent unavailable: {exc}]\n"
        return

    api_key = os.environ.get("PERPLEXITY_API_KEY") or os.environ.get("PERPLEXITYAI_API_KEY")
    try:
        stream = responses(
            model=PERPLEXITY_AGENT_MODEL,
            input=prompt,
            custom_llm_provider="perplexity",
            tools=_agent_tools(),
            instructions=(
                "Use web_search and fetch_url for live grounding. Cite URLs inline. "
                "Mark uncertain claims explicitly."
            ),
            max_output_tokens=1200,
            temperature=0.2,
            stream=True,
            api_key=api_key,
        )
        for event in stream:
            delta = _extract_delta(event)
            if delta:
                yield delta
    except Exception as exc:
        yield f"[Perplexity Agent error: {exc}]\n"


def complete_perplexity_agent(prompt: str) -> str:
    """Non-streaming Perplexity Agent completion."""
    parts = list(stream_perplexity_agent(prompt))
    return "".join(parts)


def web_sources_from_text(text: str) -> list[dict[str, Any]]:
    """Synthesize web source entries for lock pills from agent output."""
    import re

    urls = re.findall(r"https?://[^\s\)\]>]+", text or "")
    sources: list[dict[str, Any]] = []
    for i, url in enumerate(urls[:5], start=1):
        sources.append(
            {
                "id": f"web-{i}",
                "name": f"🌐 {url[:48]}",
                "excerpt": text[:2000],
                "web": True,
                "url": url,
            }
        )
    if not sources and text.strip():
        sources.append(
            {
                "id": "web-1",
                "name": "🌐 Web research",
                "excerpt": text[:4000],
                "web": True,
            }
        )
    return sources


def _extract_delta(event: Any) -> str:
    if isinstance(event, str):
        return event
    if isinstance(event, dict):
        for key in ("delta", "text", "content"):
            val = event.get(key)
            if val:
                return str(val)
        data = event.get("data")
        if isinstance(data, dict):
            for key in ("delta", "text", "content"):
                val = data.get(key)
                if val:
                    return str(val)
    delta = getattr(event, "delta", None)
    if delta:
        return str(delta)
    output = getattr(event, "output", None)
    if isinstance(output, list):
        chunks: list[str] = []
        for item in output:
            text = getattr(item, "content", None) or getattr(item, "text", None)
            if text:
                chunks.append(str(text))
        return "".join(chunks)
    return ""
