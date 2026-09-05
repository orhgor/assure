"""Plain Markdown and HTML export from a JDF AST dict."""

from __future__ import annotations

import html
from typing import Any


def _title(tree: dict[str, Any]) -> str:
    meta = tree.get("meta") or {}
    return str(
        meta.get("title") or meta.get("project_id") or tree.get("document_id") or "Assure Document"
    )


def _node_text(node: dict[str, Any]) -> str:
    ntype = str(node.get("type") or "paragraph")
    if ntype == "callout":
        title = str(node.get("title") or "").strip()
        body = str(node.get("content") or "").strip()
        if title and body:
            return title + "\n" + body
        return title or body
    if ntype == "table":
        caption = str(node.get("caption") or node.get("content") or "").strip()
        return caption or "Table"
    if ntype == "image":
        alt = str(node.get("alt") or "").strip()
        caption = str(node.get("caption") or "").strip()
        return caption or alt or "Image"
    return str(node.get("content") or "").strip()


def jdf_to_markdown(tree: dict[str, Any]) -> str:
    """Turn a JDF document into GitHub-flavored Markdown."""
    lines: list[str] = ["# " + _title(tree), ""]
    for section in tree.get("body") or []:
        if not isinstance(section, dict):
            continue
        lines.append("## " + str(section.get("title") or "Section"))
        lines.append("")
        for child in section.get("children") or []:
            if not isinstance(child, dict):
                continue
            ntype = str(child.get("type") or "paragraph")
            if ntype == "image" and child.get("src"):
                alt = str(child.get("alt") or "Image").strip()
                cap = str(child.get("caption") or "").strip()
                line = f"![{alt}]({child.get('src')})"
                if cap:
                    line += f"\n*{cap}*"
                lines.append(line)
                lines.append("")
                continue
            text = _node_text(child)
            if not text:
                continue
            if ntype == "callout":
                for raw in text.splitlines() or [text]:
                    lines.append("> " + raw)
                lines.append("")
            else:
                lines.append(text)
                lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def jdf_to_html(tree: dict[str, Any]) -> str:
    """Turn a JDF document into a standalone HTML page."""
    title = _title(tree)
    parts: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        f"<title>{html.escape(title)}</title>",
        "<style>body{font-family:Georgia,serif;max-width:42rem;margin:2rem auto;padding:0 1.25rem;line-height:1.6;color:#1b1f24}h1{color:#1A4B8C}blockquote{border-left:4px solid #1A4B8C;margin:1rem 0;padding-left:1rem;color:#334}</style>",
        "</head>",
        "<body>",
        f"<h1>{html.escape(title)}</h1>",
    ]
    for section in tree.get("body") or []:
        if not isinstance(section, dict):
            continue
        parts.append(f"<h2>{html.escape(str(section.get('title') or 'Section'))}</h2>")
        for child in section.get("children") or []:
            if not isinstance(child, dict):
                continue
            ntype = str(child.get("type") or "paragraph")
            if ntype == "image" and child.get("src"):
                alt = html.escape(str(child.get("alt") or "Image"))
                src = str(child.get("src") or "")
                if src.startswith("data:") or src.startswith("/"):
                    parts.append(f'<figure><img src="{src}" alt="{alt}" style="max-width:100%"/>')
                    cap = str(child.get("caption") or "").strip()
                    if cap:
                        parts.append(f"<figcaption>{html.escape(cap)}</figcaption>")
                    parts.append("</figure>")
                continue
            text = _node_text(child)
            if not text:
                continue
            escaped = html.escape(text).replace("\n", "<br>\n")
            if ntype == "callout":
                parts.append(f"<blockquote><p>{escaped}</p></blockquote>")
            else:
                parts.append(f"<p>{escaped}</p>")
    parts.extend(["</body>", "</html>", ""])
    return "\n".join(parts)
