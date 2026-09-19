"""Six-layer token control, asymmetric model routing, and project budgets."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

import tiktoken

try:
    from .history import get_db
except ImportError:
    from history import get_db

try:
    from .token_counter import count_tokens
except ImportError:
    from token_counter import count_tokens


def _litellm_api_kwargs(model: str) -> dict:
    """Return {api_key: ...} for the model's provider, reading BYOK from Flask g if available."""
    prefix = model.split("/")[0] if "/" in model else model
    provider = {
        "anthropic": "claude",
        "deepseek": "deepseek",
        "gemini": "gemini",
        "groq": "groq",
        "openrouter": "openrouter",
    }.get(prefix, prefix)
    try:
        try:
            from .keys import litellm_kwargs_for
        except ImportError:
            from keys import litellm_kwargs_for
        return litellm_kwargs_for(provider)
    except Exception:
        return {}


DEFAULT_LITELLM_TIMEOUT_SECONDS = 60
MIN_LITELLM_TIMEOUT_SECONDS = 10
MAX_LITELLM_TIMEOUT_SECONDS = 80


def _resolve_litellm_timeout() -> int:
    """Clamp PEM_TIMEOUT_SECONDS so a stalled provider cannot silence the caller.

    The web inquire stream keeps the connection open with SSE keepalives and only
    survives ~100s at the edge; an unbounded completion would outlive it.
    """
    raw = (os.environ.get("PEM_TIMEOUT_SECONDS") or "").strip()
    try:
        seconds = int(float(raw)) if raw else DEFAULT_LITELLM_TIMEOUT_SECONDS
    except ValueError:
        seconds = DEFAULT_LITELLM_TIMEOUT_SECONDS
    return max(MIN_LITELLM_TIMEOUT_SECONDS, min(MAX_LITELLM_TIMEOUT_SECONDS, seconds))


class TaskType(str, Enum):
    SURGICAL_EDIT = "surgical_edit"
    SUMMARIZE_NODE = "summarize_node"
    DRAFT_COMPILE = "draft_compile"
    SEMANTIC_VALIDATION = "semantic_validation"
    DEEP_SYNTHESIS = "deep_synthesis"
    MACRO_AUDIT = "macro_audit"
    REDHAT = "redhat"


# Hard per-request input caps (tokens). Independent of remaining project budget.
MAX_INPUT_TOKENS: dict[TaskType, int] = {
    TaskType.SURGICAL_EDIT: 2000,
    TaskType.SUMMARIZE_NODE: 2000,
    TaskType.SEMANTIC_VALIDATION: 4000,
    TaskType.DRAFT_COMPILE: 100_000,
    TaskType.REDHAT: 8000,
    TaskType.DEEP_SYNTHESIS: 100_000,
    TaskType.MACRO_AUDIT: 100_000,
}


@dataclass(frozen=True)
class ModelPolicy:
    model_id: str
    max_input_tokens: int
    max_output_tokens: int
    caching: bool
    litellm_model: str = ""


