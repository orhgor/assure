/**
 * Wrapper overlay for an empty canvas. Does not rewrite #jdf-render-target internals.
 */
(function (global) {
  "use strict";

  function tx(key, fallback) {
    if (typeof global.__assureT === "function") return global.__assureT(key, fallback);
    return fallback;
  }

  function collapsedText(el) {
    return (el.innerText || "").replace(/\s+/g, " ").trim().toLowerCase();
  }

  function isPlaceholderCanvas(editor) {
    var text = collapsedText(editor);
    if (!text) return true;
    if (editor.querySelector(".jdf-canvas-empty")) return true;
    if (/^untitled( section)?$/.test(text)) return true;
    if (text === "untitled heading") return true;
    return false;
  }

  function isEditorEmpty(editor) {
    if (!editor) return true;
    if (editor.querySelector(".is-streaming-skeleton")) return false;
    if (isPlaceholderCanvas(editor)) return true;
    var structural = editor.querySelectorAll(
      ".jdf-node, .jdf-ast-section, .jdf-ast-accordion, .ProseMirror p, .ProseMirror h1, .ProseMirror h2"
    );
    if (structural.length) {
      var filled = Array.prototype.some.call(structural, function (node) {
        var t = (node.innerText || "").replace(/\s+/g, " ").trim();
        if (!t) return false;
        if (/^(untitled|section|heading)$/i.test(t)) return false;
        return true;
      });
      return !filled;
    }
    return !collapsedText(editor);
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

  function openTemplate(id) {
    var wizard = global.AssureNewProjectWizard;
    if (wizard && typeof wizard.open === "function") {
      wizard.open(id || "blank");
      return;
    }
    if (global.AssureNav && typeof global.AssureNav.switchView === "function") {
      global.AssureNav.switchView("projects", { replaceHash: false, persist: false });
    }
  }

  function bind() {
    var editor = document.getElementById("jdf-render-target");
    var dashboard = document.getElementById("zero-state-dashboard");
    if (!editor || editor.dataset.zeroStateBound === "1") return;
    editor.dataset.zeroStateBound = "1";
    editor.addEventListener("input", toggleZeroState);
    var observer = new MutationObserver(toggleZeroState);
    observer.observe(editor, { childList: true, subtree: true, characterData: true });
    document.addEventListener("assure:jdf:rendered", toggleZeroState);
    document.addEventListener("assure:compile:start", toggleZeroState);
    document.addEventListener("assure:compile:verified", toggleZeroState);
    document.addEventListener("assure:i18n", toggleZeroState);
    if (dashboard) {
      dashboard.addEventListener("click", function (ev) {
        var btn = ev.target && ev.target.closest ? ev.target.closest("[data-zero-template]") : null;
        if (!btn) return;
        openTemplate(btn.getAttribute("data-zero-template") || "blank");
      });
    }
    toggleZeroState();
  }

  global.AssureZeroState = { toggle: toggleZeroState };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }
})(window);
