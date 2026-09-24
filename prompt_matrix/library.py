"""Local prompt library: classes, saved prompts, and structure learning."""

from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

try:
    from .engine import MatrixError, load_matrix
except ImportError:
    from engine import MatrixError, load_matrix

try:
    from .paths import user_data_dir
except ImportError:
    from paths import user_data_dir

DEFAULT_LIBRARY_PATH = user_data_dir() / "library.json"
_LOCK = threading.Lock()

_XML_BLOCK = re.compile(
    r"^<(role|instructions|context|thinking|format|output)>\s*\n(.*?)</\1>",
    re.S | re.I | re.M,
)
_YOU_ARE = re.compile(
    r"(?:You are|Role:)\s+(.+?)(?:\.|$)",
    re.I,
)
_MD_HEADER = re.compile(r"^#{1,3}\s+(.+)$", re.M)


class PromptClass(BaseModel):
    id: str
    name: str
    description: str = ""
    role: str = ""
    output_format: str = ""
    structure: str | None = None
    wrapper: str | None = None
    target_hint: str | None = None
    source_excerpt: str = ""
    created_at: str
    builtin: bool = False
    versions: list["ClassVersion"] = Field(default_factory=list)


class ClassVersion(BaseModel):
    n: int
    role: str = ""
    output_format: str = ""
    structure: str | None = None
    created_at: str
    note: str = ""


class SavedPrompt(BaseModel):
    id: str
    class_id: str
    target_ai: str
    intent: str
    task: str
    prompt: str
    created_at: str


class PromptLibrary(BaseModel):
    classes: list[PromptClass] = Field(default_factory=list)
    prompts: list[SavedPrompt] = Field(default_factory=list)


class LearnedStructure(BaseModel):
    wrapper: str
    target_guess: str | None = None
    intent_guess: str | None = None
    role: str = ""
    output_format: str = ""
    task_guess: str = ""
    structure: str = ""
    suggested_name: str = ""
    headings: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


