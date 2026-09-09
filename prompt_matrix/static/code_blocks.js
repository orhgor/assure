/**
 * Highlight.js + language badges for founder TipTap code blocks.
 */
(function (global) {
  "use strict";

  function isFounderShell() {
    return (
      document.body.classList.contains("founder-workbench") &&
      !document.body.classList.contains("legacy-workbench")
    );
  }

  function applyHighlightToCodeBlocks(root) {
    if (!global.hljs) return;
    codeBlockPres(root).forEach(function (pre) {
      var el = pre.querySelector("code");
      if (!el) return;
      try {
        global.hljs.highlightElement(el);
      } catch (_) {}
    });
  }

  function codeBlockPres(root) {
    var scope = root || document;
    if (scope.classList && scope.classList.contains("ProseMirror")) {
      return scope.querySelectorAll("pre");
    }
    if (scope.querySelector) {
      var host = scope.querySelector ? scope.querySelector(".ProseMirror") : null;
      if (host) return host.querySelectorAll("pre");
    }
    return scope.querySelectorAll(".founder-workbench .ProseMirror pre, .ProseMirror pre");
  }

  function languageFromCode(code) {
    if (!code) return "";
    var match = (code.className || "").match(/\blanguage-([\w-]+)\b/);
    if (match) return match[1];
    for (var i = 0; i < code.classList.length; i++) {
      var cls = code.classList[i];
      if (cls.indexOf("language-") === 0) {
        return cls.replace("language-", "") || "text";
      }
    }
    return "";
  }

  function insertLanguageBadges(root) {
    codeBlockPres(root).forEach(function (pre) {
      if (pre.querySelector(".hljs-lang-badge")) return;
      var code = pre.querySelector("code");
      if (!code) return;
      var lang = languageFromCode(code);
      if (!lang) return;
      var badge = document.createElement("span");
      badge.className = "hljs-lang-badge";
      badge.textContent = lang;
      pre.style.position = "relative";
      pre.appendChild(badge);
    });
  }

  function decorateCodeBlocks(root) {
    if (!isFounderShell()) return;
    insertLanguageBadges(root);
    applyHighlightToCodeBlocks(root);
  }

  function init() {
    document.addEventListener("DOMContentLoaded", function () {
      decorateCodeBlocks(document);
    });
  }

  global.AssureCodeBlocks = {
    init: init,
    decorate: decorateCodeBlocks,
    applyHighlightToCodeBlocks: applyHighlightToCodeBlocks,
    insertLanguageBadges: insertLanguageBadges,
  };

  init();
})(window);
