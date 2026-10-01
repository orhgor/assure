"""Six-layer token control, asymmetric model routing, and project budgets."""

from __future__ import annotations

import json
import os
import sqlite3
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

import tiktoken

try:
    from .litellm_runner import CompletionMeta, completion_meta
except ImportError:
    from litellm_runner import CompletionMeta, completion_meta

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
        "gemini": "gemini",
        "groq": "groq",
    }.get(prefix, prefix)
    try:
        try:
            from .keys import litellm_kwargs_for
        except ImportError:
            from keys import litellm_kwargs_for
        return litellm_kwargs_for(provider)
    except Exception:
        return {}


def _model_calls():
    """``services.model_calls`` (lazy: it imports the db layer; this module is
    imported by everything, so the ledger is fetched at call time)."""
    try:
        from .services import model_calls
    except ImportError:  # pragma: no cover - flat-import fallback
        from services import model_calls  # type: ignore
    return model_calls


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
    FIELD_EXTRACTION = "field_extraction"


# Hard per-request input caps (tokens). Independent of remaining project budget.
MAX_INPUT_TOKENS: dict[TaskType, int] = {
    TaskType.SURGICAL_EDIT: 2000,
    TaskType.SUMMARIZE_NODE: 2000,
    TaskType.SEMANTIC_VALIDATION: 4000,
    TaskType.DRAFT_COMPILE: 100_000,
    TaskType.REDHAT: 8000,
    # Parsure grounded field extraction (services/llm_extraction): one call
    # per document holds the page texts of an ICP document (the golden set is
    # 150–450 tokens a page); anything longer is chunked by page against this cap.
    TaskType.FIELD_EXTRACTION: 6000,
    # Only DRAFT_COMPILE was raised (it must hold two numbered policies).
    # These two were raised alongside it on the reasoning that they carried the
    # same 30,000, which widened the input ceiling on two task types nobody
    # asked to change and moved the per-compile cost without anyone measuring
    # it. They go back.
    TaskType.DEEP_SYNTHESIS: 30000,
    TaskType.MACRO_AUDIT: 30000,
}


@dataclass(frozen=True)
class ModelPolicy:
    model_id: str
    max_input_tokens: int
    max_output_tokens: int
    caching: bool
    litellm_model: str = ""


# Every task runs on Claude Sonnet 5.5 through Amazon Bedrock (user decision
# 2026-10-01: "diğer prompt atılan yerlerde Sonnet'in son modelini kullan ki
# daha ucuz olsun"; OpenRouter removed). The document read itself is the one
# Opus 5.5 call (services/llm_parse, BEDROCK_MODEL_PARSE) and is not a policy.
# Ids are bare here and qualified with the region's inference-profile prefix
# by _apply_llm_backend; `us.anthropic.claude-sonnet-5-5` answered a Converse
# call in us-east-1 on 2026-10-01. Sonnet 5.5 and Opus 5.5 reject `temperature`
# ("deprecated for this model", 2026-10-01) — litellm_runner sets
# litellm.drop_params so the call sites that still pass it keep working.
_SONNET = "bedrock/anthropic.claude-sonnet-5-5"


def _policy(task: TaskType, max_output: int) -> ModelPolicy:
    return ModelPolicy(
        model_id=_SONNET,
        max_input_tokens=MAX_INPUT_TOKENS[task],
        max_output_tokens=max_output,
        caching=False,
        litellm_model=_SONNET,
    )


