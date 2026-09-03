"""Pydantic schemas for config, user input, and optional structured AI replies."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TargetAI(str, Enum):
    claude = "claude"
    gemini = "gemini"
    deepseek = "deepseek"
    kimi = "kimi"
    ollama = "ollama"
    cursor = "cursor"


class Intent(str, Enum):
    research = "research"
    design = "design"
    comparison = "comparison"
    debug = "debug"
    analysis = "analysis"


class TargetConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    wrapper: Literal["xml", "markdown", "plain"]
    structure: str = Field(min_length=1, description="Jinja2 template for the prompt.")
    label: str | None = None
    model: str | None = Field(
        default=None,
        description="LiteLLM model id used when --direct is set.",
    )


class IntentConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: str = Field(min_length=1)
    output_format: str = Field(min_length=1)


class RuntimeSettings(BaseModel):
    """CLI / env defaults. Flags override. Do not put passwords here."""

    max_tokens: int = 4096
    timeout_seconds: int = 60
    store_prompts: bool = False
    auth_user: str = "admin"
    edition: str = "free"


class MatrixConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    targets: dict[str, TargetConfig]
    intents: dict[str, IntentConfig]
    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)

    @field_validator("targets", "intents")
    @classmethod
    def not_empty(cls, value: dict) -> dict:
        if not value:
            raise ValueError("must contain at least one entry")
        return {str(key).strip().lower(): item for key, item in value.items()}


class PromptRequest(BaseModel):
    """What the user asked the matrix to compile."""

    target_ai: str
    intent: str
    task: str = Field(min_length=1)
    context: str = ""

    @field_validator("target_ai", "intent")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if not cleaned:
            raise ValueError("must not be empty")
        return cleaned

    @field_validator("task")
    @classmethod
    def task_not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("task must not be empty")
        return cleaned


class RenderedPrompt(BaseModel):
    target_ai: str
    intent: str
    wrapper: str
    prompt: str
    context_injected: bool = False
    files_read: list[str] = Field(default_factory=list)
    class_id: str | None = None


class StructuredAIResponse(BaseModel):
    """Instructor/LiteLLM parse target when the user saves a live reply."""

    headline: str = Field(description="One-line summary of the answer.")
    answer: str = Field(description="Full answer in the requested output format.")
    key_points: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    follow_ups: list[str] = Field(default_factory=list)
    data_gaps: list[str] = Field(
        default_factory=list,
        description="Facts that could not be verified. Use 'Data not available' style notes.",
    )


class ExecutionResult(BaseModel):
    rendered: RenderedPrompt
    copied: bool = False
    direct: bool = False
    reply: str | None = None
    structured: StructuredAIResponse | None = None
    saved_to: str | None = None
    note: str | None = None
