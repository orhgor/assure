"""Local compile-time checks for swarm dumps. No live APIs, no Sends."""

from __future__ import annotations

import ast
import importlib.util
import re
import subprocess
import sys
import sysconfig
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

try:
    from .patch_apply import dump_is_truncated, parse_planned_paths
except ImportError:
    from patch_apply import dump_is_truncated, parse_planned_paths

_REQ_NAME = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")
_PYPROJECT_DEP = re.compile(r'"([A-Za-z0-9][A-Za-z0-9._-]*)(?:[<>=!~]|\[|")')
_IMPORT_ALIASES = {
    "dotenv": "python-dotenv",
    "flask_httpauth": "flask-httpauth",
    "flask_babel": "flask-babel",
    "yaml": "pyyaml",
    "PIL": "pillow",
    "cv2": "opencv-python",
    "bs4": "beautifulsoup4",
    "sklearn": "scikit-learn",
    "attr": "attrs",
    "rest_framework": "djangorestframework",
}
_STDLIB = set(getattr(sys, "stdlib_module_names", ())) | set(
    sys.builtin_module_names
)
_FIRST_PARTY = frozenset({"prompt_matrix"})


@dataclass
class LintReport:
    ok: bool
    errors: list[str] = field(default_factory=list)

    def text(self) -> str:
        if self.ok:
            return "Local lint: PASS (py_compile, SQL, imports, patch integrity)."
        lines = ["Local lint: FAIL"]
        lines.extend(f"{i}. {item}" for i, item in enumerate(self.errors, 1))
        return "\n".join(lines)


def repo_root() -> Path:
    here = Path(__file__).resolve().parent.parent
    if (here / "pyproject.toml").is_file():
        return here
    return Path.cwd()


def local_lint(
    files: dict[str, str],
    *,
    spec: str = "",
    implementation: str = "",
    diffs: dict[str, str] | None = None,
    root: Path | None = None,
) -> LintReport:
    """py_compile, SQL paren check, third-party imports, patch integrity.

    Does not call providers, Flask routes, Supabase, Stripe, or ``pem --direct``.
    """
    errors: list[str] = []
    files = {str(path).replace("\\", "/"): body for path, body in (files or {}).items()}
    base = root or repo_root()
    if dump_is_truncated(implementation or ""):
        errors.append(
            "implementation dump looks truncated. Re-request the patch; do not Keep."
        )
    for path, body in files.items():
        cut = dump_is_truncated(body, path=path)
        if cut:
            errors.append(f"{path}: truncated dump ({'; '.join(cut.reasons)})")
    errors.extend(_patch_integrity(files, spec=spec, diffs=diffs))
    errors.extend(_py_compile_files(files))
    errors.extend(_sql_files(files))
    errors.extend(_import_files(files, root=base))
    return LintReport(ok=not errors, errors=errors)


def _patch_integrity(
    files: dict[str, str],
    *,
    spec: str,
    diffs: dict[str, str] | None,
) -> list[str]:
    errors: list[str] = []
    planned = parse_planned_paths(spec or "")
    if planned:
        missing = [path for path in planned if not _has_path(files, path)]
        if missing:
            errors.append(
                "empty patch: planned files not in the dump: " + ", ".join(missing)
            )
    if files and diffs is not None and not diffs:
        errors.append("empty patch: extracted files did not change any lines")
    return errors


def _has_path(files: dict[str, str], path: str) -> bool:
    needle = Path(path).as_posix()
    for key in files:
        posix = Path(key).as_posix()
        if posix == needle or Path(key).name == Path(path).name:
            return True
    return False


def _py_compile_files(files: dict[str, str]) -> list[str]:
    errors: list[str] = []
    for path, body in files.items():
        if not path.endswith(".py"):
            continue
        with tempfile.TemporaryDirectory(prefix="pem-lint-") as tmp:
            dest = Path(tmp) / Path(path).name
            dest.write_text(body or "", encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, "-m", "py_compile", str(dest)],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "py_compile failed").strip()
            errors.append(f"{path}: py_compile failed\n{detail}")
    return errors