TASK_POLICIES: dict[TaskType, ModelPolicy] = {
    TaskType.SURGICAL_EDIT: _policy(TaskType.SURGICAL_EDIT, 500),
    TaskType.SUMMARIZE_NODE: _policy(TaskType.SUMMARIZE_NODE, 500),
    # Output caps are for the answer; the earlier reasoning-model lesson
    # (hidden reasoning spending the cap, ledger ids 2361/2362) still holds,
    # and Sonnet 5.5 without extended thinking answers within them.
    TaskType.SEMANTIC_VALIDATION: _policy(TaskType.SEMANTIC_VALIDATION, 1024),
    TaskType.DRAFT_COMPILE: _policy(TaskType.DRAFT_COMPILE, 2048),
    TaskType.DEEP_SYNTHESIS: _policy(TaskType.DEEP_SYNTHESIS, 2048),
    TaskType.MACRO_AUDIT: _policy(TaskType.MACRO_AUDIT, 2048),
    TaskType.REDHAT: _policy(TaskType.REDHAT, 8192),
    # Parsure grounded field extraction: one strict JSON object; every value
    # is re-found verbatim in the page text before it is used.
    TaskType.FIELD_EXTRACTION: _policy(TaskType.FIELD_EXTRACTION, 2048),
}

def llm_backend() -> str:
    """``ASSURE_LLM_BACKEND``: ``"bedrock"`` (default — Amazon Bedrock, Claude
    Sonnet 5.5 for every prompt, Opus 5.5 for the document read) or
    ``"ollama"`` (every task on the local Ollama container, set explicitly).
    OpenRouter was removed on 2026-10-01 (user decision); a leftover
    ``openrouter`` / ``cloud`` value reads as Bedrock rather than as a backend
    nothing can serve."""
    explicit = os.environ.get("ASSURE_LLM_BACKEND", "").strip().lower()
    if explicit == "ollama":
        return "ollama"
    return "bedrock"


# Bedrock defaults (user decision 2026-10-01): the document is read by Claude
# Opus 5.5 (services/llm_parse — handwriting, ticked boxes, stamps), every
# other prompt — compile, entailment, Red-Hat, field extraction, surgical edit,
# Compare — is Claude Sonnet 5.5, the cheaper model. Both ids were listed by
# `bedrock list-foundation-models` in us-east-1 and answered through the `us.`
# inference profile on 2026-10-01. Override per role in .env:
#   ASSURE_BEDROCK_MODEL_PARSE, ASSURE_BEDROCK_MODEL_DRAFT,
#   ASSURE_BEDROCK_MODEL_ANALYSIS, ASSURE_BEDROCK_MODEL_B
# (ASSURE_BEDROCK_MODEL alone sets draft and analysis).
BEDROCK_MODEL_PARSE = "anthropic.claude-opus-5-5"
BEDROCK_MODEL_DRAFT = "anthropic.claude-sonnet-5-5"
BEDROCK_MODEL_ANALYSIS = "anthropic.claude-sonnet-5-5"
BEDROCK_MODEL_A = BEDROCK_MODEL_DRAFT
BEDROCK_MODEL_B = "anthropic.claude-sonnet-5-5"  # Compare's second column

#: Which task class each policy belongs to on the Bedrock backend.
BEDROCK_ANALYSIS_TASKS = frozenset(
    {TaskType.SEMANTIC_VALIDATION, TaskType.MACRO_AUDIT, TaskType.REDHAT, TaskType.FIELD_EXTRACTION}
)
_GEO_PREFIXES = ("us.", "eu.", "apac.", "global.")


def bedrock_region() -> str:
    """The Bedrock region: ``ASSURE_BEDROCK_REGION``, else the AWS region.
    Optional and separate from S3's region (2026-10-01): the models are
    invoked through the ``us.`` profiles in us-east-1 whatever region the
    bucket is in; unset, the AWS region serves both."""
    return (os.environ.get("ASSURE_BEDROCK_REGION") or os.environ.get("AWS_DEFAULT_REGION")
            or os.environ.get("AWS_REGION") or "us-east-1").strip().lower()


