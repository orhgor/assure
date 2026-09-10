/**
 * Difference Engine orchestrator — POST intent, render Claude + DeepSeek staging panes.
 */
(function (global) {
  "use strict";

  function workspaceId() {
    if (global.AssureFounderDraft && typeof global.AssureFounderDraft.getWorkspaceId === "function") {
      return global.AssureFounderDraft.getWorkspaceId();
    }
    if (global.AssureFounderMode && typeof global.AssureFounderMode.getWorkspaceId === "function") {
      return global.AssureFounderMode.getWorkspaceId();
    }
    return global.__ASSURE_PROJECT_ID__ || "founder";
  }

  function orchestrateUrl() {
    return "/api/projects/" + encodeURIComponent(workspaceId()) + "/orchestrate";
  }
  var DIFF_HIGHLIGHT_CLASS =
    "diff-highlight bg-yellow-500/20 text-yellow-200 border-l-2 border-yellow-500 p-2 my-2 relative group";
  var PUSH_BTN_CLASS =
    "push-to-main-btn absolute top-1 right-1 opacity-0 group-hover:opacity-100 bg-blue-600 text-white text-xs px-2 py-1 rounded";
  var submitting = false;
  var INJECTED_BTN_STYLE = {
    opacity: "1",
    background: "#10b981",
    color: "#ffffff",
    cursor: "default",
  };

  function isFounderShell() {
    return (
      document.body.classList.contains("founder-workbench") &&
      !document.body.classList.contains("legacy-workbench")
    );
  }

  function orchestratorInput() {
    return document.getElementById("operator-prompt-input");
  }

  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function stagingCanvas() {
    return document.querySelector(".staging-canvas");
  }

  function paneEl(modelKey) {
    return document.querySelector('.model-pane[data-model="' + modelKey + '"]');
  }

  function ensureStagingRow() {
    var canvas = stagingCanvas();
    if (!canvas) return null;
    var row = canvas.querySelector(".staging-canvas-row");
    if (row) return row;
    row = document.createElement("div");
    row.className = "staging-canvas-row";
    row.innerHTML =
      '<div class="model-pane p-4" data-model="claude"></div>' +
      '<div class="model-pane p-4" data-model="deepseek"></div>';
    canvas.innerHTML = "";
    canvas.appendChild(row);
    return row;
  }

  function showStagingLoading() {
    var canvas = stagingCanvas();
    if (!canvas) return;
    canvas.innerHTML =
      '<div class="staging-canvas-loading" aria-live="polite" aria-busy="true">' +
      '<p class="model-pane-text">Running Claude + DeepSeek…</p>' +
      "</div>";
  }

  function clearStagingLoading() {
    var canvas = stagingCanvas();
    if (!canvas) return;
    canvas.removeAttribute("aria-busy");
  }

  function diffRegions(leftText, rightText) {
    var left = String(leftText || "");
    var right = String(rightText || "");
    if (left === right) {
      return {
        left: { prefix: left, highlight: "", suffix: "" },
        right: { prefix: right, highlight: "", suffix: "" },
      };
    }
    var start = 0;
    var maxStart = Math.min(left.length, right.length);
    while (start < maxStart && left.charAt(start) === right.charAt(start)) {
      start += 1;
    }
    var endLeft = left.length - 1;
    var endRight = right.length - 1;
    while (endLeft >= start && endRight >= start && left.charAt(endLeft) === right.charAt(endRight)) {
      endLeft -= 1;
      endRight -= 1;
    }
    return {
      left: {
        prefix: left.slice(0, start),
        highlight: left.slice(start, endLeft + 1),
        suffix: left.slice(endLeft + 1),
      },
      right: {
        prefix: right.slice(0, start),
        highlight: right.slice(start, endRight + 1),
        suffix: right.slice(endRight + 1),
      },
    };
  }

  function renderPane(modelKey, model, regions) {
    var pane = paneEl(modelKey);
    if (!pane || !model) return;
    var region = regions[modelKey === "claude" ? "left" : "right"];
    var header = escapeHtml(model.name || modelKey);
    var html = '<header class="model-pane-header">' + header + "</header>";
    if (region.prefix) {
      html += '<p class="model-pane-text">' + escapeHtml(region.prefix) + "</p>";
    }
    if (region.highlight) {
      html +=
        '<p class="' +
        DIFF_HIGHLIGHT_CLASS +
        '">' +
        escapeHtml(region.highlight) +
        '<button type="button" class="' +
        PUSH_BTN_CLASS +
        '">Add to Main</button></p>';
    }
    if (region.suffix) {
      html += '<p class="model-pane-text">' + escapeHtml(region.suffix) + "</p>";
    }
    pane.innerHTML = html;
  }

  function renderStaging(payload) {
    ensureStagingRow();
    var models = (payload && payload.models) || {};
    var claude = models.claude || {};
    var deepseek = models.deepseek || {};
    var regions = diffRegions(claude.text, deepseek.text);
    renderPane("claude", claude, regions);
    renderPane("deepseek", deepseek, regions);
    var canvas = stagingCanvas();
    if (canvas) canvas.scrollTop = 0;
  }

  function getMainEditor() {
    var api = global.AssureTiptapEditor;
    if (!api || typeof api.getEditor !== "function") return null;
    var ed = api.getEditor();
    if (!ed || ed.isDestroyed) return null;
    return ed;
  }

  function extractMergeText(button) {
    if (!button) return "";
    var block = button.closest(".diff-highlight") || button.closest(".model-pane");
    if (!block) return "";
    var clone = block.cloneNode(true);
    clone.querySelectorAll(".push-to-main-btn, .model-pane-header").forEach(function (el) {
      el.remove();
    });
    return String(clone.textContent || "")
      .replace(/\s+/g, " ")
      .trim();
  }

  function appendToMainDocument(text) {
    var raw = String(text || "").trim();
    if (!raw) return false;
    var api = global.AssureTiptapEditor;
    var ed = getMainEditor();
    if (!ed) return false;
    try {
      if (api && typeof api.insertAtCursor === "function") {
        var prefix = ed.getText().trim().length > 0 ? "\n\n" : "";
        return api.insertAtCursor(prefix + raw);
      }
      ed.chain()
        .focus("end")
        .insertContent({
          type: "jdfParagraph",
          attrs: { nodeId: "", gutter: "unverified" },
          content: [{ type: "text", text: raw }],
        })
        .run();
      return true;
    } catch (_) {
      return false;
    }
  }

  function markButtonInjected(button) {
    if (!button) return;
    button.textContent = "Injected";
    button.disabled = true;
    button.setAttribute("aria-disabled", "true");
    button.dataset.injected = "1";
    Object.keys(INJECTED_BTN_STYLE).forEach(function (key) {
      button.style[key] = INJECTED_BTN_STYLE[key];
    });
  }

  function onPushToMain(button) {
    if (!button || button.disabled || button.dataset.injected === "1") return;
    var text = extractMergeText(button);
    if (!text) return;
    var ok = appendToMainDocument(text);
    if (!ok) return;
    markButtonInjected(button);
    var pane = button.closest(".model-pane");
    document.dispatchEvent(
      new CustomEvent("assure:merge:main", {
        detail: { text: text, model: pane ? pane.getAttribute("data-model") || "" : "" },
      })
    );
  }

  function bindPushToMain() {
    if (document.body.dataset.pushToMainBound === "1") return;
    document.body.dataset.pushToMainBound = "1";
    document.addEventListener(
      "click",
      function (e) {
        var btn = e.target && e.target.closest ? e.target.closest(".push-to-main-btn") : null;
        if (!btn || btn.disabled) return;
        var canvas = stagingCanvas();
        if (!canvas || !canvas.contains(btn)) return;
        e.preventDefault();
        e.stopPropagation();
        onPushToMain(btn);
      },
      false
    );
  }

  function readIntent() {
    var input = orchestratorInput();
    return input ? String(input.value || "").trim() : "";
  }

  function closeOperatorPrompt() {
    if (global.AssureOperatorPrompt && typeof global.AssureOperatorPrompt.close === "function") {
      global.AssureOperatorPrompt.close();
      return;
    }
    if (typeof global.hideOperatorPrompt === "function") {
      global.hideOperatorPrompt(true);
    }
  }

  function postOrchestrate(intent) {
    return fetch(orchestrateUrl(), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ intent: intent }),
    }).then(function (response) {
      return response.json().then(function (body) {
        if (!response.ok) {
          throw new Error((body && body.error) || "Orchestrator request failed");
        }
        return body;
      });
    });
  }

  function runIntent(intent) {
    var value = String(intent || "").trim();
    if (!value) return Promise.reject(new Error("Intent is required"));
    if (submitting) return Promise.resolve();
    submitting = true;
    showStagingLoading();
    return postOrchestrate(value)
      .then(function (payload) {
        if (!payload || payload.status !== "success") {
          throw new Error((payload && payload.error) || "Orchestrator returned an error");
        }
        clearStagingLoading();
        renderStaging(payload);
        document.dispatchEvent(
          new CustomEvent("assure:orchestrate:complete", { detail: payload })
        );
        return payload;
      })
      .catch(function (err) {
        clearStagingLoading();
        ensureStagingRow();
        var claudePane = paneEl("claude");
        var deepseekPane = paneEl("deepseek");
        var msg = escapeHtml(String((err && err.message) || err));
        if (claudePane) claudePane.innerHTML = '<p class="model-pane-text">' + msg + "</p>";
        if (deepseekPane) deepseekPane.innerHTML = '<p class="model-pane-text">' + msg + "</p>";
        throw err;
      })
      .finally(function () {
        submitting = false;
      });
  }

  function onSubmit() {
    if (!isFounderShell()) return;
    var intent = readIntent();
    if (!intent) return;
    runIntent(intent)
      .then(function () {
        closeOperatorPrompt();
      })
      .catch(function () {
        /* error surfaced in staging panes */
      });
  }

  function bindSubmitControls() {
    var btn = document.querySelector(".orchestrator-submit");
    if (btn && btn.dataset.orchestratorBound !== "1") {
      btn.dataset.orchestratorBound = "1";
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        onSubmit();
      });
    }

    if (document.body.dataset.orchestratorEnterBound === "1") return;
    document.body.dataset.orchestratorEnterBound = "1";
    document.addEventListener(
      "keydown",
      function (e) {
        if (e.key !== "Enter" || e.shiftKey) return;
        var input = orchestratorInput();
        if (!input || document.activeElement !== input) return;
        if (!isFounderShell()) return;
        e.preventDefault();
        e.stopImmediatePropagation();
        onSubmit();
      },
      true
    );
  }

  function init() {
    if (!isFounderShell()) return;
    bindSubmitControls();
    bindPushToMain();
  }

  global.AssureOrchestrator = {
    init: init,
    run: runIntent,
    renderStaging: renderStaging,
    handleSubmit: onSubmit,
    appendToMainDocument: appendToMainDocument,
    pushToMain: onPushToMain,
  };

  document.addEventListener("DOMContentLoaded", init);
})(window);
