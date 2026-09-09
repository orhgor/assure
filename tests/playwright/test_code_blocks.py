"""Hybrid AST — TipTap code blocks with Highlight.js CDN."""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from prompt_matrix.exporters.text_ast import jdf_to_html
from tests.playwright.helpers import goto_founder_workbench

pytestmark = pytest.mark.playwright


def _wait_for_editor(page: Page):
    page.wait_for_function(
        "() => document.querySelector('#founder-draft-editor .ProseMirror')",
        timeout=15_000,
    )
    return page.locator("#founder-draft-editor .ProseMirror")


def test_highlightjs_cdn_loaded(page: Page, base_url: str):
    goto_founder_workbench(page, base_url)
    loaded = page.evaluate(
        "() => !!(window.hljs && typeof window.hljs.highlightElement === 'function')"
    )
    assert loaded is True


def test_code_block_creation(page: Page, base_url: str):
    goto_founder_workbench(page, base_url)
    _wait_for_editor(page)
    page.evaluate(
        """() => {
          const ed = window.AssureTiptapEditor && window.AssureTiptapEditor.getEditor();
          if (!ed) return false;
          return ed
            .chain()
            .focus()
            .insertContent({
              type: 'codeBlock',
              attrs: { language: 'python' },
              content: [{ type: 'text', text: "print('hi')" }],
            })
            .run();
        }"""
    )
    page.wait_for_function(
        """() => document.querySelector(
          '#founder-draft-editor .ProseMirror pre code.language-python'
        )""",
        timeout=10_000,
    )
    expect(
        page.locator("#founder-draft-editor .ProseMirror pre code.language-python")
    ).to_be_visible()


def test_code_block_serialization(page: Page, base_url: str):
    goto_founder_workbench(page, base_url)
    _wait_for_editor(page)
    result = page.evaluate(
        """() => {
          const api = window.AssureTiptapEditor;
          const ed = api && api.getEditor && api.getEditor();
          if (!ed) return null;
          ed.chain()
            .focus()
            .insertContent({
              type: 'codeBlock',
              attrs: { language: 'json' },
              content: [{ type: 'text', text: '{\\"ok\\": true}' }],
            })
            .run();
          const jdf = api.tiptapToJdf(ed.getJSON(), { meta: {}, body: [] });
          const child = jdf.body && jdf.body[0] && jdf.body[0].children && jdf.body[0].children[0];
          return child;
        }"""
    )
    assert result is not None
    assert result.get("type") == "code_block"
    assert result.get("language") == "json"
    assert '{"ok": true}' in str(result.get("content") or "")


def test_code_block_pdf_render():
    tree = {
        "meta": {"title": "Code PDF"},
        "body": [
            {
                "type": "section",
                "title": "Snippet",
                "children": [
                    {
                        "type": "code_block",
                        "language": "python",
                        "content": "print('pdf')",
                    }
                ],
            }
        ],
    }
    out = jdf_to_html(tree)
    assert '<pre><span class="code-lang-label">python</span>' in out
    assert '<code class="language-python">' in out
    assert "print(" in out and "pdf" in out


def test_language_badge(page: Page, base_url: str):
    goto_founder_workbench(page, base_url)
    _wait_for_editor(page)
    info = page.evaluate(
        """() => {
          const ed = window.AssureTiptapEditor && window.AssureTiptapEditor.getEditor();
          if (!ed) return { ok: false, reason: 'no-editor' };
          ed.chain()
            .focus()
            .insertContent({
              type: 'codeBlock',
              attrs: { language: 'sql' },
              content: [{ type: 'text', text: 'SELECT 1;' }],
            })
            .run();
          const code = document.querySelector('#founder-draft-editor .ProseMirror pre code');
          if (window.AssureCodeBlocks && window.AssureCodeBlocks.insertLanguageBadges) {
            window.AssureCodeBlocks.insertLanguageBadges(document);
          }
          const badge = document.querySelector(
            '#founder-draft-editor .ProseMirror pre .hljs-lang-badge'
          );
          return {
            ok: !!(badge && badge.textContent === 'sql'),
            codeClass: code ? code.className : 'no-code',
            badgeText: badge ? badge.textContent : null,
          };
        }"""
    )
    assert info.get("ok"), info


def test_dynamic_rehighlight(page: Page, base_url: str):
    goto_founder_workbench(page, base_url)
    _wait_for_editor(page)
    info = page.evaluate(
        """() => {
          const ed = window.AssureTiptapEditor && window.AssureTiptapEditor.getEditor();
          if (!ed) return { ok: false, reason: 'no-editor' };
          ed.chain()
            .focus()
            .insertContent({
              type: 'codeBlock',
              attrs: { language: 'javascript' },
              content: [{ type: 'text', text: 'const x = 1;' }],
            })
            .run();
          if (window.AssureCodeBlocks && window.AssureCodeBlocks.applyHighlightToCodeBlocks) {
            window.AssureCodeBlocks.applyHighlightToCodeBlocks(document);
          }
          const code = document.querySelector(
            '#founder-draft-editor .ProseMirror pre code.language-javascript'
          );
          if (!code) return { ok: false, reason: 'no-code', codeClass: 'missing' };
          const highlighted =
            code.querySelector('.hljs-keyword, .hljs-built_in, .hljs-number, .hljs-literal') !== null
            || code.innerHTML.indexOf('hljs-') >= 0
            || code.classList.contains('hljs');
          return { ok: highlighted, codeClass: code.className, html: code.innerHTML.slice(0, 120) };
        }"""
    )
    assert info.get("ok"), info
