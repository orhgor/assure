/**
 * Founder Main Document — full-context scan issue list (Sprint 3 Step 7).
 * Local fix wiring — Sprint 3 Steps 8 & 9 (issue card → ⌘K operator prompt → re-scan).
 */
(function (global) {
  "use strict";

  var scanning = false;
  var pendingLocalFixRescan = false;

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

  function workspaceId() {
    if (global.AssureFounderDraft && typeof global.AssureFounderDraft.getWorkspaceId === "function") {
      return global.AssureFounderDraft.getWorkspaceId();
    }
    if (global.AssureFounderMode && typeof global.AssureFounderMode.getWorkspaceId === "function") {
      return global.AssureFounderMode.getWorkspaceId();
    }
    return global.__ASSURE_PROJECT_ID__ || "founder";
  }

  function getEditor() {
    var api = global.AssureTiptapEditor;
    return api && api.getEditor && api.getEditor();
  }

  function captureDocumentTree() {
    var api = global.AssureTiptapEditor;
    var ed = getEditor();
    if (!api || !ed || !api.tiptapToJdf) return null;
    var base =
      global.AssureFounderDraft && typeof global.AssureFounderDraft.getDraftTree === "function"
        ? global.AssureFounderDraft.getDraftTree()
        : null;
    try {
      return api.tiptapToJdf(ed.getJSON(), base || undefined);
    } catch (_) {
      return null;
    }
  }

  function setButtonBusy(btn, busy) {
    if (!btn) return;
    btn.disabled = !!busy;
    btn.classList.toggle("is-busy", !!busy);
    btn.setAttribute("aria-busy", busy ? "true" : "false");
    if (busy) {
      btn.dataset.busyLabel = btn.textContent;
      btn.textContent = translate("founder.scan.running", "Scanning document...");
    } else if (btn.dataset.busyLabel) {
      btn.textContent = btn.dataset.busyLabel;
      delete btn.dataset.busyLabel;
    }
  }

  function toast(msg, kind) {
    if (global.AssureToast && typeof global.AssureToast.show === "function") {
      global.AssureToast.show(msg, kind || "error");
    }
  }

  function escapeHtml(text) {
    return String(text || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function buildLocalFixPrompt(nodeId, description) {
    var id = String(nodeId || "unknown").trim() || "unknown";
    var desc = String(description || "").trim();
    return "Fix this issue for node " + id + ": " + desc;
  }

  function markLocalFixPanel(shell, active) {
    if (!shell) return;
    if (active) {
      shell.classList.add("local-fix-panel");
      shell.setAttribute("data-local-fix-origin", "1");
      shell.hidden = false;
      shell.setAttribute("aria-hidden", "false");
      return;
    }
    shell.classList.remove("local-fix-panel");
    shell.removeAttribute("data-local-fix-origin");
  }

  function openOperatorPromptWithIntent(promptText) {
    var ed = getEditor();
    if (!ed) {
      toast(translate("founder.scan.error", "Full-context scan failed."));
      return;
    }
    var shell = document.getElementById("operator-prompt");
    var input = document.getElementById("operator-prompt-input");
    if (!input) {
      toast(translate("founder.scan.error", "Full-context scan failed."));
      return;
    }

    pendingLocalFixRescan = true;

    if (global.AssureOperatorPrompt && typeof global.AssureOperatorPrompt.open === "function") {
      global.AssureOperatorPrompt.open(ed);
    } else if (typeof global.showOperatorPrompt === "function") {
      global.showOperatorPrompt(ed);
    } else {
      pendingLocalFixRescan = false;
      toast(translate("founder.scan.error", "Full-context scan failed."));
      return;
    }

    requestAnimationFrame(function () {
      input.value = String(promptText || "");
      markLocalFixPanel(shell, true);
      input.focus();
      if (typeof input.setSelectionRange === "function") {
        var len = input.value.length;
        input.setSelectionRange(len, len);
      }
      document.dispatchEvent(
        new CustomEvent("assure:local-fix:opened", {
          detail: { prompt: input.value },
        })
      );
    });
  }

  function onLocalFixClick(card) {
    if (!card) return;
    var nodeId = card.getAttribute("data-node-id") || "";
    var descEl = card.querySelector(".full-context-issue-description");
    var description = descEl ? descEl.textContent : "";
    openOperatorPromptWithIntent(buildLocalFixPrompt(nodeId, description));
  }

  function renderIssues(container, issues) {
    if (!container) return;
    container.hidden = false;
    container.setAttribute("aria-hidden", "false");
    if (!issues || !issues.length) {
      container.innerHTML =
        '<p class="full-context-empty hint">' +
        escapeHtml(translate("founder.scan.no_issues", "No verifiable issues found.")) +
        "</p>";
      return;
    }
    var html =
      '<header class="full-context-results-header"><h4>' +
      escapeHtml(translate("founder.scan.results_title", "Full-context issues")) +
      '</h4><span class="full-context-count">' +
      escapeHtml(String(issues.length)) +
      "</span></header><ul class=\"full-context-issue-list\">";
    issues.forEach(function (issue) {
      var severity = String(issue.severity || "medium").toLowerCase();
      var nodeId = String(issue.node_id || "").trim();
      html +=
        '<li class="full-context-issue severity-' +
        escapeHtml(severity) +
        '" data-issue-id="' +
        escapeHtml(issue.id || "") +
        '"' +
        (nodeId ? ' data-node-id="' + escapeHtml(nodeId) + '"' : "") +
        ">" +
        '<div class="full-context-issue-meta">' +
        '<span class="full-context-issue-id">' +
        escapeHtml(issue.id || "") +
        "</span>" +
        '<span class="full-context-issue-severity">' +
        escapeHtml(severity) +
        "</span>" +
        '<span class="full-context-issue-category">' +
        escapeHtml(issue.category || "") +
        "</span>" +
        "</div>" +
        '<p class="full-context-issue-description">' +
        escapeHtml(issue.description || "") +
        "</p>" +
        '<button type="button" class="btn btn-outline btn-sm local-fix-btn">' +
        escapeHtml(translate("founder.scan.fix_locally", "Fix Locally")) +
        "</button>" +
        "</li>";
    });
    html += "</ul>";
    container.innerHTML = html;
  }

  function requestScan(documentTree) {
    return fetch("/api/projects/" + encodeURIComponent(workspaceId()) + "/scan", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ document: documentTree }),
    }).then(function (response) {
      return response.json().then(function (body) {
        if (!response.ok) {
          var msg =
            (body && body.error) ||
            translate("founder.scan.error", "Full-context scan failed.");
          throw new Error(msg);
        }
        return body;
      });
    });
  }

  function onFullContextScan() {
    if (!isFounderShell() || scanning) return;
    var documentTree = captureDocumentTree();
    if (!documentTree) {
      toast(translate("founder.scan.error", "Full-context scan failed."));
      return;
    }
    var btn = document.getElementById("btn-full-context-scan");
    var results = document.querySelector(".full-context-results");
    scanning = true;
    setButtonBusy(btn, true);
    if (results) {
      results.hidden = false;
      results.setAttribute("aria-hidden", "false");
      results.innerHTML =
        '<p class="full-context-empty hint">' +
        escapeHtml(translate("founder.scan.running", "Scanning document...")) +
        "</p>";
    }
    requestScan(documentTree)
      .then(function (payload) {
        if (!payload || payload.status !== "success") {
          throw new Error(translate("founder.scan.error", "Full-context scan failed."));
        }
        renderIssues(results, payload.issues || []);
        document.dispatchEvent(
          new CustomEvent("assure:scan:complete", {
            detail: { issue_count: payload.issue_count || 0 },
          })
        );
      })
      .catch(function (err) {
        toast(String((err && err.message) || err));
        if (results) {
          results.hidden = false;
          results.setAttribute("aria-hidden", "false");
          results.innerHTML =
            '<p class="full-context-empty hint">' +
            escapeHtml(translate("founder.scan.error", "Full-context scan failed.")) +
            "</p>";
        }
      })
      .finally(function () {
        scanning = false;
        setButtonBusy(btn, false);
      });
  }

  function bindScanButton() {
    var btn = document.getElementById("btn-full-context-scan");
    if (!btn || btn.dataset.scanBound === "1") return;
    btn.dataset.scanBound = "1";
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      onFullContextScan();
    });
  }

  function bindLocalFixButtons() {
    if (document.body.dataset.localFixBound === "1") return;
    document.body.dataset.localFixBound = "1";
    document.addEventListener(
      "click",
      function (e) {
        if (!isFounderShell()) return;
        var btn = e.target && e.target.closest ? e.target.closest(".local-fix-btn") : null;
        if (!btn) return;
        var results = document.querySelector(".full-context-results");
        if (!results || !results.contains(btn)) return;
        e.preventDefault();
        e.stopPropagation();
        onLocalFixClick(btn.closest(".full-context-issue"));
      },
      false
    );
  }

  function bindRescanAfterLocalFix() {
    if (document.body.dataset.scanRescanBound === "1") return;
    document.body.dataset.scanRescanBound = "1";

    document.addEventListener("assure:orchestrate:complete", function () {
      if (!pendingLocalFixRescan) return;
      pendingLocalFixRescan = false;
      markLocalFixPanel(document.getElementById("operator-prompt"), false);
      onFullContextScan();
    });

    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape") return;
      var shell = document.getElementById("operator-prompt");
      if (!shell || shell.getAttribute("data-local-fix-origin") !== "1") return;
      pendingLocalFixRescan = false;
      markLocalFixPanel(shell, false);
    });
  }

  function init() {
    if (!isFounderShell()) return;
    bindScanButton();
    bindLocalFixButtons();
    bindRescanAfterLocalFix();
  }

  global.AssureFounderScan = {
    init: init,
    scanMain: onFullContextScan,
    renderIssues: renderIssues,
    openLocalFix: openOperatorPromptWithIntent,
  };

  document.addEventListener("DOMContentLoaded", init);
})(window);
