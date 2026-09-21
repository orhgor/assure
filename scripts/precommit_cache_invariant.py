#!/usr/bin/env python3
"""Pre-commit guard: a pipeline-cache write must not lie about its project.

`pipeline_cache.project_id` references `projects(id)`. Two ways a write can go
wrong silently, both of them measured on this repository:

  1. A sentinel fallback — `save_pipeline_cache(key, project_id or "entailment", …)`.
     The literal is not a project. On a database that declares the foreign key the
     write is rejected and the cache entry is dropped; on one that does not, the row
     is stored claiming a project that does not exist. Both are lies about
     provenance, and neither is visible as a failure.

  2. A bare `except Exception` around the call. It swallows the rejection AND the
     broken connection the uncommitted statement left behind: the next write on the
     same connection then failed `database is locked` (measured 2026-09-19). Only a
     foreign-key rejection is a rejected write; anything else is a bug and must
     propagate.

Exit 1 with the offending file:line on stderr. No dependencies beyond the stdlib.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

SAVE = "save_pipeline_cache"
ROOT = Path("prompt_matrix")


def _calls_save(node: ast.AST) -> list[ast.Call]:
    return [
        n
        for n in ast.walk(node)
        if isinstance(n, ast.Call)
        and (
            (isinstance(n.func, ast.Name) and n.func.id == SAVE)
            or (isinstance(n.func, ast.Attribute) and n.func.attr == SAVE)
        )
    ]


def _project_id_argument(call: ast.Call) -> ast.expr | None:
    if len(call.args) >= 2:
        return call.args[1]
    for kw in call.keywords:
        if kw.arg == "project_id":
            return kw.value
    return None


def _is_fallback(node: ast.expr) -> bool:
    """`a or b` — the fallback shape the invariant forbids."""
    return isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or)


def _swallows_everything(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:  # bare `except:`
        return True
    names: list[str] = []
    for exc in handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]:
        if isinstance(exc, ast.Name):
            names.append(exc.id)
        elif isinstance(exc, ast.Attribute):
            names.append(exc.attr)
    return "Exception" in names or "BaseException" in names


def check_file(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    problems: list[str] = []

    for call in _calls_save(tree):
        arg = _project_id_argument(call)
        if arg is not None and _is_fallback(arg):
            problems.append(
                f"{path}:{call.lineno}: the project_id argument to {SAVE} is an `or` "
                f"fallback. A sentinel literal is not a project: a cache write must "
                f"reference a real `projects` row or NULL."
            )

    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        guarded = [c for c in _calls_save(ast.Module(body=node.body, type_ignores=[]))]
        if not guarded:
            continue
        for handler in node.handlers:
            if _swallows_everything(handler):
                problems.append(
                    f"{path}:{handler.lineno}: `except Exception` around {SAVE} at "
                    f"line {guarded[0].lineno}. Narrow it to sqlite3.IntegrityError "
                    f"(a rejected write, counted) so every other failure propagates."
                )

    return problems


def main(argv: list[str]) -> int:
    targets = [Path(a) for a in argv[1:]] or sorted(ROOT.rglob("*.py"))
    problems: list[str] = []
    for path in targets:
        if path.suffix == ".py" and path.is_file():
            problems.extend(check_file(path))
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
