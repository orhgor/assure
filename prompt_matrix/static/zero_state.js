/**
 * Wrapper overlay for an empty canvas. Does not rewrite #jdf-render-target internals.
 */
(function (global) {
  "use strict";

  function tx(key, fallback) {
    if (typeof global.__assureT === "function") return global.__assureT(key, fallback);
    return fallback;
  }

  function isEditorEmpty(editor) {
    if (!editor) return true;
    if (editor.querySelector(".is-streaming-skeleton")) return false;
    if (editor.querySelector(".jdf-canvas-empty")) return true;
    var structural = editor.querySelectorAll(
      ".jdf-node, .jdf-ast-section, .jdf-ast-accordion, .ProseMirror p, .ProseMirror h1, .ProseMirror h2"
    );
    if (structural.length) {
      var filled = Array.prototype.some.call(structural, function (node) {
        return (node.innerText || "").replace(/\s+/g, " ").trim().length > 0;
      });
      return !filled;
    }
    return !(editor.innerText || "").replace(/\s+/g, " ").trim();
  }

  function toggleZeroState() {
    var editor = document.getElementById("jdf-render-target");
    var dashboard = document.getElementById("zero-state-dashboard");
    var stage = document.getElementById("canvas-stage");
    if (!editor) return;
    var empty = isEditorEmpty(editor);
    if (dashboard) dashboard.hidden = !empty;
    editor.classList.toggle("is-empty", empty);
    if (stage) stage.classList.toggle("is-zero", empty);
    editor.setAttribute(
      "data-ghost",
      tx(
        "canvas.zero.ghost",
        "1. Paste or describe the document in Draft.\n2. Click Assemble to extract claims.\n3. Run Full Audit to verify locked numbers.\n\nType or paste to begin…"
      )
    );
  }

  function bind() {
    var editor = document.getElementById("jdf-render-target");
    if (!editor || editor.dataset.zeroStateBound === "1") return;
    editor.dataset.zeroStateBound = "1";
    editor.addEventListener("input", toggleZeroState);
    var observer = new MutationObserver(toggleZeroState);
    observer.observe(editor, { childList: true, subtree: true, characterData: true });
    document.addEventListener("assure:jdf:rendered", toggleZeroState);
    document.addEventListener("assure:compile:start", toggleZeroState);
    document.addEventListener("assure:compile:verified", toggleZeroState);
    document.addEventListener("assure:i18n", toggleZeroState);
    toggleZeroState();
  }

  global.AssureZeroState = { toggle: toggleZeroState };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }
})(window);