def load_library(path: Path | None = None) -> PromptLibrary:
    library_path = path or DEFAULT_LIBRARY_PATH
    with _LOCK:
        if not library_path.is_file():
            library = _seed_library()
            _write_unlocked(library_path, library)
            return library
        try:
            raw = json.loads(library_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MatrixError(f"Could not read library.json: {exc}") from exc
        return PromptLibrary.model_validate(raw)


def save_library(library: PromptLibrary, path: Path | None = None) -> None:
    library_path = path or DEFAULT_LIBRARY_PATH
    with _LOCK:
        _write_unlocked(library_path, library)


def create_class(
    name: str,
    *,
    description: str = "",
    role: str = "",
    output_format: str = "",
    structure: str | None = None,
    wrapper: str | None = None,
    target_hint: str | None = None,
    source_excerpt: str = "",
    path: Path | None = None,
) -> PromptClass:
    name = name.strip()
    if not name:
        raise MatrixError("Class name cannot be empty.")
    library = load_library(path)
    slug = _unique_slug(name, {item.id for item in library.classes})
    item = PromptClass(
        id=slug,
        name=name,
        description=description.strip(),
        role=role.strip(),
        output_format=output_format.strip(),
        structure=(structure.strip() + "\n") if structure and structure.strip() else None,
        wrapper=wrapper,
        target_hint=target_hint,
        source_excerpt=source_excerpt.strip()[:800],
        created_at=_now(),
        builtin=False,
        versions=[
            ClassVersion(
                n=1,
                role=role.strip(),
                output_format=output_format.strip(),
                structure=(structure.strip() + "\n") if structure and structure.strip() else None,
                created_at=_now(),
                note="created",
            )
        ],
    )
    library.classes.append(item)
    save_library(library, path)
    return item


def snapshot_class(class_id: str, *, note: str = "", path: Path | None = None) -> PromptClass:
    library = load_library(path)
    item = next((row for row in library.classes if row.id == class_id), None)
    if item is None:
        raise MatrixError(f"Unknown class '{class_id}'.")
    n = max((ver.n for ver in item.versions), default=0) + 1
    item.versions.append(
        ClassVersion(
            n=n,
            role=item.role,
            output_format=item.output_format,
            structure=item.structure,
            created_at=_now(),
            note=(note or "").strip(),
        )
    )
    save_library(library, path)
    return item


def rollback_class(class_id: str, n: int, path: Path | None = None) -> PromptClass:
    library = load_library(path)
    item = next((row for row in library.classes if row.id == class_id), None)
    if item is None:
        raise MatrixError(f"Unknown class '{class_id}'.")
    ver = next((row for row in item.versions if row.n == int(n)), None)
    if ver is None:
        raise MatrixError(f"No version {n} for class '{class_id}'.")
    item.role = ver.role
    item.output_format = ver.output_format
    item.structure = ver.structure
    save_library(library, path)
    return item


def class_version_diff(class_id: str, a: int, b: int, path: Path | None = None) -> str:
    import difflib

    library = load_library(path)
    item = next((row for row in library.classes if row.id == class_id), None)
    if item is None:
        raise MatrixError(f"Unknown class '{class_id}'.")
    left = next((row for row in item.versions if row.n == int(a)), None)
    right = next((row for row in item.versions if row.n == int(b)), None)
    if left is None or right is None:
        raise MatrixError("Both version numbers must exist.")

    def blob(ver: ClassVersion) -> str:
        return f"role: {ver.role}\nformat: {ver.output_format}\n{ver.structure or ''}"

    return "".join(
        difflib.unified_diff(
            blob(left).splitlines(keepends=True),
            blob(right).splitlines(keepends=True),
            fromfile=f"v{a}",
            tofile=f"v{b}",
        )
    )


def get_saved_prompt(prompt_id: str, path: Path | None = None) -> SavedPrompt:
    library = load_library(path)
    for item in library.prompts:
        if item.id == prompt_id:
            return item
    raise MatrixError(f"Unknown saved prompt '{prompt_id}'.")


def save_prompt(
    *,
    class_id: str,
    target_ai: str,
    intent: str,
    task: str,
    prompt: str,
    path: Path | None = None,
) -> SavedPrompt:
    library = load_library(path)
    if not any(item.id == class_id for item in library.classes):
        raise MatrixError(f"Unknown class '{class_id}'.")
    item = SavedPrompt(
        id=uuid.uuid4().hex[:12],
        class_id=class_id,
        target_ai=target_ai,
        intent=intent,
        task=task.strip(),
        prompt=prompt,
        created_at=_now(),
    )
    library.prompts.insert(0, item)
    save_library(library, path)
    return item


def delete_class(class_id: str, path: Path | None = None) -> None:
    library = load_library(path)
    target = next((item for item in library.classes if item.id == class_id), None)
    if target is None:
        raise MatrixError(f"Unknown class '{class_id}'.")
    if target.builtin:
        raise MatrixError("Built-in classes cannot be deleted. Rename by saving a new one.")
    library.classes = [item for item in library.classes if item.id != class_id]
    library.prompts = [item for item in library.prompts if item.class_id != class_id]
    save_library(library, path)


def delete_prompt(prompt_id: str, path: Path | None = None) -> None:
    library = load_library(path)
    before = len(library.prompts)
    library.prompts = [item for item in library.prompts if item.id != prompt_id]
    if len(library.prompts) == before:
        raise MatrixError(f"Unknown saved prompt '{prompt_id}'.")
    save_library(library, path)


def get_class(class_id: str, path: Path | None = None) -> PromptClass:
    library = load_library(path)
    for item in library.classes:
        if item.id == class_id:
            return item
    raise MatrixError(f"Unknown class '{class_id}'.")


def suggest_class(task: str, intent: str, path: Path | None = None) -> PromptClass | None:
    library = load_library(path)
    if not library.classes:
        return None
    for item in library.classes:
        if item.id == intent:
            return item
    tokens = set(_tokens(task + " " + intent))
    if not tokens:
        return library.classes[0]
    scored: list[tuple[int, PromptClass]] = []
    for item in library.classes:
        hay = " ".join([item.id, item.name, item.description, item.role, item.output_format])
        overlap = len(tokens & _tokens(hay))
        scored.append((overlap, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    best, item = scored[0]
    return item if best else None


def library_payload(path: Path | None = None) -> dict[str, Any]:
    library = load_library(path)
    counts: dict[str, int] = {}
    for item in library.prompts:
        counts[item.class_id] = counts.get(item.class_id, 0) + 1
    return {
        "classes": [
            {**item.model_dump(), "count": counts.get(item.id, 0)} for item in library.classes
        ],
        "prompts": [item.model_dump() for item in library.prompts],
    }


def learn_structure(prompt_text: str) -> LearnedStructure:
    text = (prompt_text or "").strip()
    if not text:
        raise MatrixError("Paste a prompt to learn from.")

    xml_fields = {
        match.group(1).lower(): match.group(2).strip() for match in _XML_BLOCK.finditer(text)
    }
    headings = [item.strip() for item in _MD_HEADER.findall(text)]
    wrapper, target_guess = _guess_target(text, xml_fields, headings)
    role = xml_fields.get("role") or _first_you_are(text)
    output_format = _extract_format(text, xml_fields)
    task_guess = _extract_task(text, xml_fields)
    intent_guess = _guess_intent(text, role, output_format)
    structure = _to_template(text, task_guess, xml_fields)
    suggested_name = _suggest_name(headings, role, intent_guess, task_guess)
    tags = [item for item in [wrapper, target_guess, intent_guess] if item]

    return LearnedStructure(
        wrapper=wrapper,
        target_guess=target_guess,
        intent_guess=intent_guess,
        role=role,
        output_format=output_format,
        task_guess=task_guess,
        structure=structure,
        suggested_name=suggested_name,
        headings=headings[:8],
        tags=tags,
    )


def _seed_library() -> PromptLibrary:
    try:
        config = load_matrix()
    except MatrixError:
        return PromptLibrary()
    classes = []
    for key, intent in config.intents.items():
        classes.append(
            PromptClass(
                id=key,
                name=key.replace("_", " "),
                description=f"Built-in {key} class from config.json.",
                role=intent.role,
                output_format=intent.output_format,
                created_at=_now(),
                builtin=True,
            )
        )
    return PromptLibrary(classes=classes)


def _write_unlocked(path: Path, library: PromptLibrary) -> None:
    path.write_text(library.model_dump_json(indent=2) + "\n", encoding="utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _unique_slug(name: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "class"
    slug = base
    index = 2
    while slug in taken:
        slug = f"{base}-{index}"
        index += 1
    return slug


def _guess_target(
    text: str, xml_fields: dict[str, str], headings: list[str]
) -> tuple[str, str | None]:
    lowered = text.lower()
    if "/ask @workspace" in lowered or "output only the code" in lowered:
        return "markdown", "cursor"
    if "thinking" in xml_fields or "instructions" in xml_fields:
        return "xml", "claude"
    if any(h.lower() in {"system prompt", "user request", "self-correction"} for h in headings):
        return "markdown", "gemini"
    if "you are kimi" in lowered or "moonshot" in lowered:
        return "markdown", "kimi"
    if "data not available" in lowered or "do not hallucinate" in lowered:
        return "plain", "gemini"
    if xml_fields:
        return "xml", "claude"
    if headings:
        return "markdown", None
    return "plain", None


def _first_you_are(text: str) -> str:
    match = _YOU_ARE.search(text)
    if not match:
        return ""
    return match.group(1).strip().rstrip(".")


def _extract_format(text: str, xml_fields: dict[str, str]) -> str:
    for key in ("output_format", "format", "output"):
        if xml_fields.get(key):
            return xml_fields[key]
    patterns = [
        r"Produce the answer in this format:\s*(.+?)(?:\n\s*\n|</)",
        r"## Output Format\n(.+?)(?:\n## |\Z)",
        r"## Output rules\n(.+?)(?:\n## |\Z)",
        r"Output constraints:\n(.+?)(?:\n\n|\Z)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.S | re.I)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip()
    return ""


def _extract_task(text: str, xml_fields: dict[str, str]) -> str:
    if xml_fields.get("instructions"):
        block = xml_fields["instructions"]
        block = re.split(r"Produce the answer in this format:", block, maxsplit=1)[0]
        return block.strip()
    for header in ("User Request", "Task", "User"):
        match = re.search(rf"## {header}\n(.+?)(?:\n## |\Z)", text, re.S | re.I)
        if match:
            return match.group(1).strip()
    match = re.search(r"Task:\s*\n(.+?)(?:\n\n|\Z)", text, re.S)
    if match:
        return match.group(1).strip()
    return ""


def _guess_intent(text: str, role: str, output_format: str) -> str | None:
    blob = f"{text}\n{role}\n{output_format}".lower()
    scores = {
        "comparison": ("compare", "vs", "versus", "tradeoff", "table"),
        "debug": ("debug", "error", "stack", "traceback", "fail", "bug"),
        "design": ("design", "architect", "api", "schema", "endpoint"),
        "analysis": ("analy", "metric", "p95", "quant", "data"),
        "research": ("research", "source", "cite", "survey"),
    }
    ranked = []
    for intent, words in scores.items():
        ranked.append((sum(1 for word in words if word in blob), intent))
    ranked.sort(reverse=True)
    return ranked[0][1] if ranked and ranked[0][0] else None


def _to_template(text: str, task_guess: str, xml_fields: dict[str, str]) -> str:
    template = text
    if task_guess:
        template = template.replace(task_guess, "{{ task }}", 1)
    context_body = xml_fields.get("context")
    if context_body:
        template = template.replace(context_body, "{{ context }}", 1)
    else:
        template = re.sub(
            r"(## Context\n)(.+?)(\n## |\Z)",
            r"\1{% if context %}{{ context }}\n{% endif %}\3",
            template,
            count=1,
            flags=re.S,
        )
    if "{{ task }}" not in template:
        template = (
            template.rstrip() + "\n\n{{ task }}\n{% if context %}\n{{ context }}\n{% endif %}\n"
        )
    elif "{{ context }}" not in template:
        template = template.rstrip() + "\n{% if context %}\n{{ context }}\n{% endif %}\n"
    return template.strip() + "\n"


def _suggest_name(headings: list[str], role: str, intent_guess: str | None, task_guess: str) -> str:
    skip = {
        "system prompt",
        "user request",
        "output format",
        "self-correction",
        "task",
        "context",
        "output rules",
        "role",
        "instructions",
    }
    for heading in headings:
        if heading.lower() not in skip:
            return heading[:48]
    if intent_guess:
        return intent_guess.replace("_", " ")
    if role:
        return role.split(",")[0][:48]
    if task_guess:
        return task_guess.split("\n", 1)[0][:40]
    return "learned class"


def _tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]{3,}", text.lower()) if token}
