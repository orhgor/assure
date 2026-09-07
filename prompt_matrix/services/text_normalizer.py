"""Normalize raw document text for lexical and semantic verification passes."""

from __future__ import annotations

import re
import unicodedata


def normalize_text(raw_content: str) -> str:
    """
    Normalizes raw document text or file payload streams into a clean UTF-8 text stream
    optimized for deterministic lexical and semantic verification passes.
    Strips binary artifacts, unrendered layout markers, and unifies whitespace.
    """
    if not raw_content:
        return ""

    text = unicodedata.normalize("NFC", raw_content)

    text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C" or ch in ("\n", "\t"))

    text = re.sub(r"\b(Page\s+\d+|Confidential|Draft)\b", "", text, flags=re.IGNORECASE)

    text = re.sub(r"\n\s*\n", "\n\n", text)

    text = re.sub(r"[ \t]+", " ", text)

    return text.strip()