TASK_POLICIES: dict[TaskType, ModelPolicy] = {
    # Surgical edits → DeepSeek-V3 (cheap, fast, strict JSON)
    TaskType.SURGICAL_EDIT: ModelPolicy(
        model_id="deepseek/deepseek-chat",
        max_input_tokens=MAX_INPUT_TOKENS[TaskType.SURGICAL_EDIT],
        max_output_tokens=500,
        caching=False,
        litellm_model="deepseek/deepseek-chat",
    ),
    # Quick draft / node summarize → DeepSeek-V3
    TaskType.SUMMARIZE_NODE: ModelPolicy(
        model_id="deepseek/deepseek-chat",
        max_input_tokens=MAX_INPUT_TOKENS[TaskType.SUMMARIZE_NODE],
        max_output_tokens=500,
        caching=False,
        litellm_model="deepseek/deepseek-chat",
    ),
    # Claim entailment (source quote → paragraph claim) → Qwen3-Next-80B-A3B
    # Instruct (non-reasoning). Was GLM 5.3 Flash via OpenRouter (:floor =
    # cheapest provider), whose hidden reasoning spent the whole 1024-token output
    # budget before the verdict line, so the gate read "unverified" on anchored
    # paragraphs. Cap unchanged: a non-reasoning model emits the verdict without
    # burning output budget on reasoning first.
    TaskType.SEMANTIC_VALIDATION: ModelPolicy(
        model_id="qwen/qwen3-next-80b-a3b-instruct",
        max_input_tokens=MAX_INPUT_TOKENS[TaskType.SEMANTIC_VALIDATION],
        max_output_tokens=1024,
        caching=False,
        litellm_model="openrouter/qwen/qwen3-next-80b-a3b-instruct",
    ),
    # Compile draft → Qwen3-Next-80B-A3B Instruct (non-reasoning), same budget as
    # DEEP_SYNTHESIS (2048 output / 30000 input). The compile used DEEP_SYNTHESIS —
    # GLM 5.3 Flash :floor — whose hidden reasoning consumed the 2048-token output
    # budget and truncated the draft. DEEP_SYNTHESIS is deliberately untouched:
    # the Ask stream shares it and must keep its model.
    TaskType.DRAFT_COMPILE: ModelPolicy(
        model_id="qwen/qwen3-next-80b-a3b-instruct",
        max_input_tokens=MAX_INPUT_TOKENS[TaskType.DRAFT_COMPILE],
        max_output_tokens=2048,
        caching=False,
        litellm_model="openrouter/qwen/qwen3-next-80b-a3b-instruct",
    ),
    # Deep synthesis → GLM 5.3 Flash via OpenRouter (:floor = cheapest provider)
    TaskType.DEEP_SYNTHESIS: ModelPolicy(
        model_id="z-ai/glm-5.3-flash",
        max_input_tokens=MAX_INPUT_TOKENS[TaskType.DEEP_SYNTHESIS],
        max_output_tokens=2048,
        caching=False,
        litellm_model="openrouter/z-ai/glm-5.3-flash:floor",
    ),
    TaskType.MACRO_AUDIT: ModelPolicy(
        model_id="z-ai/glm-5.3-flash",
        max_input_tokens=MAX_INPUT_TOKENS[TaskType.MACRO_AUDIT],
        max_output_tokens=2048,
        caching=False,
        litellm_model="openrouter/z-ai/glm-5.3-flash:floor",
    ),
    # Red-Hat adversary → DeepSeek-R1 (CoT reasoning; headroom for hidden reasoning tokens)
    TaskType.REDHAT: ModelPolicy(
        model_id="deepseek/deepseek-reasoner",
        max_input_tokens=MAX_INPUT_TOKENS[TaskType.REDHAT],
        max_output_tokens=8192,
        caching=False,
        litellm_model="deepseek/deepseek-reasoner",
    ),
}

DEFAULT_TOKEN_LIMIT = 250_000
MAX_RETRIES = 1  # strict: 2 passes total (initial + 1 retry)


class BudgetExhaustedError(Exception):
    """Raised when project token budget cannot cover the estimated request."""


QuotaExceededError = BudgetExhaustedError


class TokenLimitExceededError(Exception):
    """Raised when a hard per-task input cap would be exceeded."""


