"""2D landing patch — unique-anchor string replacements on the served tree.

Applied ON the box, never a whole-file push from a worktree: four agents have
landed in prototype/shell.js today, so the file's line numbers and bytes are not
stable and a full-file copy would revert someone else's work. Every anchor below
must appear exactly once or the run aborts with nothing written.

prints: <path> <md5 before> -> <md5 after>   (nothing else)
"""

from __future__ import annotations

import hashlib
import pathlib
import sys

ROOT = pathlib.Path("/home/ubuntu/assure-prototype")

EXPORT_COMMENT_OLD = """\
    // Export (top bar) → audit PDF download for the active project. Disabled
    // until _canExport() (see _syncExportEnabled), which the document paths
    // re-evaluate; the click re-reads the same state instead of dead-ending in
    // a console.warn."""

EXPORT_COMMENT_NEW = """\
    // Export (top bar) → the export pair for the active project: the audit PDF
    // and the .jdf sidecar that carries the provenance, each node's verification
    // state, the source manifest, the version chain and the drafting model.
    // Disabled until _canExport() (see _syncExportEnabled), which the document
    // paths re-evaluate; the click re-reads the same state instead of dead-ending
    // in a console.warn."""

EXPORT_HREF_OLD = """\
        window.location.href =
          "/api/projects/" + encodeURIComponent(_exportProjectId()) + "/export?format=audit-pdf";"""

EXPORT_HREF_NEW = """\
        // 2D.1: one download holding both files, so the readable dossier and the
        // verifiable one cannot be separated. `format=jdf` serves the sidecar alone.
        window.location.href =
          "/api/projects/" + encodeURIComponent(_exportProjectId()) + "/export?format=bundle";"""

ROUTE_COMMENT_OLD = """\
    // ROUTED TO — the model this compile was routed to, taken from the draft
    // stream itself. /api/compile-system answers {prompt} only (web.py:1180-1187),
    // so it can supply no runtime fact."""

ROUTE_COMMENT_NEW = """\
    // ROUTED TO — the model this compile was routed to, taken from the draft
    // stream itself. /api/compile-system answers the system message and the answer
    // shape, not the model, so it can supply no runtime fact."""

COMPILE_SYSTEM_CALL_OLD = """\
      // Preview + draft stream run in parallel; do not wait for preview.
      // /api/compile-system takes no body and answers {prompt} (web.py).
      jsonPost("/api/compile-system", {})"""

COMPILE_SYSTEM_CALL_NEW = """\
      // Preview + draft stream run in parallel; do not wait for preview.
      // The ask goes with it: the compile system message is static except for the
      // answer-shape block, which follows the ask (web.py, services/answer_shape).
      jsonPost("/api/compile-system", { intent: raw })"""

WEB_VIEW_OLD = '''\
    @app.post("/api/compile-system")
    def compile_system_view():
        """Static system message the compile path sends. Consumed by
        the prototype shell panel so it can show the model what the
        model receives. No body, no params; always the same string."""
        from .routers.draft import _COMPILE_SYSTEM

        return jsonify({"prompt": _COMPILE_SYSTEM})'''

WEB_VIEW_NEW = '''\
    @app.post("/api/compile-system")
    def compile_system_view():
        """The system message the compile path sends, for the ask it is compiling.

        Consumed by the prototype shell panel so it can show the model what the
        model receives. The message is static except for the answer-shape block
        (services/answer_shape), which follows the ask — so a body carrying the ask
        returns the prompt the pipeline builds for it, and a body carrying nothing
        returns the static message, unchanged."""
        from .routers.draft import _COMPILE_SYSTEM, _compile_system
        from .services.answer_shape import choose_shape

        data = request.get_json(silent=True) or {}
        intent = str(data.get("intent") or "").strip()
        if not intent:
            return jsonify({"prompt": _COMPILE_SYSTEM})
        shape = choose_shape(intent)
        return jsonify({"prompt": _compile_system(shape), "answer_shape": shape})'''

PATCHES: dict[str, list[tuple[str, str]]] = {
    "prototype/shell.js": [
        (EXPORT_COMMENT_OLD, EXPORT_COMMENT_NEW),
        (EXPORT_HREF_OLD, EXPORT_HREF_NEW),
        (ROUTE_COMMENT_OLD, ROUTE_COMMENT_NEW),
        (COMPILE_SYSTEM_CALL_OLD, COMPILE_SYSTEM_CALL_NEW),
    ],
    "prompt_matrix/web.py": [(WEB_VIEW_OLD, WEB_VIEW_NEW)],
}


def main() -> int:
    for rel, pairs in PATCHES.items():
        path = ROOT / rel
        before = hashlib.md5(path.read_bytes()).hexdigest()
        text = path.read_text(encoding="utf-8")
        for index, (old, new) in enumerate(pairs, start=1):
            found = text.count(old)
            if found != 1:
                print(f"ABORT {rel} anchor {index}: found {found} times, expected 1")
                print(old.splitlines()[0][:120])
                return 1
            text = text.replace(old, new)
        path.write_text(text, encoding="utf-8")
        after = hashlib.md5(path.read_bytes()).hexdigest()
        print(f"{rel} {before} -> {after}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
