"""Situational awareness wrap for standalone PEM compiles. Cursor /ask is not wrapped."""

from __future__ import annotations

PREVIEW_CHARS = 500
SITUATIONAL_MARKER = "=== PEM STANDALONE EXECUTION RULES ==="


def _preview(uploaded_file_text: str) -> str:
    text = (uploaded_file_text or "").strip()
    if not text:
        return "(no attached file)"
    if len(text) <= PREVIEW_CHARS:
        return text
    return (
        text[:PREVIEW_CHARS].rstrip()
        + "\n… [preview only; the full file is in the Context section of the dialect prompt]"
    )


def _output_rule(intent: str) -> str:
    if (intent or "").strip().lower() == "research":
        return (
            "Output must be: Thesis, VERIFIED FINDINGS (Directly from uploaded context), "
            "INFERRED GAPS (Logical inference; requires manual validation), OPEN QUESTIONS."
        )
    return "Follow the output format in the dialect prompt for this intent."


def build_situational_awareness(
    user_raw_prompt: str,
    uploaded_file_text: str,
    intent: str = "",
) -> str:
    task = (user_raw_prompt or "").strip() or "(empty task)"
    try:
        from ..pem_runner import ensure_preflight, tool_availability_lines
    except ImportError:
        from pem_runner import ensure_preflight, tool_availability_lines
    ensure_preflight(announce=False)
    return f"""{SITUATIONAL_MARKER}
The user asked: {task}
- Environment: Local CLI (NO @Web, NO live browsing).
{tool_availability_lines()}
- Case and domain: only the user's task and uploaded files. Do not assume a product, industry, or prior case.
- Attached context preview:
{_preview(uploaded_file_text)}

=== CRITICAL CONSTRAINT ===
You are NOT allowed to search the internet. For this execution, "recent" means "as per the attached file and your internal training cutoff only."
Any mention of external facts not in the uploaded file (stats, dates, publication names, competitor claims) must be explicitly labeled as "Inferred from training data, requires manual verification."
If you do not know, write EXACTLY: "Data not available."

=== YOUR TASK ===
Follow the user's instruction strictly. {_output_rule(intent)}"""


def build_final_prompt(
    user_raw_prompt: str,
    uploaded_file_text: str,
    compiled_prompt: str | None = None,
    intent: str = "",
) -> str:
    """Wrap the user's prompt with PEM's standalone grounding rules.

    Full uploaded text still belongs in the dialect Context block. This function
    only prepends awareness. It does not replace the file with a 500-character stub.
    """
    awareness = build_situational_awareness(user_raw_prompt, uploaded_file_text, intent=intent)
    body = (compiled_prompt if compiled_prompt is not None else user_raw_prompt) or ""
    body = body.strip()
    if SITUATIONAL_MARKER in body:
        return body + "\n"
    if not body:
        return awareness + "\n"
    return awareness + "\n\n" + body + "\n"
