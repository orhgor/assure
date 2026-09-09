/**
 * Floating ⌘K operator prompt — Cursor-style in-editor AI command bar.
 */
(function (global) {
  "use strict";

  var shell = null;
  var input = null;
  var badge = null;
  var activeEditor = null;
  var anchorPos = null;
  var open = false;
  var submitting = false;
  var resizeBound = false;

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

  function ensureDom() {
    if (shell) return shell;
    var container = document.querySelector(".app-container.founder-workbench");
    if (!container) return null;
    shell = document.createElement("div");
    shell.id = "operator-prompt";
    shell.className = "operator-prompt hidden";
    shell.hidden = true;
    shell.setAttribute("aria-hidden", "true");
    shell.setAttribute("role", "dialog");
    shell.innerHTML =
      '<div class="operator-prompt-chrome">' +
      '<span class="operator-prompt-badge" data-i18n="operator.prompt.badge">' +
      translate("operator.prompt.badge", "⌘K to Edit") +
      "</span>" +
      '<input type="text" id="operator-prompt-input" class="operator-prompt-input" ' +
      'autocomplete="off" spellcheck="false" aria-label="' +
      translate("operator.prompt.aria", "Operator prompt") +
      '" />' +
      "</div>";
    container.appendChild(shell);
    input = shell.querySelector("#operator-prompt-input");
    badge = shell.querySelector(".operator-prompt-badge");
    bindInput();
    return shell;
  }

  function selectionEmpty(ed) {
    if (!ed) return true;
    return ed.state.selection.empty;
  }

  function placeholderFor(ed) {
    if (selectionEmpty(ed)) {
      return translate("operator.prompt.placeholder_draft", "Draft intent...");
    }
    return translate("operator.prompt.placeholder_edit", "Edit selection...");
  }

  function clamp(n, min, max) {
    return Math.max(min, Math.min(max, n));
  }

  function positionAtEditor(ed) {
    if (!shell || !ed || !ed.view) return;
    var pos = anchorPos != null ? anchorPos : ed.state.selection.from;
    var coords;
    try {
      coords = ed.view.coordsAtPos(pos);
    } catch (_) {
      return;
    }
    var margin = 8;
    var width = shell.offsetWidth || 420;
    var height = shell.offsetHeight || 48;
    var left = clamp(coords.left, margin, window.innerWidth - width - margin);
    var below = coords.bottom + margin;
    var above = coords.top - height - margin;
    var top = below + height <= window.innerHeight - margin ? below : Math.max(margin, above);
    shell.style.left = left + "px";
    shell.style.top = top + "px";
  }

  function bindResize() {
    if (resizeBound) return;
    resizeBound = true;
    window.addEventListener(
      "resize",
      function () {
        if (open && activeEditor) positionAtEditor(activeEditor);
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

  function showShell(ed) {
    ensureDom();
    if (!shell || !input) return;
    activeEditor = ed;
    anchorPos = ed.state.selection.from;
    shell.classList.remove("hidden");
    shell.hidden = false;
    shell.setAttribute("aria-hidden", "false");
    shell.classList.toggle("is-selection", !selectionEmpty(ed));
    input.disabled = false;
    input.value = "";
    input.placeholder = placeholderFor(ed);
    open = true;
    positionAtEditor(ed);
    bindResize();
    requestAnimationFrame(function () {
      input.focus();
      shell.classList.add("is-focused");
    });
  }

  function hideShell(restoreFocus) {
    if (!shell) return;
    open = false;
    submitting = false;
    shell.classList.add("hidden");
    shell.classList.remove("is-focused", "is-loading");
    shell.hidden = true;
    shell.setAttribute("aria-hidden", "true");
    if (input) {
      input.disabled = false;
      input.value = "";
    }
    if (restoreFocus !== false && activeEditor && !activeEditor.isDestroyed) {
      try {
        activeEditor.commands.focus();
      } catch (_) {}
    }
    activeEditor = null;
    anchorPos = null;
  }

  function selectedText(ed) {
    if (!ed || selectionEmpty(ed)) return "";
    return ed.state.doc.textBetween(ed.state.selection.from, ed.state.selection.to, "\n");
  }

  function setLoading(on) {
    if (!input || !shell) return;
    submitting = !!on;
    input.disabled = submitting;
    shell.classList.toggle("is-loading", submitting);
    if (submitting) {
      input.value = translate("operator.prompt.loading", "Compiling AST...");
    }
  }

  function submitOperatorIntent(prompt, selection) {
    var directive = String(prompt || "").trim();
    if (!directive) return Promise.resolve();
    if (global.AssureCommandBar && typeof global.AssureCommandBar.submitExternal === "function") {
      return global.AssureCommandBar.submitExternal(directive, {
        selected_text: selection || "",
      });
    }
    var workspaceId =
      (global.AssureFounderMode && global.AssureFounderMode.getWorkspaceId()) ||
      global.__ASSURE_PROJECT_ID__ ||
      "founder";
    var body = {
      directive: directive,
      workspace_id: workspaceId,
      model: "gemini",
      stream: true,
    };
    if (selection) {
      body.directive = directive + "\n\n---\nSelected:\n" + selection;
    }
    return fetch("/api/runs", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(body),
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        document.dispatchEvent(new CustomEvent("assure:runs-updated"));
        return data;
      });
  }

  function handleSubmit() {
    if (!input || submitting || !activeEditor) return;
    var prompt = input.value.trim();
    if (!prompt) return;
    var selection = selectedText(activeEditor);
    setLoading(true);
    Promise.resolve(submitOperatorIntent(prompt, selection))
      .then(function () {
        hideShell(true);
      })
      .catch(function () {
        setLoading(false);
        if (input) input.value = prompt;
      });
  }

  function bindInput() {
    if (!input || input.dataset.bound === "1") return;
    input.dataset.bound = "1";
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        e.stopPropagation();
        handleSubmit();
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        hideShell(true);
      }
    });
    input.addEventListener("focus", function () {
      if (shell) shell.classList.add("is-focused");
    });
    input.addEventListener("blur", function () {
      if (shell && !submitting) shell.classList.remove("is-focused");
    });
  }

  function openAtSelection(ed) {
    if (!isFounderShell() || !ed || ed.isDestroyed) return;
    if (open) {
      hideShell(false);
    }
    showShell(ed);
  }

  function init() {
    if (!isFounderShell()) return;
    ensureDom();
  }

  global.AssureOperatorPrompt = {
    init: init,
    open: openAtSelection,
    openAtSelection: openAtSelection,
    close: hideShell,
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