def _sql_files(files: dict[str, str]) -> list[str]:
    errors: list[str] = []
    for path, body in files.items():
        if not path.lower().endswith(".sql"):
            continue
        if not _sql_parens_ok(body or ""):
            errors.append(f"{path}: mismatched parentheses in SQL")
    return errors


def _sql_parens_ok(text: str) -> bool:
    depth = 0
    in_single = False
    i = 0
    blob = text or ""
    while i < len(blob):
        char = blob[i]
        if in_single:
            if char == "'":
                if i + 1 < len(blob) and blob[i + 1] == "'":
                    i += 2
                    continue
                in_single = False
            i += 1
            continue
        if char == "'":
            in_single = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return False
        i += 1
    return depth == 0 and not in_single


def _import_files(files: dict[str, str], *, root: Path) -> list[str]:
    declared = _declared_packages(root, files)
    local_mods = {Path(path).stem for path in files if path.endswith(".py")}
    errors: list[str] = []
    for path, body in files.items():
        if not path.endswith(".py"):
            continue
        try:
            tree = ast.parse(body or "")
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level and not node.module:
                    continue
                if node.level:
                    continue
                if node.module:
                    names = [node.module.split(".")[0]]
            for name in names:
                if _import_ok(name, declared=declared, local_mods=local_mods, root=root):
                    continue
                errors.append(
                    f"{path}: third-party import {name!r} is not in requirements.txt "
                    "or pyproject.toml"
                )
    return errors


def _import_ok(
    name: str,
    *,
    declared: set[str],
    local_mods: set[str],
    root: Path,
) -> bool:
    if not name or name == "__future__":
        return True
    if name in _STDLIB or name in _FIRST_PARTY or name in local_mods:
        return True
    if (root / "prompt_matrix" / f"{name}.py").is_file():
        return True
    if (root / "prompt_matrix" / name / "__init__.py").is_file():
        return True
    key = _norm(name)
    alias = _IMPORT_ALIASES.get(name, name)
    if key in declared or _norm(alias) in declared:
        return True
    spec = importlib.util.find_spec(name)
    if spec is not None:
        origin = getattr(spec, "origin", None) or ""
        if origin and _is_site_package(origin):
            return True
        if spec.parent or spec.submodule_search_locations:
            return True
        if origin in {"frozen", "built-in"}:
            return True
    return False


def _is_site_package(origin: str) -> bool:
    pure = sysconfig.get_path("purelib") or ""
    plat = sysconfig.get_path("platlib") or ""
    return origin.startswith(pure) or origin.startswith(plat)


def _declared_packages(root: Path, files: dict[str, str]) -> set[str]:
    names: set[str] = set()
    for path in (
        root / "requirements.txt",
        root / "prompt_matrix" / "requirements.txt",
        root / "pyproject.toml",
    ):
        if path.is_file():
            names.update(_parse_dep_blob(path.read_text(encoding="utf-8", errors="replace")))
    for path, body in files.items():
        lower = path.replace("\\", "/").lower()
        if lower.endswith("requirements.txt") or lower.endswith("pyproject.toml"):
            names.update(_parse_dep_blob(body or ""))
    return names


def _parse_dep_blob(text: str) -> set[str]:
    names: set[str] = set()
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("["):
            continue
        if line.startswith('"') or line.startswith("'"):
            match = _PYPROJECT_DEP.match(line)
            if match:
                names.add(_norm(match.group(1)))
            continue
        match = _REQ_NAME.match(line)
        if match:
            names.add(_norm(match.group(1)))
    for match in _PYPROJECT_DEP.finditer(text or ""):
        names.add(_norm(match.group(1)))
    return names


def _norm(name: str) -> str:
    return name.lower().replace("-", "_").replace(".", "_")
