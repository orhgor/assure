"""Heuristic intent picker for Compose. Fallback is research. No accuracy claims."""

from __future__ import annotations

INTENTS = ("research", "design", "comparison", "debug", "analysis")
FALLBACK = "research"

# Phrase / token lists. Longer phrases first in matching via word-ish tokens.
_KEYWORDS: dict[str, tuple[str, ...]] = {
    "comparison": (
        "versus",
        "compared to",
        "compare",
        "difference between",
        "differences",
        "better than",
        "trade-off",
        "tradeoff",
        " vs ",
        " vs.",
        " or ",
    ),
    "debug": (
        "traceback",
        "stack trace",
        "stacktrace",
        "exception",
        "typeerror",
        "nullpointer",
        "segfault",
        "bug",
        "error",
        "broken",
        "crash",
        "failing",
        "doesn't work",
        "does not work",
        "won't start",
        "fix this",
    ),
    "design": (
        "architecture",
        "wireframe",
        "mockup",
        "layout",
        "ux ",
        " ui",
        "user interface",
        "design a",
        "redesign",
        "prototype",
    ),
    "analysis": (
        "analyze",
        "analyse",
        "breakdown",
        "metrics",
        "trend",
        "dataset",
        "spreadsheet",
        "kpi",
        "cohort",
        "root cause",
    ),
    "research": (
        "literature",
        "paper",
        "study",
        "what is",
        "how does",
        "survey",
        "evidence",
        "cite",
    ),
}


def detect_intent(text: str | None) -> str:
    """Return a catalog intent id. Empty or unmatched text is research."""
    blob = " " + (text or "").lower().replace("\n", " ") + " "
    if not blob.strip():
        return FALLBACK
    scores = {name: 0 for name in INTENTS}
    for name, words in _KEYWORDS.items():
        for word in words:
            if word in blob:
                scores[name] += 1
    best = max(INTENTS, key=lambda name: (scores[name], 0 if name != FALLBACK else -1))
    if scores[best] == 0:
        return FALLBACK
    return best


def detect_intent_payload(text: str | None) -> dict[str, str]:
    intent = detect_intent(text)
    return {"intent": intent, "fallback": FALLBACK}
