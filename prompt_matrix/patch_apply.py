"""Safe unified-diff apply and truncation checks. No live APIs."""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

TRUNC_MARK = "... [truncated]"
LENGTH_FINISH_REASONS = frozenset(
    {
        "length",
        "max_tokens",
        "max_output_tokens",
        "max_output_length",
        "token_limit",
        "max_tokens_reached",
    }
)

_BEGIN_PATCH = re.compile(r"(?im)^\*\*\*\s*Begin Patch\b")
_END_PATCH = re.compile(r"(?im)^\*\*\*\s*End Patch\b")
_DIFF_GIT = re.compile(r"^diff --git a/(.+?) b/(.+)$", re.MULTILINE)
_PLUS_PLUS = re.compile(r"^\+\+\+ (?:b/)?(.+)$", re.MULTILINE)
_MINUS_MINUS = re.compile(r"^--- (?:a/)?(.+)$", re.MULTILINE)
_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_FILES_HEADING = re.compile(r"(?im)^#{1,6}\s+files to write\s*$")
_FILES_LABEL = re.compile(r"(?im)^(?:files?|paths?)\s*:\s*$")
_PATH_ITEM = re.compile(
    r"^\s*(?:[-*]|\d+[.)])\s+`?([A-Za-z0-9][A-Za-z0-9._/-]*\.[A-Za-z0-9]{1,12})`?\s*$"
)
_BARE_PATH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*\.[A-Za-z0-9]{1,12}$")


class PatchError(ValueError):
    """Rejected or failed patch apply."""


@dataclass(frozen=True)
class Truncation:
    truncated: bool
    reasons: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.truncated


def dump_is_truncated(
    text: str,
    *,
    path: str | None = None,
    finish_reason: str | None = None,
) -> Truncation:
    """True when a role dump looks cut off and must not be applied."""
    reasons: list[str] = []
    blob = text or ""
    reason = (finish_reason or "").strip().lower().replace(" ", "_")
    if reason in LENGTH_FINISH_REASONS:
        reasons.append(f"finish_reason={finish_reason}")
    if TRUNC_MARK in blob:
        reasons.append(f"contains '{TRUNC_MARK}'")
    if blob.count("```") % 2:
        reasons.append("unclosed code fence")
    if _BEGIN_PATCH.search(blob) and not _END_PATCH.search(blob):
        reasons.append("PATCH header without end")
    stripped = blob.rstrip()
    if re.search(r"(?m)^diff --git ", stripped) and "@@" not in stripped:
        reasons.append("diff header without hunks")
    if stripped.endswith("@@") or _ends_on_open_hunk(stripped):
        reasons.append("hunk cut off")
    path_hint = truncation_reason_for_body(path or "", blob)
    if path_hint:
        reasons.append(path_hint)
    elif _html_dump_truncated(blob):
        reasons.append("cut HTML dump")
    # de-dupe while preserving order
    seen: list[str] = []
    for item in reasons:
        if item not in seen:
            seen.append(item)
    return Truncation(bool(seen), tuple(seen))


def truncation_reason_for_body(path: str, body: str) -> str | None:
    text = body or ""
    lowered = (path or "").lower()
    if TRUNC_MARK in text:
        return f"{path or 'body'} contains '{TRUNC_MARK}'"
    if text.count("```") % 2:
        return f"{path or 'body'} has an unclosed code fence"
    if lowered.endswith((".html", ".htm")) or _looks_like_html_document(text):
        stripped = text.strip()
        head = stripped[:240].lower()
        if ("<!doctype" in head or "<html" in head) and "</html>" not in stripped.lower():
            return f"{path or 'html'} looks like a cut HTML dump (no closing </html>)"
        last_lt = stripped.rfind("<")
        last_gt = stripped.rfind(">")
        if stripped.endswith("<") or last_lt > last_gt:
            return f"{path or 'html'} ends on an unclosed HTML tag"
    return None