def bedrock_api_key() -> str:
    """The Bedrock API key (bearer token), or "".

    User decision 2026-10-01: Bedrock is reached with an API key only, no IAM
    identity. ``AWS_BEARER_TOKEN_BEDROCK`` is the name boto3/botocore and
    litellm read themselves; ``ASSURE_BEDROCK_API_KEY`` is accepted as an
    alias and copied onto it so both clients see the same token. Without a
    key the boto3 credential chain (keys, role) signs as before."""
    token = (os.environ.get("AWS_BEARER_TOKEN_BEDROCK") or os.environ.get("ASSURE_BEDROCK_API_KEY") or "").strip()
    if token and not os.environ.get("AWS_BEARER_TOKEN_BEDROCK"):
        os.environ["AWS_BEARER_TOKEN_BEDROCK"] = token
    return token


def _bedrock_geo_prefix() -> str:
    """Cross-region inference profile prefix for the configured region:
    ``us.`` / ``eu.`` / ``apac.`` — the ids Bedrock serves the Anthropic models
    under (a bare model id is not invocable on-demand in most regions)."""
    region = bedrock_region()
    if region.startswith("eu-"):
        return "eu."
    if region.startswith("ap-"):
        return "apac."
    return "us."


def _bedrock_qualify(raw: str) -> str:
    """``bedrock/<geo>.<id>``: a bare ``anthropic.…`` id gets the region's
    cross-region inference-profile prefix (bare ids are not invocable
    on-demand in most regions); ids that already carry ``eu.``/``us.``/
    ``apac.``/``global.``, an ARN, or the ``bedrock/`` prefix pass through."""
    raw = raw.strip()
    if raw.startswith("bedrock/"):
        raw = raw[len("bedrock/"):]
    if raw.startswith("anthropic."):
        raw = _bedrock_geo_prefix() + raw
    return f"bedrock/{raw}"


def bedrock_model(role: str = "a") -> str:
    """Bedrock model id per role. ``"a"``/``"draft"`` → ``ASSURE_BEDROCK_MODEL_DRAFT``
    (drafting: compile, deep synthesis, summarise, surgical edit);
    ``"analysis"`` → ``ASSURE_BEDROCK_MODEL_ANALYSIS`` (entailment, Red-Hat,
    macro audit, field extraction, lock inference); ``"b"`` →
    ``ASSURE_BEDROCK_MODEL_B`` (Compare's second column). ``ASSURE_BEDROCK_MODEL``
    is the shared fallback for draft and analysis; ``"parse"`` →
    ``ASSURE_BEDROCK_MODEL_PARSE`` (the document read). Defaults: Sonnet 5.5
    everywhere, Opus 5.5 for the parse, through the region's inference profile."""
    shared = os.environ.get("ASSURE_BEDROCK_MODEL", "").strip()
    if role in ("anchor", "evidence", "redhat"):
        role = "analysis"
    elif role == "edit":
        role = "draft"
    if role == "parse":
        raw = os.environ.get("ASSURE_BEDROCK_MODEL_PARSE", "").strip() or BEDROCK_MODEL_PARSE
    elif role == "b":
        raw = os.environ.get("ASSURE_BEDROCK_MODEL_B", "").strip() or BEDROCK_MODEL_B
    elif role == "analysis":
        raw = os.environ.get("ASSURE_BEDROCK_MODEL_ANALYSIS", "").strip() or shared or BEDROCK_MODEL_ANALYSIS
    else:
        raw = os.environ.get("ASSURE_BEDROCK_MODEL_DRAFT", "").strip() or shared or BEDROCK_MODEL_DRAFT
    return _bedrock_qualify(raw)