class TokenAccountant:
    """Precise token counting via tiktoken cl100k_base."""

    def __init__(self, encoding_name: str = "cl100k_base") -> None:
        self._encoding = tiktoken.get_encoding(encoding_name)

    def count(self, text: str) -> int:
        return len(self._encoding.encode(text or ""))

    def count_messages(self, messages: list[dict[str, Any]]) -> int:
        total = 0
        for msg in messages or []:
            content = msg.get("content")
            if isinstance(content, str):
                total += self.count(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        total += self.count(str(block.get("text") or block.get("content") or ""))
        return total


def inject_bedrock_cache_control(
    messages: list[dict[str, Any]],
    static_block_indices: list[int] | None = None,
) -> list[dict[str, Any]]:
    """Mark static substrate blocks with Bedrock ephemeral prompt caching."""
    patched: list[dict[str, Any]] = []
    indices = set(static_block_indices or [0])
    for i, msg in enumerate(messages):
        item = dict(msg)
        if i in indices:
            content = item.get("content")
            if isinstance(content, str):
                item["content"] = [
                    {
                        "type": "text",
                        "text": content,
                        "cache_control": {"type": "ephemeral"},
                    }
                ]
            elif isinstance(content, list):
                blocks = []
                for block in content:
                    b = (
                        dict(block)
                        if isinstance(block, dict)
                        else {"type": "text", "text": str(block)}
                    )
                    b["cache_control"] = {"type": "ephemeral"}
                    blocks.append(b)
                item["content"] = blocks
        patched.append(item)
    return patched


class ProjectBudgetStore:
    """SQLite-backed project budgets on history.sqlite (no external DB)."""

    def __init__(self, conn: sqlite3.Connection | None = None) -> None:
        self._conn = conn

    def _connection(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        return get_db()

    def ensure_tables(self) -> None:
        try:
            from .db.connection import init_db
        except ImportError:
            from db.connection import init_db
        init_db(self._connection())

    def ensure_project(self, project_id: str, token_limit: int = DEFAULT_TOKEN_LIMIT) -> None:
        self.ensure_tables()
        db = self._connection()
        db.execute(
            """
            INSERT INTO project_budgets (project_id, token_limit, tokens_used)
            VALUES (?, ?, 0)
            ON CONFLICT(project_id) DO NOTHING
            """,
            (project_id, token_limit),
        )
        db.commit()

    def get_usage(self, project_id: str) -> tuple[int, int]:
        self.ensure_project(project_id)
        db = self._connection()
        row = db.execute(
            "SELECT token_limit, tokens_used FROM project_budgets WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        if not row:
            return DEFAULT_TOKEN_LIMIT, 0
        return int(row[0]), int(row[1])

    def check_budget_available(self, project_id: str, estimated_in: int) -> None:
        limit, used = self.get_usage(project_id)
        if used + max(0, estimated_in) > limit:
            raise BudgetExhaustedError(
                f"Project {project_id} budget exhausted: {used}+{estimated_in} > {limit}"
            )

    def record_usage(
        self,
        project_id: str,
        *,
        task_type: str,
        model_id: str,
        input_tokens: int,
        output_tokens: int,
        meta: dict[str, Any] | None = None,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> None:
        self.ensure_project(project_id)
        db = self._connection()
        total = max(0, input_tokens) + max(0, output_tokens)
        db.execute(
            """
            UPDATE project_budgets
            SET tokens_used = tokens_used + ?, updated_at = datetime('now')
            WHERE project_id = ?
            """,
            (total, project_id),
        )
        cols = {row[1] for row in db.execute("PRAGMA table_info(token_ledger_entries)")}
        if "cache_read_tokens" in cols:
            db.execute(
                """
                INSERT INTO token_ledger_entries
                (project_id, task_type, model_id, input_tokens, output_tokens,
                 cache_read_tokens, cache_write_tokens, meta)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    task_type,
                    model_id,
                    input_tokens,
                    output_tokens,
                    cache_read_tokens,
                    cache_write_tokens,
                    json.dumps(meta or {}),
                ),
            )
        else:
            db.execute(
                """
                INSERT INTO token_ledger_entries
                (project_id, task_type, model_id, input_tokens, output_tokens, meta)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    task_type,
                    model_id,
                    input_tokens,
                    output_tokens,
                    json.dumps(meta or {}),
                ),
            )
        db.commit()

    def load_document(self, project_id: str) -> dict[str, Any] | None:
        try:
            from .db.jdf_repository import fetch_latest_jdf
        except ImportError:
            from db.jdf_repository import fetch_latest_jdf
        return fetch_latest_jdf(project_id)

    def save_document(self, project_id: str, document_id: str, tree: dict[str, Any]) -> None:
        try:
            from .db.jdf_repository import save_jdf_revision
        except ImportError:
            from db.jdf_repository import save_jdf_revision
        save_jdf_revision(
            project_id,
            tree,
            mutation_type="legacy_save",
            change_summary="ProjectBudgetStore.save_document",
        )


@dataclass
class ExecutionResult:
    ok: bool
    text: str = ""
    node: dict[str, Any] | None = None
    status: str = "ok"
    error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    retries: int = 0
    model_id: str = ""


@dataclass
class CostGovernor:
    budget_store: ProjectBudgetStore = field(default_factory=ProjectBudgetStore)
    accountant: TokenAccountant = field(default_factory=TokenAccountant)
    executor: Callable[..., tuple[str, int, int]] | None = None

    def policy_for(self, task_type: TaskType) -> ModelPolicy:
        return TASK_POLICIES[task_type]

    def get_remaining_budget(self, project_id: str) -> int:
        limit, used = self.budget_store.get_usage(project_id)
        return max(0, limit - used)

    def preflight(
        self, project_id: str, task_type: TaskType, messages: list[dict[str, Any]]
    ) -> ModelPolicy:
        policy = self.policy_for(task_type)
        estimated_in = self.accountant.count_messages(messages)
        hard_cap = MAX_INPUT_TOKENS.get(task_type, 30_000)
        if estimated_in > hard_cap:
            raise TokenLimitExceededError(
                f"Request exceeds hard input cap of {hard_cap} tokens for {task_type.value} "
                f"(got {estimated_in}). Please shorten your context."
            )
        remaining = self.get_remaining_budget(project_id)
        if estimated_in > remaining:
            raise BudgetExhaustedError(
                f"Insufficient project budget. {estimated_in} tokens needed, {remaining} remaining."
            )
        self.budget_store.check_budget_available(project_id, estimated_in)
        return policy

    enforce_preflight = preflight

    def record_usage(
        self,
        project_id: str,
        *,
        input_tokens: int,
        output_tokens: int,
        model_id: str | None = None,
        model: str | None = None,
        task_type: TaskType | str,
        meta: dict[str, Any] | None = None,
    ) -> None:
        """Record LLM token usage against the project budget (SQLite ledger)."""
        resolved_model = (model_id or model or "").strip()
        if not resolved_model:
            raise ValueError("model_id or model required")
        task = task_type.value if isinstance(task_type, TaskType) else str(task_type)
        self.budget_store.record_usage(
            project_id,
            task_type=task,
            model_id=resolved_model,
            input_tokens=max(0, int(input_tokens)),
            output_tokens=max(0, int(output_tokens)),
            meta=meta,
        )

    def _default_executor(
        self,
        model: str,
        messages: list[dict[str, Any]],
        max_output: int,
        use_cache: bool,
    ) -> tuple[str, int, int]:
        try:
            from .services.language_guard import (
                ensure_response_language,
                guard_messages,
                resolve_request_locale,
            )
        except ImportError:
            from services.language_guard import (
                ensure_response_language,
                guard_messages,
                resolve_request_locale,
            )

        locale = resolve_request_locale()
        payload = guard_messages(messages, locale=locale)
        if use_cache:
            payload = inject_bedrock_cache_control(payload)

        # Prefer Bedrock Converse stream when boto3 credentials exist.
        try:
            import boto3

            if model.startswith("bedrock/") or model.startswith("anthropic."):
                bedrock_model = model.split("/", 1)[-1]
                client = boto3.client("bedrock-runtime")
                converse_messages = []
                for msg in payload:
                    role = msg.get("role", "user")
                    content = msg.get("content")
                    if isinstance(content, list):
                        blocks = []
                        for block in content:
                            blocks.append(
                                {"text": str(block.get("text") or block.get("content") or "")}
                            )
                        converse_messages.append({"role": role, "content": blocks})
                    else:
                        converse_messages.append(
                            {"role": role, "content": [{"text": str(content or "")}]}
                        )
                resp = client.converse(
                    modelId=bedrock_model,
                    messages=converse_messages,
                    inferenceConfig={"maxTokens": max_output},
                )
                out = resp.get("output", {}).get("message", {}).get("content", [])
                text = "".join(part.get("text", "") for part in out if isinstance(part, dict))
                usage = resp.get("usage") or {}
                text = ensure_response_language(text, locale)
                return (
                    text,
                    int(usage.get("inputTokens") or self.accountant.count_messages(messages)),
                    int(usage.get("outputTokens") or self.accountant.count(text)),
                )
        except Exception:
            pass

        try:
            import litellm

            _api_kwargs = _litellm_api_kwargs(model)
            _timeout = _resolve_litellm_timeout()

            def _complete():
                return litellm.completion(
                    model=model if not model.startswith("anthropic.") else f"bedrock/{model}",
                    messages=payload,
                    max_tokens=max_output,
                    stream=False,
                    timeout=_timeout,
                    **_api_kwargs,
                )

            try:
                from .litellm_runner import call_with_retry
            except ImportError:
                from litellm_runner import call_with_retry

            resp = call_with_retry(_complete)
            try:
                from .services.model_utils import extract_litellm_response_text
            except ImportError:
                from services.model_utils import extract_litellm_response_text

            text = ensure_response_language(extract_litellm_response_text(resp), locale)
            usage = getattr(resp, "usage", None)
            in_tok = int(
                getattr(usage, "prompt_tokens", 0) or self.accountant.count_messages(messages)
            )
            out_tok = int(getattr(usage, "completion_tokens", 0) or self.accountant.count(text))
            return text, in_tok, out_tok
        except Exception as exc:
            return f"ERROR: {exc}", self.accountant.count_messages(messages), 0

    def execute_with_retry_budget(
        self,
        project_id: str,
        task_type: TaskType,
        messages: list[dict[str, Any]],
        *,
        validate_fn: Callable[[str], tuple[bool, str | None]] | None = None,
        build_node_fn: Callable[[str], dict[str, Any]] | None = None,
        defer_budget_record: bool = False,
    ) -> ExecutionResult:
        """Run up to two passes (initial + one retry). No further re-prompting."""
        run = self.executor or self._default_executor
        policy = self.preflight(project_id, task_type, messages)
        model = policy.litellm_model or policy.model_id
        last_error: str | None = None
        total_in = 0
        total_out = 0

        for attempt in range(MAX_RETRIES + 1):
            text, in_tok, out_tok = run(model, messages, policy.max_output_tokens, policy.caching)
            total_in += in_tok
            total_out += out_tok
            if not defer_budget_record:
                self.budget_store.record_usage(
                    project_id,
                    task_type=task_type.value,
                    model_id=policy.model_id,
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    meta={"attempt": attempt},
                )
            if validate_fn is not None:
                ok, err = validate_fn(text)
                if not ok:
                    last_error = err or "validation failed"
                    if attempt < MAX_RETRIES:
                        continue
                    node = build_node_fn(text) if build_node_fn else None
                    if isinstance(node, dict):
                        node = {
                            **node,
                            "status": "VALIDATION_FAILED",
                            "meta": {**(node.get("meta") or {}), "z3_error": last_error},
                        }
                    return ExecutionResult(
                        ok=False,
                        text=text,
                        node=node,
                        status="VALIDATION_FAILED",
                        error=last_error,
                        input_tokens=total_in,
                        output_tokens=total_out,
                        retries=attempt,
                        model_id=policy.model_id,
                    )
            node = build_node_fn(text) if build_node_fn else None
            return ExecutionResult(
                ok=True,
                text=text,
                node=node,
                status="ok",
                input_tokens=total_in,
                output_tokens=total_out,
                retries=attempt,
                model_id=policy.model_id,
            )

        return ExecutionResult(
            ok=False,
            status="VALIDATION_FAILED",
            error=last_error or "unknown",
            input_tokens=total_in,
            output_tokens=total_out,
            model_id=policy.model_id,
        )
