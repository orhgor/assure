"""Universal Prompt Engineering Matrix: vague task in, target-tuned prompt out."""

from __future__ import annotations

__version__ = "0.1.0"

from .engine import execute, load_matrix, render_prompt
from .models import ExecutionResult, PromptRequest, RenderedPrompt, StructuredAIResponse

__all__ = [
    "ExecutionResult",
    "PromptRequest",
    "RenderedPrompt",
    "StructuredAIResponse",
    "execute",
    "load_matrix",
    "render_prompt",
    "__version__",
]