#: The five stages a deployment picks an open model for (user's table,
#: 2026-09-25: Parsing / Prompt Compile / Red-Hat / Evidence / Compare), the
#: .env variable of each, and which policies belong to it. ``ASSURE_OLLAMA_MODEL``
#: is the fallback for every stage; ``ASSURE_OLLAMA_MODEL_B`` is the old name of
#: the Compare column and still works.
OLLAMA_STAGE_ENV = {
    "parse": "ASSURE_OLLAMA_MODEL_PARSE",        # field extraction from the parsed text
    "draft": "ASSURE_OLLAMA_MODEL_DRAFT",        # compile, deep synthesis, summarise, surgical edit
    "redhat": "ASSURE_OLLAMA_MODEL_REDHAT",      # Red-Hat critique, macro audit
    "evidence": "ASSURE_OLLAMA_MODEL_EVIDENCE",  # entailment (claims validation), lock inference
    "compare": "ASSURE_OLLAMA_MODEL_COMPARE",    # Compare's second column
}
OLLAMA_TASK_STAGE = {
    TaskType.FIELD_EXTRACTION: "parse",
    TaskType.DRAFT_COMPILE: "draft",
    TaskType.DEEP_SYNTHESIS: "draft",
    TaskType.SUMMARIZE_NODE: "draft",
    TaskType.SURGICAL_EDIT: "draft",
    TaskType.REDHAT: "redhat",
    TaskType.MACRO_AUDIT: "redhat",
    TaskType.SEMANTIC_VALIDATION: "evidence",
}
#: CPU defaults (a laptop or a Graviton box without a GPU). The GPU overlay
#: (docker-compose.gpu.yml) and gen-env's GPU tiers set larger ones per stage.
OLLAMA_DEFAULT_MODEL = "qwen2.5:1.5b"
OLLAMA_DEFAULT_COMPARE = "llama3.2:1b"
_OLLAMA_ROLE_ALIASES = {"a": "draft", "b": "compare", "analysis": "evidence", "anchor": "evidence", "edit": "draft"}


def local_model(role: str = "a") -> str:
    """Ollama model for one stage of the local backend.

    ``role`` is a stage name (``parse`` / ``draft`` / ``redhat`` / ``evidence`` /
    ``compare``) or one of the older aliases ``a`` (draft), ``b`` (compare),
    ``analysis`` (evidence). Resolution: the stage's own variable →
    ``ASSURE_OLLAMA_MODEL`` (``ASSURE_OLLAMA_MODEL_B`` for compare) → the CPU
    default. The ``ollama/`` litellm prefix is added.
    """
    stage = _OLLAMA_ROLE_ALIASES.get(role, role)
    if stage not in OLLAMA_STAGE_ENV:
        stage = "draft"
    raw = os.environ.get(OLLAMA_STAGE_ENV[stage], "").strip()
    if not raw and stage == "compare":
        raw = os.environ.get("ASSURE_OLLAMA_MODEL_B", "").strip()
    if not raw:
        raw = os.environ.get("ASSURE_OLLAMA_MODEL", "").strip()
    if not raw:
        raw = OLLAMA_DEFAULT_COMPARE if stage == "compare" else OLLAMA_DEFAULT_MODEL
    return raw if raw.startswith("ollama/") else f"ollama/{raw}"


def local_models_by_stage() -> dict[str, str]:
    """Stage → bare Ollama tag (no ``ollama/`` prefix) for every stage; what
    ollama-pull downloads and /health checks."""
    return {stage: local_model(stage).split("/", 1)[-1] for stage in OLLAMA_STAGE_ENV}


def resolve_model(default: str, *, role: str = "a") -> str:
    """The model a task actually calls: ``default`` on the cloud backend, the
    local Ollama model when ``ASSURE_LLM_BACKEND=ollama``, the per-role Bedrock
    model (``role`` ``"a"``/``"draft"``, ``"analysis"``, ``"b"``) on Bedrock.

    One switch instead of six hard-coded ids (compile, locks, entailment,
    Red-Hat, surgical edit, Compare) so `docker compose up` runs every model
    call against the `ollama` service with no provider key, and production
    runs on Bedrock (OpenRouter removed 2026-10-01)."""
    backend = llm_backend()
    if backend == "ollama":
        return local_model(role)
    if backend == "bedrock":
        return bedrock_model(role)
    return default


