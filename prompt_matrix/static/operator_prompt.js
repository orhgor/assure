/**
 * Floating ⌘K operator prompt — cockpit prototype integrated with TipTap.
 */
(function (global) {
  "use strict";

  var shell = null;
  var overlay = null;
  var input = null;
  var loading = null;
  var loadingText = null;
  var activeEditor = null;
  var anchorPos = null;
  var open = false;
  var submitting = false;
  var resizeTimer = null;

  var PROMPT_WIDTH = 600;
  var PROMPT_HEIGHT = 100;

  function translate(key, fallback) {
    if (typeof global.__assureT === "function") return global.__assureT(key, fallback);
    return fallback || key;
  }

  function isFounderShell() {
    return (
      document.body.classList.contains("founder-workbench") &&
      !document.body.classList.contains("legacy-workbench")
    );
  }

  function bindDom() {
    if (shell) return;
    shell = document.getElementById("operator-prompt");
    overlay = document.getElementById("operator-canvas-overlay");
    input = document.getElementById("operator-prompt-input");
    loading = document.getElementById("operator-prompt-loading");
    loadingText = document.getElementById("operator-prompt-loading-text");
    if (!shell || !input) return;
    bindInput();
    bindGlobalKeys();
  }

  function containerFor(editor) {
    if (!editor || !editor.view) return null;
    return (
      editor.view.dom.closest(".app-container.founder-workbench") ||
      editor.view.dom.closest(".founder-workbench") ||
      document.querySelector(".app-container.founder-workbench")
    );
  }

  function selectionEmpty(ed) {
    if (!ed) return true;
    var sel = ed.state.selection;
    return sel.empty || sel.from === sel.to;
  }

  function placeholderFor(ed) {
    if (selectionEmpty(ed)) {
      return translate(
        "operator.prompt.placeholder_draft",
        "Draft intent... (e.g., 'Add a governing law clause')"
      );
    }
    return translate(
      "operator.prompt.placeholder_edit",
      "Edit selection... (e.g., 'Make this more aggressive')"
    );
  }

  function clamp(n, min, max) {
    return Math.max(min, Math.min(max, n));
  }

  function positionAtEditor(ed) {
    if (!shell || !ed || !ed.view) return;
    var container = containerFor(ed);
    if (!container) return;
    var pos = anchorPos != null ? anchorPos : ed.state.selection.from;
    var coords;
    try {
      coords = ed.view.coordsAtPos(pos);
    } catch (_) {
      return;
    }
    var rect = container.getBoundingClientRect();
    var left = coords.left - rect.left;
    var top = coords.top - rect.top - 10;
    var containerWidth = container.clientWidth;
    var containerHeight = container.clientHeight;
    if (left + PROMPT_WIDTH > containerWidth) left = containerWidth - PROMPT_WIDTH - 10;
    if (left < 10) left = 10;
    if (top < 10) top = 10;
    if (top + PROMPT_HEIGHT > containerHeight) {
      top = coords.top - rect.top - PROMPT_HEIGHT - 10;
    }
    if (top < 10) top = 10;
    shell.style.left = left + "px";
    shell.style.top = top + "px";
  }

  function showOperatorPrompt(editor) {
    if (!isFounderShell() || !editor || editor.isDestroyed) return;
    bindDom();
    if (!shell || !input) return;
    activeEditor = editor;
    anchorPos = editor.state.selection.from;
    input.placeholder = placeholderFor(editor);
    input.value = "";
    input.disabled = false;
    if (loading) {
      loading.classList.add("hidden");
      loading.hidden = true;
    }
    if (overlay) {
      overlay.classList.remove("hidden");
      overlay.hidden = false;
      overlay.setAttribute("aria-hidden", "false");
    }
    shell.classList.remove("hidden", "scale-95", "opacity-0");
    shell.hidden = false;
    shell.setAttribute("aria-hidden", "false");
    shell.classList.add("scale-100", "opacity-100", "is-focused");
    open = true;
    positionAtEditor(editor);
    requestAnimationFrame(function () {
      input.focus();
    });
  }

  function hideOperatorPrompt(restoreFocus) {
    if (!shell) return;
    open = false;
    submitting = false;
    shell.classList.remove("scale-100", "opacity-100", "is-focused", "is-loading");
    shell.classList.add("scale-95", "opacity-0");
    if (overlay) {
      overlay.classList.add("hidden");
      overlay.hidden = true;
      overlay.setAttribute("aria-hidden", "true");
    }
    setTimeout(function () {
      if (!shell) return;
      shell.classList.add("hidden");
      shell.hidden = true;
      shell.setAttribute("aria-hidden", "true");
      if (input) {
        input.value = "";
        input.disabled = false;
      }
      if (loading) {
        loading.classList.add("hidden");
        loading.hidden = true;
      }
      if (restoreFocus !== false && activeEditor && !activeEditor.isDestroyed) {
        try {
          activeEditor.commands.focus();
        } catch (_) {}
      }
      activeEditor = null;
      anchorPos = null;
    }, 200);
  }

  function setLoadingStage(text) {
    if (!loading || !loadingText || !input) return;
    loading.classList.remove("hidden");
    loading.hidden = false;
    loadingText.textContent = text;
    shell.classList.add("is-loading");
    input.disabled = true;
    submitting = true;
  }

  function selectedText(ed) {
    if (!ed || selectionEmpty(ed)) return "";
    return ed.state.doc.textBetween(ed.state.selection.from, ed.state.selection.to, "\n");
  }

  function submitOperatorIntent(intent, editor) {
    var directive = String(intent || "").trim();
    if (!directive || !editor) return Promise.resolve();
    setLoadingStage(translate("operator.prompt.loading_route", "Routing to Auto-Compiler..."));
    var selection = selectedText(editor);
    var stage2 = setTimeout(function () {
      setLoadingStage(translate("operator.prompt.loading_compile", "Compiling AST..."));
    }, 800);
    var stage3 = setTimeout(function () {
      setLoadingStage(translate("operator.prompt.loading_apply", "Applying inline diff..."));
    }, 1600);
    var done = setTimeout(function () {
      clearTimeout(stage2);
      clearTimeout(stage3);
    }, 1600);

    var submitPromise;
    if (global.AssureCommandBar && typeof global.AssureCommandBar.submitExternal === "function") {
      submitPromise = global.AssureCommandBar.submitExternal(directive, {
        selected_text: selection,
      });
    } else {
      submitPromise = Promise.resolve();
    }

    var minDelay = new Promise(function (resolve) {
      setTimeout(resolve, 2000);
    });
    return Promise.all([submitPromise, minDelay])
      .then(function () {
        clearTimeout(stage2);
        clearTimeout(stage3);
        clearTimeout(done);
        hideOperatorPrompt(true);
      })
      .catch(function () {
        clearTimeout(stage2);
        clearTimeout(stage3);
        clearTimeout(done);
        submitting = false;
        if (input) {
          input.disabled = false;
          input.value = directive;
        }
        if (loading) {
          loading.classList.add("hidden");
          loading.hidden = true;
        }
        if (shell) shell.classList.remove("is-loading");
      });
  }

  function handleSubmit() {
    if (!input || submitting) return;
    if (isFounderShell()) {
      if (
        global.AssureOrchestrator &&
        typeof global.AssureOrchestrator.handleSubmit === "function"
      ) {
        global.AssureOrchestrator.handleSubmit();
      }
      return;
    }
    if (!activeEditor) return;
    var intent = input.value.trim();
    if (!intent) return;
    submitOperatorIntent(intent, activeEditor);
  }

  function bindInput() {
    if (!input || input.dataset.bound === "1") return;
    input.dataset.bound = "1";
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        e.stopPropagation();
        handleSubmit();
      }
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        hideOperatorPrompt(true);
      }
    });
  }

  function bindGlobalKeys() {
    if (document.body.dataset.operatorPromptKeys === "1") return;
    document.body.dataset.operatorPromptKeys = "1";
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape" || !shell || shell.classList.contains("hidden")) return;
      e.preventDefault();
      hideOperatorPrompt(true);
    });
    global.addEventListener(
      "resize",
      function () {
        if (!open || !activeEditor) return;
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(function () {
          positionAtEditor(activeEditor);
        }, 100);
      },
      { passive: true }
    );
    document.addEventListener(
      "scroll",
      function () {
        if (open && activeEditor) positionAtEditor(activeEditor);
      },
      true
    );
  }

  function init() {
    if (!isFounderShell()) return;
    bindDom();
  }

  global.showOperatorPrompt = showOperatorPrompt;
  global.hideOperatorPrompt = hideOperatorPrompt;
  global.submitOperatorIntent = submitOperatorIntent;

  global.AssureOperatorPrompt = {
    init: init,
    open: showOperatorPrompt,
    openAtSelection: showOperatorPrompt,
    close: hideOperatorPrompt,
    reposition: function () {
      if (activeEditor) positionAtEditor(activeEditor);
    },
    submitOperatorIntent: submitOperatorIntent,
    isOpen: function () {
      return open;
    },
  };

  document.addEventListener("DOMContentLoaded", init);
})(window);
