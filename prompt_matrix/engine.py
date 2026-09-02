"""Load the matrix, render Jinja2 prompts, copy them, or send them through LiteLLM."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateError, TemplateSyntaxError
from pydantic import ValidationError

try:
    from .models import (
        ExecutionResult,
        MatrixConfig,
        PromptRequest,
        RenderedPrompt,
        RuntimeSettings,
        StructuredAIResponse,
        TargetConfig,
    )
except ImportError:
    from models import (
        ExecutionResult,
        MatrixConfig,
        PromptRequest,
        RenderedPrompt,
        RuntimeSettings,
        StructuredAIResponse,
        TargetConfig,
    )

try:
    from .paths import resource_dir
except ImportError:
    from paths import resource_dir

PACKAGE_DIR = resource_dir()
DEFAULT_CONFIG_PATH = PACKAGE_DIR / "config.json"

MAX_FILE_BYTES = 200_000
MAX_TOTAL_BYTES = 500_000
MAX_FILES = 20
MAX_GLOB_LEN = 240
MAX_PATH_LEN = 1024
MAX_PATH_PART = 255
GLOB_PATH_ERROR = (
    "Could not search those file paths. Remove * and ** from the question, "
    "or put paths only in Extra context."
)

TARGET_ALIASES = {
    "anthropic": "claude",
    "sonnet": "claude",
    "opus": "claude",
    "google": "gemini",
    "bard": "gemini",
    "ds": "deepseek",
    "moonshot": "kimi",
    "kimi.ai": "kimi",
    "moonshot-ai": "kimi",
    "k2": "kimi",
    "k3": "kimi",
    "llama": "ollama",
    "local": "ollama",
    "vllm": "ollama",
    "sglang": "ollama",
    "llamafile": "ollama",
    "lmstudio": "ollama",
    "lm-studio": "ollama",
    "cursor-ide": "cursor",
    "ide": "cursor",
}

DEFAULT_MODELS = {
    "claude": "anthropic/claude-sonnet-4-5",
    "gemini": "gemini/gemini-3.5-flash",
    "deepseek": "deepseek/deepseek-chat",
    "kimi": "moonshot/kimi-k2.5",
    "ollama": "ollama/llama3.2",
    "cursor": None,
}

API_KEY_HINTS = {
    "claude": "ANTHROPIC_API_KEY or CLAUDE_API_KEY",
    "gemini": "GEMINI_API_KEY or GOOGLE_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "kimi": "MOONSHOT_API_KEY or KIMI_API_KEY",
}

_PATH_TOKEN = re.compile(
    r"(?:@)?("
    r"(?:~|\\.{1,2})?(?:/[^\s,;]+)+"
    r"|(?:[A-Za-z]:[\\/][^\s,;]+)"
    r"|(?:[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.*?-]+)+\.[A-Za-z0-9]+)"
    r"|(?:\./[^\s,;]+)"
    r")"
)


class MatrixError(Exception):
    """User-facing error. The CLI prints this and exits."""


class ConfigError(MatrixError):
    pass


class UnknownTargetError(MatrixError):
    pass


class UnknownIntentError(MatrixError):
    pass


class RenderError(MatrixError):
    pass


class DirectCallError(MatrixError):
    pass


def load_matrix(config_path: str | Path | None = None) -> MatrixConfig:
    """Read and validate config.json. Missing keys become a ConfigError, not a stack trace."""
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    if not path.is_file():
        raise ConfigError(
            f"Config file not found: {path}. "
            "Keep config.json next to engine.py, or pass an explicit path."
        )

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config.json is not valid JSON ({path}): {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"Could not read {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("config.json must be a JSON object with 'targets' and 'intents'.")

    missing = [key for key in ("targets", "intents") if key not in raw]
    if missing:
        raise ConfigError(
            f"config.json is missing required key(s): {', '.join(missing)}. "
            "Expected {\"targets\": {...}, \"intents\": {...}}."
        )

    try:
        config = MatrixConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(_format_pydantic_error("config.json", exc)) from exc

    _validate_templates(config, path.parent)
    return config


def apply_runtime_options(
    config: MatrixConfig | None = None,
    *,
    store_prompts: bool | None = None,
    max_tokens: int | None = None,
    timeout: int | None = None,
    critic: str | None = None,
    auth_user: str | None = None,
    auth_pass: str | None = None,
    cheap: bool | None = None,
    model: str | None = None,
    edition: str | None = None,
) -> None:
    """Push CLI flags into env so LiteLLM, history, auth, and the rule critic see them."""
    rt = (config.runtime if config is not None else RuntimeSettings())
    if store_prompts or rt.store_prompts:
        os.environ["PEM_STORE_PROMPTS"] = "1"
    if cheap:
        os.environ["PEM_COST_ROUTE"] = "1"
    if edition:
        os.environ["ASSURE_EDITION"] = edition.strip().lower()
    elif not os.environ.get("ASSURE_EDITION") and not os.environ.get("PEM_EDITION"):
        os.environ["ASSURE_EDITION"] = (rt.edition or "free").strip().lower()
    if model:
        os.environ["PEM_MODEL"] = model
    if max_tokens is not None:
        os.environ["PEM_MAX_TOKENS"] = str(max_tokens)
    elif not os.environ.get("PEM_MAX_TOKENS"):
        os.environ["PEM_MAX_TOKENS"] = str(rt.max_tokens)
    if timeout is not None:
        os.environ["PEM_TIMEOUT_SECONDS"] = str(timeout)
    elif not os.environ.get("PEM_TIMEOUT_SECONDS"):
        os.environ["PEM_TIMEOUT_SECONDS"] = str(rt.timeout_seconds)
    if (critic or "").strip().lower() == "rule":
        os.environ["PEM_CRITIC_MODE"] = "rule"
    if auth_user:
        os.environ["PEM_HTTP_USER"] = auth_user
    elif not os.environ.get("PEM_HTTP_USER"):
        os.environ["PEM_HTTP_USER"] = rt.auth_user
    if auth_pass:
        os.environ["PEM_HTTP_PASS"] = auth_pass


def render_prompt(
    target_ai: str,
    intent: str,
    user_task: str,
    context_text: str = "",
    *,
    config: MatrixConfig | None = None,
    config_path: str | Path | None = None,
) -> str:
    """Fetch the target template, render it with Jinja2, return the final prompt string."""
    rendered = render_prompt_detailed(
        target_ai,
        intent,
        user_task,
        context_text,
        config=config,
        config_path=config_path,
    )
    return rendered.prompt


def render_prompt_detailed(
    target_ai: str,
    intent: str,
    user_task: str,
    context_text: str = "",
    *,
    config: MatrixConfig | None = None,
    config_path: str | Path | None = None,
    class_id: str | None = None,
    format_override: str | None = None,
) -> RenderedPrompt:
    matrix = config or load_matrix(config_path)
    try:
        request = PromptRequest(
            target_ai=normalize_target(target_ai, matrix),
            intent=intent,
            task=user_task,
            context=context_text or "",
        )
    except ValidationError as exc:
        raise MatrixError(_format_pydantic_error("prompt request", exc)) from exc

    target = _get_target(matrix, request.target_ai)
    intent_cfg = _get_intent(matrix, request.intent)

    role = intent_cfg.role
    output_format = intent_cfg.output_format
    structure = target.structure
    wrapper = target.wrapper
    used_class = None
    if class_id:
        try:
            from .library import get_class
        except ImportError:
            from library import get_class
        prompt_class = get_class(class_id)
        used_class = prompt_class.id
        if prompt_class.role:
            role = prompt_class.role
        if prompt_class.output_format:
            output_format = prompt_class.output_format
        if prompt_class.structure:
            structure = prompt_class.structure
        if prompt_class.wrapper:
            wrapper = prompt_class.wrapper
    elif format_override and str(format_override).strip():
        output_format = str(format_override).strip()

    files_read, context = expand_context(request.context, request.task)

    env = _jinja_env(DEFAULT_CONFIG_PATH.parent)
    try:
        template = env.from_string(structure)
        prompt = template.render(
            role=role,
            format=output_format,
            output_format=output_format,
            task=request.task,
            context=context,
            target=request.target_ai,
            intent=request.intent,
            wrapper=wrapper,
        )
    except TemplateError as exc:
        raise RenderError(
            f"Jinja2 failed while rendering the '{request.target_ai}' template: {exc}"
        ) from exc

    prompt = prompt.strip() + "\n"
    if request.target_ai != "cursor":
        try:
            from .core.prompt_builder import build_final_prompt
            from .config.system_prompt import apply_base_instruction
        except ImportError:
            from core.prompt_builder import build_final_prompt
            from config.system_prompt import apply_base_instruction
        prompt = build_final_prompt(
            request.task, context, compiled_prompt=prompt, intent=request.intent
        )
        prompt = apply_base_instruction(prompt, intent=request.intent)
    return RenderedPrompt(
        target_ai=request.target_ai,
        intent=request.intent,
        wrapper=wrapper,
        prompt=prompt,
        context_injected=bool(context),
        files_read=files_read,
        class_id=used_class,
    )


def execute(
    target_ai: str,
    intent: str,
    user_task: str,
    context_text: str = "",
    *,
    direct: bool = False,
    copy: bool = True,
    save_path: str | Path | None = None,
    model: str | None = None,
    config: MatrixConfig | None = None,
    config_path: str | Path | None = None,
    class_id: str | None = None,
) -> ExecutionResult:
    """Render the prompt, then copy it to the clipboard or send it through LiteLLM."""
    rendered = render_prompt_detailed(
        target_ai,
        intent,
        user_task,
        context_text,
        config=config,
        config_path=config_path,
        class_id=class_id,
    )

    reply: str | None = None
    structured: StructuredAIResponse | None = None
    copied = False
    saved_to: str | None = None
    note: str | None = None
    used_direct = direct
    should_copy = copy

    if used_direct:
        model_id = model or _resolve_model(rendered.target_ai, config)
        if not model_id:
            note = (
                "Cursor has no public chat API. Copied the /ask @workspace prompt "
                "so you can paste it into the Cursor agent."
            )
            used_direct = False
            should_copy = True
        else:
            reply, structured = send_to_llm(
                rendered,
                model=model,
                structured=save_path is not None,
                config=config,
            )

    if not used_direct:
        should_copy = bool(copy)
    if should_copy:
        copied = copy_to_clipboard(rendered.prompt)

    if save_path is not None:
        saved_to = str(_save_result(save_path, rendered, reply, structured))

    return ExecutionResult(
        rendered=rendered,
        copied=copied,
        direct=used_direct,
        reply=reply,
        structured=structured,
        saved_to=saved_to,
        note=note,
    )


def copy_to_clipboard(text: str) -> bool:
    try:
        import pyperclip
    except ImportError as exc:
        raise MatrixError("pyperclip is not installed. Run: pip install -r requirements.txt") from exc

    try:
        pyperclip.copy(text)
    except Exception as exc:
        raise MatrixError(
            "Could not write to the clipboard. On Linux you may need xclip or xsel. "
            f"Original error: {exc}"
        ) from exc
    return True


def send_to_llm(
    rendered: RenderedPrompt,
    *,
    model: str | None = None,
    max_tokens: int | None = None,
    timeout: int | None = None,
    structured: bool = False,
    config: MatrixConfig | None = None,
) -> tuple[str, StructuredAIResponse | None]:
    target_name = rendered.target_ai
    model_id = model or _resolve_model(target_name, config)

    if not model_id:
        raise DirectCallError(
            "Cursor has no public chat API. The prompt was built for /ask @workspace. "
            "Copy it into the Cursor agent instead of using --direct."
        )

    try:
        import litellm
    except ImportError as exc:
        raise DirectCallError(
            "litellm is not installed. Run: pip install litellm   (or pip install -r requirements.txt)"
        ) from exc

    try:
        from .keys import litellm_kwargs_for, load_keys, missing_key_message
    except ImportError:
        from keys import litellm_kwargs_for, load_keys, missing_key_message

    load_keys()
    runner = None
    if target_name == "ollama":
        try:
            from .local_runners import litellm_kwargs, pick_local
        except ImportError:
            from local_runners import litellm_kwargs, pick_local
        runner = pick_local(wake_ollama=True)
    missing = None if runner else missing_key_message(target_name)
    if missing:
        raise DirectCallError(missing)

    litellm.drop_params = True
    messages = [{"role": "user", "content": rendered.prompt}]
    extra = litellm_kwargs_for(target_name)

    if structured:
        return _send_structured(
            litellm,
            model_id,
            messages,
            target_name,
            intent=rendered.intent,
            extra=extra,
        )

    try:
        from .litellm_runner import call_model
    except ImportError:
        from litellm_runner import call_model

    try:
        if runner:
            model_id, local_extra = litellm_kwargs(runner)
            extra.update(local_extra)
        content = call_model(
            model_id,
            messages,
            max_tokens=max_tokens,
            timeout=timeout,
            intent=rendered.intent,
            **extra,
        )
    except Exception as exc:
        try:
            from .workflow_cap import WorkflowTimeout
        except ImportError:
            from workflow_cap import WorkflowTimeout
        if isinstance(exc, WorkflowTimeout):
            raise
        raise _wrap_llm_error(target_name, model_id, exc) from exc

    if isinstance(content, str) and content.startswith("ERROR:"):
        raise DirectCallError(content)
    if not content:
        raise DirectCallError("The model returned an empty reply.")
    return str(content), None


def expand_context(context_text: str, user_task: str) -> tuple[list[str], str]:
    """Turn file paths (./src/app.py, @README.md, globs) into template context."""
    try:
        found = _discover_paths(f"{context_text}\n{user_task}")
    except (RecursionError, OSError) as exc:
        raise RenderError(GLOB_PATH_ERROR) from exc
    files, blobs = _read_paths(found)
    original_names = [str(path) for path in found] + files

    prose = (context_text or "").strip()
    if prose and _is_only_paths(prose, original_names):
        prose = ""

    sections: list[str] = []
    if prose:
        sections.append(prose)
    if blobs:
        sections.append(blobs)
    return files, "\n\n".join(sections).strip()


def normalize_target(name: str, config: MatrixConfig | None = None) -> str:
    cleaned = (name or "").strip().lower()
    cleaned = TARGET_ALIASES.get(cleaned, cleaned)
    if config and cleaned not in config.targets:
        raise UnknownTargetError(
            f"Unknown target '{name}'. Available: {', '.join(sorted(config.targets))}."
        )
    return cleaned


def list_targets(config: MatrixConfig) -> list[tuple[str, str]]:
    rows = []
    for key, target in config.targets.items():
        rows.append((key, target.label or key))
    return rows


def list_intents(config: MatrixConfig) -> list[tuple[str, str]]:
    rows = []
    for key, intent in config.intents.items():
        preview = intent.role if len(intent.role) <= 72 else intent.role[:69] + "..."
        rows.append((key, preview))
    return rows


def _get_target(config: MatrixConfig, name: str) -> TargetConfig:
    try:
        return config.targets[name]
    except KeyError as exc:
        raise UnknownTargetError(
            f"Unknown target '{name}'. Available: {', '.join(sorted(config.targets))}."
        ) from exc


def _get_intent(config: MatrixConfig, name: str) -> Any:
    try:
        return config.intents[name]
    except KeyError as exc:
        raise UnknownIntentError(
            f"Unknown intent '{name}'. Available: {', '.join(sorted(config.intents))}."
        ) from exc


def _resolve_model(target_ai: str, config: MatrixConfig | None) -> str | None:
    override = os.environ.get("PEM_MODEL")
    try:
        from .cost_router import rewrite_send_id, routed_model
    except ImportError:
        from cost_router import rewrite_send_id, routed_model
    if override:
        return rewrite_send_id(override)
    routed = routed_model(target_ai)
    if routed:
        return rewrite_send_id(routed)
    if target_ai == "ollama":
        local = os.environ.get("PEM_OLLAMA_MODEL")
        if local:
            raw = local if local.startswith("ollama/") else f"ollama/{local}"
            return rewrite_send_id(raw)
    if config and target_ai in config.targets and config.targets[target_ai].model:
        return rewrite_send_id(config.targets[target_ai].model)
    return rewrite_send_id(DEFAULT_MODELS.get(target_ai))


def _jinja_env(search_dir: Path) -> Environment:
    loader = FileSystemLoader(str(search_dir))
    return Environment(
        loader=loader,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        autoescape=False,
    )


def _validate_templates(config: MatrixConfig, search_dir: Path) -> None:
    env = _jinja_env(search_dir)
    errors: list[str] = []
    for name, target in config.targets.items():
        try:
            env.parse(target.structure)
        except TemplateSyntaxError as exc:
            errors.append(f"targets.{name}.structure: {exc}")
    if errors:
        raise ConfigError("Invalid Jinja2 in config.json:\n  - " + "\n  - ".join(errors))


def _discover_paths(text: str) -> list[Path]:
    if not text.strip():
        return []

    candidates: list[str] = []
    stripped = text.strip()
    # A whole question with ? or **bold** is not a filesystem glob.
    if "\n" not in stripped and (_looks_like_path(stripped) or _looks_like_fs_glob(stripped)):
        candidates.append(os.path.expanduser(stripped))

    for raw in text.replace(",", " ").split():
        token = raw.strip().strip("`'\"")
        mentioned = token.startswith("@")
        if mentioned:
            token = token[1:]
        token = os.path.expanduser(token)
        if token and (mentioned or _should_try_read(token)):
            candidates.append(token)

    for match in _PATH_TOKEN.finditer(text):
        candidates.append(os.path.expanduser(match.group(1)))

    found: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            expanded = _expand_candidate(candidate)
        except OSError:
            continue
        for path in expanded:
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            found.append(path)
            if len(found) >= MAX_FILES:
                return found
    return found


def _plausible_path_token(candidate: str) -> bool:
    """Skip binary blobs, URLs, and overlong names before calling stat()."""
    if not candidate or any(ch in candidate for ch in "\n\r"):
        return False
    if len(candidate) > MAX_PATH_LEN:
        return False
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in candidate):
        return False
    if candidate.startswith(("http://", "https://", "file:")):
        return False
    parts = candidate.replace("\\", "/").split("/")
    if any(len(part) > MAX_PATH_PART for part in parts):
        return False
    return True


def _looks_like_fs_glob(candidate: str) -> bool:
    """True only for path globs like ./src/**/*.py, not questions or markdown."""
    if not _plausible_path_token(candidate):
        return False
    if len(candidate) > MAX_GLOB_LEN or candidate.count("*") > 8:
        return False
    if "*" not in candidate and "[" not in candidate:
        return False
    if " " in candidate:
        return False
    if "/" in candidate or "\\" in candidate or candidate.startswith(("./", "../", "~", "@")):
        return True
    return candidate.endswith((".py", ".md", ".txt", ".json", ".html", ".css", ".js"))


def _expand_candidate(candidate: str) -> list[Path]:
    if not _should_try_read(candidate):
        return []
    path = Path(candidate)
    if _looks_like_fs_glob(candidate):
        import glob

        try:
            matches = [Path(item) for item in glob.glob(candidate, recursive=True)]
        except (RecursionError, OSError, ValueError):
            return []
        out: list[Path] = []
        for item in matches:
            try:
                if item.is_file():
                    out.append(item)
            except OSError:
                continue
            if len(out) >= MAX_FILES:
                break
        return out

    try:
        if path.is_file():
            return [path]
    except OSError:
        return []
    return []


def _should_try_read(candidate: str) -> bool:
    if not candidate or candidate in {".", "..", "/", "~"}:
        return False
    if not _plausible_path_token(candidate):
        return False
    if _looks_like_fs_glob(candidate):
        return True
    if candidate.startswith(("./", "../", "/", "~/", "~\\", "@")):
        return True
    return "/" in candidate or "\\" in candidate


def _looks_like_path(text: str) -> bool:
    if not text or "\n" in text:
        return False
    if text.startswith(("./", "../", "/", "~/", "@")):
        return True
    if "\\" in text and (":" in text[:3] or text.startswith("\\")):
        return True
    return "/" in text and Path(text).suffix != ""


def _is_only_paths(text: str, files: list[str]) -> bool:
    leftover = text
    for name in files:
        leftover = leftover.replace(f"./{name}", " ")
        leftover = leftover.replace(f".\\{name}", " ")
        leftover = leftover.replace(name, " ")
    leftover = _PATH_TOKEN.sub(" ", leftover)
    leftover = leftover.replace("@", " ")
    leftover = re.sub(r"[\s,;:'\"`.\\/-]+", "", leftover)
    return leftover == ""


def _read_paths(paths: list[Path]) -> tuple[list[str], str]:
    used: list[str] = []
    chunks: list[str] = []
    total = 0

    for path in paths:
        try:
            is_file = path.is_file()
            is_dir = False if is_file else path.is_dir()
        except OSError:
            continue
        if not is_file:
            if is_dir:
                listing = _list_directory(path)
                chunks.append(listing)
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in data[:4096]:
            continue
        if len(data) > MAX_FILE_BYTES:
            data = data[:MAX_FILE_BYTES] + b"\n... [truncated]\n"
        if total + len(data) > MAX_TOTAL_BYTES:
            chunks.append(
                f"### File: {path}\nFurther files skipped (context cap of {MAX_TOTAL_BYTES} bytes)."
            )
            break
        total += len(data)
        text = data.decode("utf-8", errors="replace")
        fence = _fence_for(text)
        lang = path.suffix.lstrip(".")
        display = _display_path(path)
        chunks.append(f"### File: {display}\n{fence}{lang}\n{text.rstrip()}\n{fence}")
        used.append(display)

    return used, "\n\n".join(chunks)


def _list_directory(path: Path) -> str:
    try:
        names = sorted(p.name for p in path.iterdir())[:40]
    except OSError as exc:
        return f"### Directory: {path}\nCould not list contents: {exc}"
    listing = "\n".join(f"- {name}" for name in names)
    return (
        f"### Directory: {_display_path(path)}\n"
        "Pass a file path or glob if you want file contents injected.\n"
        f"{listing}"
    )


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def _fence_for(text: str) -> str:
    ticks = "```"
    while ticks in text:
        ticks += "`"
    return ticks


def _send_structured(
    litellm: Any,
    model_id: str,
    messages: list[dict[str, str]],
    target_name: str,
    intent: str | None = None,
    extra: dict[str, Any] | None = None,
) -> tuple[str, StructuredAIResponse]:
    extra = dict(extra or {})
    try:
        import instructor
    except ImportError:
        return _send_structured_fallback(litellm, model_id, messages, target_name, extra=extra)

    factory = getattr(instructor, "from_litellm", None)
    if factory is None:
        return _send_structured_fallback(litellm, model_id, messages, target_name, extra=extra)

    client = factory(litellm.completion)
    try:
        from .litellm_runner import completion_limits
    except ImportError:
        from litellm_runner import completion_limits
    max_tokens, timeout = completion_limits(intent=intent, model=model_id)
    try:
        parsed = client.chat.completions.create(
            model=model_id,
            messages=messages,
            response_model=StructuredAIResponse,
            max_tokens=max_tokens,
            timeout=timeout,
            **extra,
        )
    except Exception as exc:
        raise _wrap_llm_error(target_name, model_id, exc) from exc

    if not isinstance(parsed, StructuredAIResponse):
        parsed = StructuredAIResponse.model_validate(parsed)
    return parsed.answer, parsed


def _send_structured_fallback(
    litellm: Any,
    model_id: str,
    messages: list[dict[str, str]],
    target_name: str,
    extra: dict[str, Any] | None = None,
) -> tuple[str, StructuredAIResponse]:
    schema = StructuredAIResponse.model_json_schema()
    forced = list(messages) + [
        {
            "role": "user",
            "content": (
                "Reply with a single JSON object that matches this schema. "
                f"No markdown fences.\n{json.dumps(schema)}"
            ),
        }
    ]
    try:
        from .litellm_runner import call_model
    except ImportError:
        from litellm_runner import call_model
    try:
        content = call_model(model_id, forced, **(extra or {}))
        if content.startswith("ERROR:"):
            raise DirectCallError(content)
    except DirectCallError:
        raise
    except Exception as exc:
        raise _wrap_llm_error(target_name, model_id, exc) from exc

    if not content:
        raise DirectCallError("The model returned an empty reply.")
    parsed = _parse_json_model(content)
    return parsed.answer, parsed


def _parse_json_model(content: str) -> StructuredAIResponse:
    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise DirectCallError(
            "The model did not return valid JSON for --save. "
            f"Raw reply starts with: {stripped[:180]!r}"
        ) from exc
    try:
        return StructuredAIResponse.model_validate(payload)
    except ValidationError as exc:
        raise DirectCallError(_format_pydantic_error("model JSON reply", exc)) from exc


def _save_result(
    save_path: str | Path,
    rendered: RenderedPrompt,
    reply: str | None,
    structured: StructuredAIResponse | None,
) -> Path:
    path = Path(save_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "target_ai": rendered.target_ai,
        "intent": rendered.intent,
        "wrapper": rendered.wrapper,
        "files_read": rendered.files_read,
        "prompt": rendered.prompt,
        "reply": reply,
        "structured": structured.model_dump() if structured else None,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _wrap_llm_error(target_name: str, model_id: str, exc: Exception) -> DirectCallError:
    hint = API_KEY_HINTS.get(target_name, "the provider API key")
    message = str(exc)
    lowered = message.lower()
    if any(word in lowered for word in ("api key", "authentication", "unauthorized", "401")):
        return DirectCallError(
            f"LiteLLM could not authenticate for {target_name} ({model_id}). "
            f"Set {hint} in your environment."
        )
    if target_name == "ollama" and any(
        word in lowered for word in ("connection refused", "connect", "11434", "failed to connect")
    ):
        return DirectCallError(
            f"Ollama is not reachable ({model_id}). Start it with `ollama serve`, "
            "then `ollama pull llama3.2` (or set PEM_OLLAMA_MODEL to a model you have)."
        )
    return DirectCallError(f"LiteLLM call failed ({model_id}): {exc}")


def _format_pydantic_error(label: str, exc: ValidationError) -> str:
    lines = [f"Invalid {label}:"]
    for err in exc.errors():
        loc = ".".join(str(part) for part in err.get("loc", ())) or "(root)"
        lines.append(f"  - {loc}: {err.get('msg')}")
    return "\n".join(lines)