def patch_looks_truncated(diff: str) -> str | None:
    """Reject applying a dump that is not a complete unified diff."""
    blob = diff or ""
    if TRUNC_MARK in blob:
        return f"diff contains '{TRUNC_MARK}'"
    if blob.count("```") % 2:
        return "unclosed code fence"
    if _BEGIN_PATCH.search(blob) and not _END_PATCH.search(blob):
        return "PATCH header without end"
    stripped = blob.strip()
    if not stripped:
        return "empty diff"
    if (
        re.search(r"(?m)^diff --git ", stripped)
        and "@@" not in stripped
        and "/dev/null" not in stripped
    ):
        return "diff header without hunks"
    if re.search(r"(?m)^(?:--- |\+\+\+ )", stripped) and "@@" not in stripped:
        return "file header without hunks"
    if stripped.rstrip().endswith("@@"):
        return "hunk cut off"
    return None


def parse_planned_paths(spec: str) -> list[str]:
    """Paths listed under 'Files to write' (or Files:/Paths:) in the architect spec."""
    text = spec or ""
    match = _FILES_HEADING.search(text) or _FILES_LABEL.search(text)
    if not match:
        return []
    rest = text[match.end() :]
    next_h = re.search(r"(?m)^#{1,6}\s+\S", rest)
    block = rest[: next_h.start()] if next_h else rest
    paths: list[str] = []
    seen: set[str] = set()
    for line in block.splitlines():
        found = _PATH_ITEM.match(line)
        raw = found.group(1) if found else line.strip().strip("`")
        if not found and not _BARE_PATH.match(raw):
            continue
        path = raw.replace("\\", "/").lstrip("./")
        if path in seen:
            continue
        seen.add(path)
        paths.append(path)
    return paths


def stitch_continuation(prefix: str, extra: str) -> str:
    """Append a continuation, dropping a repeated tail overlap."""
    prefix = prefix or ""
    extra = extra or ""
    if not extra:
        return prefix
    max_check = min(len(prefix), len(extra), 4000)
    overlap = 0
    for n in range(max_check, 19, -1):
        if prefix.endswith(extra[:n]):
            overlap = n
            break
    return prefix + extra[overlap:]


def safe_relpath(raw: str, root: Path) -> Path:
    """Resolve a diff path under root. Reject traversal and absolute escapes."""
    text = (raw or "").strip().replace("\\", "/")
    if text.startswith("a/") or text.startswith("b/"):
        text = text[2:]
    if text in {"/dev/null", "dev/null"}:
        raise PatchError("diff path is /dev/null")
    if text.startswith("~") or text.startswith("/"):
        raise PatchError(f"absolute path rejected: {raw}")
    if ":" in text and text[1:3] == ":/":
        raise PatchError(f"absolute path rejected: {raw}")
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise PatchError(f"path traversal rejected: {raw}")
    dest = (root / text).resolve()
    root_r = root.resolve()
    try:
        dest.relative_to(root_r)
    except ValueError as exc:
        raise PatchError(f"path traversal rejected: {raw}") from exc
    return dest


def write_workspace_files(
    files: dict[str, str],
    *,
    root: Path | None = None,
    dry_run: bool = False,
) -> str:
    """Write accepted path → body pairs under root. Rejects traversal and truncated dumps."""
    root_r = (root or Path.cwd()).resolve()
    written: list[str] = []
    for raw, body in (files or {}).items():
        report = dump_is_truncated(body or "", path=raw)
        if report:
            raise PatchError(f"not writing {raw}: {'; '.join(report.reasons)}")
        dest = safe_relpath(str(raw), root_r)
        text = body if (body or "").endswith("\n") else (body or "") + "\n"
        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")
        written.append(str(raw).replace("\\", "/"))
    if not written:
        raise PatchError("no files to write.")
    mode = "dry-run" if dry_run else "wrote"
    return f"{mode}: {', '.join(written)}"


