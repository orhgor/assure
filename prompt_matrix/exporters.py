"""Static exports of a saved class. No DSPy/Fabric SDK dependency."""

from __future__ import annotations

try:
    from .engine import MatrixError
    from .library import get_class
except ImportError:
    from engine import MatrixError
    from library import get_class

FORMATS = ("cursorrules", "mdc", "fabric", "dspy")


def export_class(class_id: str, fmt: str, *, target_hint: str | None = None) -> str:
    # Free has export_formats=(). Pro/Team/Self-hosted allow cursorrules, mdc, fabric, dspy.
    fmt = (fmt or "").strip().lower()
    try:
        from .editions import current_edition
    except ImportError:
        from editions import current_edition
    plan = current_edition()
    if not plan.allows_export(fmt):
        allowed = ", ".join(plan.export_formats) or "none on Free (copy from the UI instead)"
        raise MatrixError(
            f"Export '{fmt}' needs Pro, Team, or Self-hosted. This edition allows: {allowed}."
        )
    if fmt not in FORMATS:
        raise MatrixError(f"Unknown export '{fmt}'. Use {', '.join(FORMATS)}.")
    item = get_class(class_id)
    if fmt == "cursorrules":
        return _cursorrules(item)
    if fmt == "mdc":
        return _mdc(item)
    if fmt == "fabric":
        return _fabric(item)
    return _dspy(item, target_hint)


def _cursorrules(item) -> str:
    role = item.role or item.name
    fmt = item.output_format or "Follow the class format."
    body = item.structure or (
        f"You are {role}.\n\n{{{{ task }}}}\n\nOutput:\n{fmt}\n"
        "{% if context %}\n{{ context }}\n{% endif %}\n"
    )
    return (
        f"# {item.name}\n"
        f"{item.description}\n\n"
        f"{body.strip()}\n"
    ).strip() + "\n"


def _mdc(item) -> str:
    desc = (item.description or item.name).replace('"', "'")
    return (
        "---\n"
        f"description: {desc}\n"
        "globs:\n"
        "alwaysApply: false\n"
        "---\n\n"
        f"{_cursorrules(item)}"
    )


def _fabric(item) -> str:
    role = item.role or item.name
    fmt = item.output_format or "Answer the request."
    return (
        "# system.md\n"
        f"You are {role}.\n\n"
        "# user.md\n"
        "# IDENTITY and PURPOSE\n"
        f"{item.description or role}\n\n"
        "# STEPS\n"
        "1. Read the request.\n"
        "2. Follow the output format.\n\n"
        "# OUTPUT INSTRUCTIONS\n"
        f"{fmt}\n\n"
        "INPUT:\n"
        "{{ task }}\n"
    )


def _dspy(item, target_hint: str | None) -> str:
    ident = "".join(part.title() for part in item.id.replace("-", "_").split("_") if part) or "Pem"
    doc = (item.description or item.role or item.name).replace('"""', "'")
    out = (item.output_format or "the answer").replace('"', "'")
    hint = target_hint or item.target_hint or "the compiled prompt"
    return (
        "import dspy\n\n"
        f'class {ident}Signature(dspy.Signature):\n'
        f'    """{doc}"""\n'
        f'    task = dspy.InputField(desc="the user task")\n'
        f'    context = dspy.InputField(desc="optional local context", default="")\n'
        f'    answer = dspy.OutputField(desc="{out}")\n'
        f"# Compile with PEM for {hint}, then optimize this signature in DSPy.\n"
    )