def _apply_llm_backend(policies: dict) -> dict:
    backend = llm_backend()
    if backend not in ("ollama", "bedrock"):
        return policies

    def _model_for(task: TaskType) -> str:
        if backend == "ollama":
            return local_model(OLLAMA_TASK_STAGE.get(task, "draft"))
        return bedrock_model("analysis" if task in BEDROCK_ANALYSIS_TASKS else "draft")

    return {
        task: ModelPolicy(
            model_id=_model_for(task),
            max_input_tokens=pol.max_input_tokens,
            # Small local models: cap answers so a 1–2B model does not spend
            # minutes on a 8k-token reply on CPU. Bedrock keeps the policy cap.
            max_output_tokens=min(pol.max_output_tokens, 4096) if backend == "ollama" else pol.max_output_tokens,
            caching=pol.caching if backend == "bedrock" else False,
            litellm_model=_model_for(task),
        )
        for task, pol in policies.items()
    }


TASK_POLICIES = _apply_llm_backend(TASK_POLICIES)

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
    finish_reason: str | None = None
    truncated: bool = False


# The executor's return contract is a 3-tuple consumed by four other modules, so
# the completion's own metadata travels beside it rather than in it: the
# executor sets this and ``execute_with_retry_budget`` reads it straight back.
# An executor that does not set it (a stub, a test double) reports no metadata,
# which reads as "not truncated" — the same as before this existed.
_completion_meta: ContextVar[CompletionMeta | None] = ContextVar(
    "pem_governor_completion_meta", default=None
)


def answer_refusal_reason(result: ExecutionResult, task_type: TaskType) -> str | None:
    """Why this completion cannot be used as a finding, or ``None`` if it can.

    A response cut off at the output ceiling is a partial answer, and a partial
    answer persisted as a finding reaches a client as if it were a review. The
    reader gets a sentence naming what happened instead.
    """
    if not getattr(result, "truncated", False):
        return None
    cap = TASK_POLICIES[task_type].max_output_tokens
    return (
        f"The audit hit its {cap}-token output ceiling "
        f"(finish_reason={getattr(result, 'finish_reason', None)!r}) and its "
        "answer was cut off, so it is not a review. No finding was recorded."
    )


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

        # Bedrock goes through litellm like every other provider (2026-10-01).
        # The hand-written Converse branch that stood here put the language
        # guard's system message inside `messages`, which Converse rejects, so
        # every Bedrock call wrote an error ledger row and then silently fell
        # through to litellm — two rows, double latency. litellm maps `system`,
        # content parts and cache_control to Converse itself.
        try:
            import litellm

            _api_kwargs = _litellm_api_kwargs(model)
            _timeout = _resolve_litellm_timeout()

            def _complete():
                return litellm.completion(
                    model=model if not model.startswith(("anthropic.", "us.", "eu.", "apac.", "global.")) else f"bedrock/{model}",
                    messages=payload,
                    max_tokens=max_output,
                    stream=False,
                    timeout=_timeout,
                    metadata=_model_calls().litellm_metadata(_api_kwargs.pop("metadata", None)),
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
            choices = getattr(resp, "choices", None) or []
            if choices:
                _completion_meta.set(
                    completion_meta(getattr(choices[0], "finish_reason", None), max_output)
                )
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
        last_meta = CompletionMeta()

        for attempt in range(MAX_RETRIES + 1):
            _completion_meta.set(None)
            text, in_tok, out_tok = run(model, messages, policy.max_output_tokens, policy.caching)
            meta = _completion_meta.get() or CompletionMeta()
            last_meta = meta
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
                        finish_reason=meta.finish_reason,
                        truncated=meta.hit_length,
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
                finish_reason=meta.finish_reason,
                truncated=meta.hit_length,
            )

        return ExecutionResult(
            ok=False,
            status="VALIDATION_FAILED",
            error=last_error or "unknown",
            input_tokens=total_in,
            output_tokens=total_out,
            model_id=policy.model_id,
            finish_reason=last_meta.finish_reason,
            truncated=last_meta.hit_length,
        )
