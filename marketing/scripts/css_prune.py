"""Remove CSS rules whose every selector names a retired class. Usage: css_prune.py FILE TOKEN [TOKEN ...]"""
import sys
from pathlib import Path


def split_items(css):
    """Yield (prelude, body, raw) for each top-level block, and ('', None, raw) for text between blocks."""
    i, n, out, start = 0, len(css), [], 0
    while i < n:
        if css.startswith("/*", i):
            j = css.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if css[i] == "{":
            pre_start = start
            depth, j = 1, i + 1
            while j < n and depth:
                if css.startswith("/*", j):
                    k = css.find("*/", j + 2)
                    j = n if k < 0 else k + 2
                    continue
                depth += {"{": 1, "}": -1}.get(css[j], 0)
                j += 1
            raw = css[pre_start:j]
            # split leading whitespace/comments from the prelude
            pre = css[pre_start:i]
            out.append((pre, css[i + 1:j - 1], raw))
            start = i = j
            continue
        i += 1
    out.append(("", None, css[start:]))
    return out


def selector_text(pre):
    s = pre
    while "/*" in s:
        a = s.find("/*")
        b = s.find("*/", a + 2)
        s = s[:a] + s[b + 2 if b >= 0 else len(s):]
    return s.strip()


def prune(css, tokens, removed):
    res = []
    for pre, body, raw in split_items(css):
        if body is None:
            res.append(raw)
            continue
        sel = selector_text(pre)
        if sel.startswith("@media") or sel.startswith("@supports"):
            inner = prune(body, tokens, removed)
            if inner.strip():
                res.append(pre + "{" + inner + "}")
            else:
                removed.append(sel)
            continue
        parts = [p.strip() for p in sel.split(",") if p.strip()]
        if parts and all(any(t in p for t in tokens) for p in parts):
            removed.append(sel)
            continue
        res.append(raw)
    return "".join(res)


if __name__ == "__main__":
    path, tokens = Path(sys.argv[1]), sys.argv[2:]
    css = path.read_text(encoding="utf-8")
    if css.count("{") != css.count("}"):
        sys.exit("STOP: unbalanced braces before pruning")
    removed = []
    new = prune(css, tokens, removed)
    if new.count("{") != new.count("}"):
        sys.exit("STOP: unbalanced braces after pruning")
    path.write_text(new, encoding="utf-8")
    for r in removed:
        print("removed:", " ".join(r.split())[:100])
    print(f"{len(removed)} rule(s) removed; {len(css) - len(new)} bytes")