def diff_target_paths(diff: str) -> list[str]:
    """Relative paths a unified diff would write (b/ side)."""
    found: list[str] = []
    seen: set[str] = set()
    for match in _DIFF_GIT.finditer(diff or ""):
        path = match.group(2).strip()
        if path and path not in seen and path != "/dev/null":
            seen.add(path)
            found.append(path)
    for match in _PLUS_PLUS.finditer(diff or ""):
        path = match.group(1).strip()
        if path in {"/dev/null", "dev/null"}:
            continue
        if path and path not in seen:
            seen.add(path)
            found.append(path)
    if found:
        return found
    for match in _MINUS_MINUS.finditer(diff or ""):
        path = match.group(1).strip()
        if path in {"/dev/null", "dev/null"}:
            continue
        if path and path not in seen:
            seen.add(path)
            found.append(path)
    return found


def apply_unified_diff(
    diff: str,
    *,
    root: Path,
    dry_run: bool = False,
) -> str:
    """Apply a unified diff under root. Uses `patch` when present, else hunk parse."""
    blob = _strip_diff_fences(diff or "")
    hint = patch_looks_truncated(blob)
    if hint:
        raise PatchError(f"Refusing to apply a truncated dump ({hint}).")
    root_r = Path(root).resolve()
    if not root_r.is_dir():
        raise PatchError(f"workspace root is not a directory: {root}")
    paths = diff_target_paths(blob)
    if not paths:
        raise PatchError("diff names no files to apply.")
    for path in paths:
        safe_relpath(path, root_r)
    try:
        return _apply_with_hunks(blob, root_r, dry_run=dry_run)
    except PatchError:
        if shutil.which("patch") and not dry_run:
            return _apply_with_patch(blob, root_r, dry_run=False)
        raise


def _strip_diff_fences(diff: str) -> str:
    text = diff.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:diff|patch)?\s*\n", "", text)
        text = re.sub(r"\n```\s*$", "", text)
    return text


