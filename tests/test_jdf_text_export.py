"""Markdown and HTML export from JDF."""

from prompt_matrix.exporters.text_ast import jdf_to_html, jdf_to_markdown


SAMPLE = {
    "meta": {"title": "Q3 Note"},
    "body": [
        {
            "type": "section",
            "title": "Summary",
            "children": [
                {"type": "paragraph", "content": "Revenue reached 4.2M."},
                {"type": "callout", "title": "Watch", "content": "Burn is 220k."},
            ],
        }
    ],
}


def test_jdf_to_markdown_headings_and_callout() -> None:
    md = jdf_to_markdown(SAMPLE)
    assert md.startswith("# Q3 Note")
    assert "## Summary" in md
    assert "Revenue reached 4.2M." in md
    assert "> Watch" in md
    assert "> Burn is 220k." in md


def test_jdf_to_html_escapes() -> None:
    tree = {
        "meta": {"title": "A <B>"},
        "body": [
            {
                "type": "section",
                "title": "Sec",
                "children": [{"type": "paragraph", "content": "x < y & z"}],
            }
        ],
    }
    out = jdf_to_html(tree)
    assert "<title>A &lt;B&gt;</title>" in out
    assert "x &lt; y &amp; z" in out
    assert "<script>" not in out
