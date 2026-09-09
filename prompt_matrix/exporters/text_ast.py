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
    if ntype in ("code_block", "codeBlock"):
        return str(node.get("content") or "").strip()
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
            if ntype in ("code_block", "codeBlock"):
                lang = str(child.get("language") or child.get("lang") or "text").strip() or "text"
                code = str(child.get("content") or "")
                lines.append("```" + lang)
                if code:
                    lines.append(code)
                lines.append("```")
                lines.append("")
                continue
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
        "<style>body{font-family:Georgia,serif;max-width:42rem;margin:2rem auto;padding:0 1.25rem;line-height:1.6;color:#1b1f24}h1{color:#1A4B8C}blockquote{border-left:4px solid #1A4B8C;margin:1rem 0;padding-left:1rem;color:#334}pre{background:#1e293b;color:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:16px;overflow-x:auto;font-family:'JetBrains Mono','Fira Code',monospace;font-size:0.85rem;position:relative}pre code{background:transparent;color:inherit;font-family:inherit;white-space:pre-wrap}.code-lang-label{position:absolute;top:8px;right:12px;font-size:0.65rem;font-weight:600;color:#94a3b8;background:#334155;padding:2px 10px;border-radius:4px;letter-spacing:0.05em}</style>",
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
            if ntype in ("code_block", "codeBlock"):
                lang = str(child.get("language") or child.get("lang") or "text").strip() or "text"
                code = html.escape(str(child.get("content") or ""))
                parts.append(
                    f'<pre><span class="code-lang-label">{html.escape(lang)}</span>'
                    f'<code class="language-{html.escape(lang, quote=True)}">{code}</code></pre>'
                )
                continue
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