def _apply_with_patch(diff: str, root: Path, *, dry_run: bool) -> str:
    argv = ["patch", "-p1", "-d", str(root), "-t", "-N"]
    try:
        proc = subprocess.run(
            argv,
            input=diff,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise PatchError("patch timed out.") from exc
    out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    if proc.returncode != 0:
        raise PatchError(out or f"patch exited {proc.returncode}")
    files = ", ".join(diff_target_paths(diff)) or "(unknown)"
    mode = "dry-run" if dry_run else "applied"
    return f"{mode}: {files}\n{out}".rstrip() + "\n"


def _apply_with_hunks(diff: str, root: Path, *, dry_run: bool) -> str:
    files = _parse_unified_files(diff)
    if not files:
        raise PatchError("could not parse unified diff hunks.")
    written: list[str] = []
    for rel, old_name, hunks in files:
        dest = safe_relpath(rel, root)
        original = ""
        if dest.is_file():
            original = dest.read_text(encoding="utf-8")
        elif old_name not in {"/dev/null", "dev/null"} and dest.exists() is False:
            # new file from /dev/null is fine; missing existing file is not
            if any(h.old_count > 0 for h in hunks) and old_name not in {"/dev/null", "dev/null"}:
                if not any(h.old_start == 0 or h.old_count == 0 for h in hunks):
                    raise PatchError(f"missing file for patch: {rel}")
        new_text = _apply_hunks_to_text(original, hunks)
        written.append(rel)
        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(new_text, encoding="utf-8")
    mode = "dry-run" if dry_run else "applied"
    return f"{mode}: {', '.join(written)}\n"


@dataclass
class _Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[str]


def _parse_unified_files(diff: str) -> list[tuple[str, str, list[_Hunk]]]:
    """Return (b_path, a_path, hunks) for each file in the diff."""
    lines = (diff or "").splitlines()
    files: list[tuple[str, str, list[_Hunk]]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("diff --git ") or line.startswith("--- "):
            a_path = "/dev/null"
            b_path = ""
            if line.startswith("diff --git "):
                match = _DIFF_GIT.match(line)
                if match:
                    a_path, b_path = match.group(1), match.group(2)
                i += 1
                while (
                    i < len(lines)
                    and not lines[i].startswith("--- ")
                    and not lines[i].startswith("diff --git ")
                ):
                    i += 1
            if i < len(lines) and lines[i].startswith("--- "):
                a_path = lines[i][4:].strip()
                if a_path.startswith("a/"):
                    a_path = a_path[2:]
                i += 1
            if i < len(lines) and lines[i].startswith("+++ "):
                b_path = lines[i][4:].strip()
                if b_path.startswith("b/"):
                    b_path = b_path[2:]
                i += 1
            hunks: list[_Hunk] = []
            while i < len(lines) and lines[i].startswith("@@ "):
                header = _HUNK_HEADER.match(lines[i])
                if not header:
                    break
                old_start = int(header.group(1))
                old_count = int(header.group(2) or "1")
                new_start = int(header.group(3))
                new_count = int(header.group(4) or "1")
                i += 1
                body: list[str] = []
                while i < len(lines):
                    row = lines[i]
                    if (
                        row.startswith("@@ ")
                        or row.startswith("diff --git ")
                        or row.startswith("--- ")
                    ):
                        break
                    if row.startswith("\\"):
                        i += 1
                        continue
                    if row[:1] in {" ", "+", "-"} or row == "":
                        body.append(row)
                        i += 1
                        continue
                    break
                hunks.append(_Hunk(old_start, old_count, new_start, new_count, body))
            rel = b_path if b_path and b_path != "/dev/null" else a_path
            if rel and rel != "/dev/null" and hunks:
                files.append((rel, a_path, hunks))
            continue
        i += 1
    return files


def _apply_hunks_to_text(original: str, hunks: list[_Hunk]) -> str:
    src = original.splitlines(keepends=True)
    if original and not original.endswith("\n"):
        # splitlines(keepends=True) keeps the last line without newline
        pass
    out: list[str] = []
    cursor = 0
    for hunk in hunks:
        start = max(hunk.old_start - 1, 0)
        if start > len(src):
            raise PatchError("hunk starts past end of file")
        if start < cursor:
            raise PatchError("hunks overlap or are out of order")
        out.extend(src[cursor:start])
        cursor = start
        for raw in hunk.lines:
            if raw.startswith(" "):
                expected = _with_newline(raw[1:])
                if cursor >= len(src) or _strip_nl(src[cursor]) != _strip_nl(expected):
                    raise PatchError("context line did not match")
                out.append(src[cursor])
                cursor += 1
            elif raw.startswith("-"):
                expected = _with_newline(raw[1:])
                if cursor >= len(src) or _strip_nl(src[cursor]) != _strip_nl(expected):
                    raise PatchError("removed line did not match")
                cursor += 1
            elif raw.startswith("+"):
                out.append(_with_newline(raw[1:]))
            elif raw == "":
                continue
            else:
                raise PatchError(f"unknown hunk line: {raw[:40]!r}")
    out.extend(src[cursor:])
    text = "".join(out)
    if original.endswith("\n") and text and not text.endswith("\n"):
        text += "\n"
    return text


def _with_newline(line: str) -> str:
    if line.endswith("\n"):
        return line
    return line + "\n"


def _strip_nl(line: str) -> str:
    return line[:-1] if line.endswith("\n") else line


def _looks_like_html_document(text: str) -> bool:
    head = (text or "").lstrip()[:240].lower()
    return "<!doctype" in head or head.startswith("<html")


def _html_dump_truncated(text: str) -> bool:
    if not _looks_like_html_document(text):
        return False
    return truncation_reason_for_body("dump.html", text) is not None


def _ends_on_open_hunk(stripped: str) -> bool:
    if "@@" not in stripped:
        return False
    last = stripped.rsplit("@@", 1)[-1]
    return "\n+" not in last and "\n-" not in last and "\n " not in last and last.strip() == ""
