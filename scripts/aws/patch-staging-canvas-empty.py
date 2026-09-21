#!/usr/bin/env python3
"""Patch staging templates for compare-pane shell + assets."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "prompt_matrix" / "templates" / "index.html"
BASE = ROOT / "prompt_matrix" / "templates" / "base.html"

COMPARE_SHELL = """<aside class="founder-staging-pane" aria-label="{{ gettext('AI Staging Area') }}">
                  <p id="compare-pane-empty" class="compare-pane-empty hint" data-i18n="founder.compare.empty">{{ gettext('Run Investigate (⌘K) to compare two models side-by-side.') }}</p>
                  <div id="compare-pane" class="compare-pane-shell hidden" hidden aria-hidden="true" role="region" aria-label="{{ gettext('Compare models') }}">
                    <div class="staging-canvas"></div>
                  </div>
                </aside>"""

LEGACY_PATTERN = re.compile(
    r'<aside class="founder-staging-pane"[^>]*>\s*'
    r'<div class="staging-canvas">[\s\S]*?</div>\s*'
    r"</aside>",
    re.MULTILINE,
)


def patch_index() -> bool:
    if not INDEX.is_file():
        print(f"missing {INDEX}", file=sys.stderr)
        return False
    text = INDEX.read_text(encoding="utf-8")
    changed = False
    if 'id="compare-pane"' not in text:
        new_text, count = LEGACY_PATTERN.subn(COMPARE_SHELL, text, count=1)
        if count != 1:
            print("founder-staging-pane block not found in index.html", file=sys.stderr)
            return False
        text = new_text
        changed = True
        print("patched compare-pane shell in index.html")
    if "compare_pane.js" not in text:
        needle = "orchestrator.js"
        insert = "<script src=\"{{ url_for('static', filename='compare_pane.js') }}?v={{ js_version|default('assure-21') }}\"></script>\n  "
        if needle not in text:
            print("orchestrator.js script tag not found", file=sys.stderr)
            return False
        text = text.replace(
            "<script src=\"{{ url_for('static', filename='orchestrator.js') }}?v={{ js_version|default('assure-21') }}\"></script>\n",
            "<script src=\"{{ url_for('static', filename='orchestrator.js') }}?v={{ js_version|default('assure-21') }}\"></script>\n  "
            + insert,
            1,
        )
        changed = True
        print("patched compare_pane.js script in index.html")
    if changed:
        INDEX.write_text(text, encoding="utf-8")
    else:
        print("index.html already patched")
    return True


def patch_base() -> bool:
    if not BASE.is_file():
        print(f"missing {BASE}", file=sys.stderr)
        return False
    text = BASE.read_text(encoding="utf-8")
    if "compare_pane.css" in text:
        print("base.html already has compare_pane.css")
        return True
    needle = "<link rel=\"stylesheet\" href=\"{{ url_for('static', filename='style.css') }}?v={{ css_version|default('assure-30') }}\">"
    insert = (
        needle
        + "\n  <link rel=\"stylesheet\" href=\"{{ url_for('static', filename='compare_pane.css') }}?v={{ css_version|default('assure-30') }}\">"
    )
    if needle not in text:
        print("style.css link not found in base.html", file=sys.stderr)
        return False
    BASE.write_text(text.replace(needle, insert, 1), encoding="utf-8")
    print("patched compare_pane.css link in base.html")
    return True


def main() -> int:
    ok = patch_index() and patch_base()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
