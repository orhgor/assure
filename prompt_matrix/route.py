"""Live send path: local runners first (Ollama if we can start it), then cloud APIs."""

from __future__ import annotations

from typing import Any

try:
    from .engine import MatrixError
    from .keys import key_present, load_keys
    from .local_runners import (
        alive_runners,
        ensure_ollama,
        local_is_up,
        ollama_bin,
        ollama_up,
        pick_local,
    )
except ImportError:
    from engine import MatrixError
    from keys import key_present, load_keys
    from local_runners import (
        alive_runners,
        ensure_ollama,
        local_is_up,
        ollama_bin,
        ollama_up,
        pick_local,
    )

# "ollama" here is the PEM local target id: Ollama, or vLLM/SGLang/Llamafile/LM Studio if up.
SEND_ORDER = ("ollama", "gemini", "claude", "kimi")


def can_send(name: str) -> bool:
    if not name or name == "cursor":
        return False
    if name == "ollama":
        return local_is_up(wake_ollama=False)
    return key_present(name)


def live_targets(*, wake_ollama: bool = True) -> list[str]:
    load_keys()
    if wake_ollama:
        ensure_ollama()
        alive_runners(refresh=True)
    return [name for name in SEND_ORDER if can_send(name)]


def pick_pair(
    prefer_creator: str | None,
    prefer_critic: str | None,
) -> tuple[str, str, str | None]:
    live = live_targets()
    if not live:
        raise MatrixError(
            "Nothing is reachable. Paste a Gemini, DeepSeek, Claude, or Kimi key, "
            "install Ollama so PEM can start it, or run vLLM / SGLang / Llamafile / LM Studio locally."
        )
    prefer_creator = (prefer_creator or "").strip().lower() or None
    prefer_critic = (prefer_critic or "").strip().lower() or None
    creator = prefer_creator if prefer_creator in live else live[0]
    if prefer_critic in live and prefer_critic != creator:
        critic = prefer_critic
    elif prefer_critic == creator == "ollama":
        critic = creator
    else:
        rest = [name for name in live if name != creator]
        critic = rest[0] if rest else creator
    bits: list[str] = []
    if prefer_creator and prefer_creator not in live:
        bits.append(f"{prefer_creator} was down, used {creator}")
    if prefer_critic and critic != prefer_critic:
        bits.append(f"critic {prefer_critic} was down, used {critic}")
    if creator == critic:
        bits.append(f"only {creator} is live, so it did attempt and critique")
    local = pick_local()
    if creator == "ollama" and local and local["id"] != "ollama":
        bits.append(f"local runner is {local['label']} ({local['fit']})")
    note = ("; ".join(bits) + ".") if bits else None
    return creator, critic, note


def next_after(current: str, tried: set[str] | None = None) -> str | None:
    skip = set(tried or ())
    skip.add(current)
    for name in live_targets(wake_ollama=False):
        if name not in skip:
            return name
    return None


def route_snapshot() -> dict[str, Any]:
    load_keys()
    binary = ollama_bin()
    up = ollama_up()
    local = pick_local(wake_ollama=False)
    locals_up = [
        {"id": row["id"], "label": row["label"], "fit": row["fit"], "model": row.get("model")}
        for row in alive_runners()
    ]
    live = [name for name in SEND_ORDER if can_send(name)]
    if local:
        local_note = (
            f"Closed to the internet: {local['label']} ({local['fit']}). "
            "The model files are separate from the runner."
        )
    elif binary:
        local_note = "Ollama is installed but not running. PEM will start it on Send. vLLM/SGLang/Llamafile are used only if already up."
    else:
        local_note = (
            "Ollama is not installed (best for personal local loops, not for production serving). "
            "PEM uses vLLM, SGLang, Llamafile, or LM Studio if those servers are already up; otherwise cloud."
        )
    chain = " → ".join(live) if live else "(none)"
    return {
        "ollama_binary": binary,
        "ollama_up": up,
        "local": local,
        "local_runners": locals_up,
        "send_order": live,
        "chain": chain,
        "note": f"{local_note} Live route: {chain}.",
        "host": (local or {}).get("api_base"),
    }
