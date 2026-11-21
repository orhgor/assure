#!/usr/bin/env python3
"""Compare two builds page by page: what a visitor or a crawler sees must not change.

    .venv/bin/python marketing/scripts/compare_dist.py /tmp/omp/dist-before dist

Compares, for every */index.html and 404.html: <title>, meta tags, link tags,
JSON-LD, visible text, link targets and element ids. Whitespace and attribute
order are ignored. Exit 1 and print the first differences when anything differs.
"""
from __future__ import annotations

import json
import re
import sys
from html import unescape
from html.parser import HTMLParser
from pathlib import Path


class Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta, self.links, self.hrefs, self.ids, self.ld, self.text = [], [], [], [], [], []
        self.title = ""
        self._stack: list[str] = []
        self._ld = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta":
            self.meta.append(tuple(sorted(a.items())))
        elif tag == "link":
            self.links.append(tuple(sorted((k, v) for k, v in a.items() if k != "href" or "?v=" not in (v or ""))))
        if tag == "a" and "href" in a:
            self.hrefs.append(a["href"])
        if "id" in a:
            self.ids.append(a["id"])
        self._ld = tag == "script" and a.get("type") == "application/ld+json"
        if tag not in ("meta", "link", "br", "img", "input", "hr", "source", "path", "circle"):
            self._stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self._stack:
            while self._stack and self._stack.pop() != tag:
                pass
        self._ld = False

    def handle_data(self, data):
        cur = self._stack[-1] if self._stack else ""
        if self._ld:
            self.ld.append(json.dumps(json.loads(data), sort_keys=True))
        elif cur == "title":
            self.title += data
        elif cur not in ("script", "style") and data.strip():
            self.text.append(data.strip())


def summary(path: Path) -> dict:
    p = Page()
    p.feed(path.read_text(encoding="utf-8"))
    return {"title": p.title.strip(), "meta": sorted(p.meta), "links": sorted(p.links), "ld": sorted(p.ld),
            "text": re.sub(r"\s+", " ", unescape(" ".join(p.text))), "hrefs": p.hrefs, "ids": sorted(p.ids)}


def main() -> int:
    before, after = Path(sys.argv[1]), Path(sys.argv[2])
    rel = lambda d: {q.relative_to(d).as_posix() for q in list(d.rglob("index.html")) + list(d.glob("404.html"))}
    a, b = rel(before), rel(after)
    problems = [f"only in before: {x}" for x in sorted(a - b)] + [f"only in after: {x}" for x in sorted(b - a)]
    for page in sorted(a & b):
        sa, sb = summary(before / page), summary(after / page)
        for key in sa:
            if sa[key] != sb[key]:
                problems.append(f"{page}: {key} differs")
    for line in problems[:60]:
        print(line)
    print(f"{len(problems)} difference(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
