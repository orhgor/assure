/**
 * Founder workbench — runs stack in left pane (Phase 2: Red-Hat findings).
 */
(function (global) {
  "use strict";

  var runs = [];
  var collapsed = false;

  function $(id) {
    return document.getElementById(id);
  }

  function esc(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function fmtTime(iso) {
    if (!iso) return "";
    try {
      var d = new Date(String(iso).replace(" ", "T") + "Z");
      return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
    } catch (_) {
      return iso;
    }
  }

  function statusLabel(status) {
    if (status === "stamped") return "Stamped";
    if (status === "contradiction") return "Contradiction";
    return "Draft";
  }

  function runPreviewText(run) {
    var parts = [run.directive || ""];
    var content = run.content || {};
    (content.body || []).forEach(function (sec) {
      (sec.children || []).forEach(function (node) {
        if (node.type === "paragraph" && node.content) parts.push(node.content);
      });
    });
    return parts.filter(Boolean).join(" ");
  }

  function highlightFindings(text, findings) {
    var html = esc(text);
    (findings || []).forEach(function (f) {
      if (f.status !== "open") return;
      var needle = (f.highlight_text || f.content || "").trim();
      if (!needle || needle.length < 4) return;
      var re = new RegExp(esc(needle).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi");
      html = html.replace(re, function (m) {
        return '<mark class="run-finding-highlight">' + m + "</mark>";
      });
    });
    return html;
  }

  function renderFindingActions(runId, finding) {
    if (finding.status !== "open") {
      return '<span class="run-finding-status">' + esc(finding.status) + "</span>";
    }
    return (
      '<button type="button" class="btn btn-outline btn-xs" data-action="accept-finding" data-finding-id="' +
      esc(finding.id) +
      '">Accept Fix</button>' +
      '<button type="button" class="btn btn-ghost btn-xs" data-action="dismiss-finding" data-finding-id="' +
      esc(finding.id) +
      '">Dismiss</button>'
    );
  }

  function render() {
    var host = $("runs-stack-list");
    if (!host) return;
    if (!runs.length) {
      host.innerHTML = '<p class="runs-stack-empty hint">No runs yet. Press ⌘K to investigate.</p>';
      return;
    }
    host.innerHTML = runs
      .map(function (run) {
        var locks = (run.extracted_locks || []).length;
        var findings = run.redhat_findings || [];
        var preview = highlightFindings(runPreviewText(run), findings);
        var findingsHtml = findings.length
          ? findings
              .map(function (f) {
                return (
                  '<li class="run-finding-item" data-status="' +
                  esc(f.status) +
                  '">' +
                  '<strong>' +
                  esc(f.title || "Finding") +
                  "</strong> " +
                  esc(f.content) +
                  '<div class="run-finding-actions">' +
                  renderFindingActions(run.id, f) +
                  "</div></li>"
                );
              })
              .join("")
          : "";
        return (
          '<article class="run-card" data-run-id="' +
          esc(run.id) +
          '">' +
          '<div class="run-card-top">' +
          '<span class="run-status-pill" data-status="' +
          esc(run.status) +
          '">' +
          esc(statusLabel(run.status)) +
          "</span>" +
          '<span class="run-model">' +
          esc(run.model || "gemini") +
          "</span></div>" +
          '<h4 class="run-card-title">' +
          esc(run.title || run.directive || "Run") +
          "</h4>" +
          '<p class="run-card-meta">' +
          esc(locks + " locks · " + fmtTime(run.created_at)) +
          "</p>" +
          '<div class="run-card-preview">' +
          preview +
          "</div>" +
          (findingsHtml ? '<ul class="run-findings-list">' + findingsHtml + "</ul>" : "") +
          '<div class="run-card-actions">' +
          '<button type="button" class="btn btn-outline btn-sm" data-action="draft">Send to Draft</button>' +
          '<button type="button" class="btn-trust-redhat btn-sm" data-action="redhat">⚡ Red-Hat</button>' +
          '<button type="button" class="btn btn-ghost btn-sm" data-action="delete">Delete</button>' +
          "</div></article>"
        );
      })
      .join("");

    host.querySelectorAll(".run-card").forEach(function (card) {
      var id = card.getAttribute("data-run-id");
      card.querySelector('[data-action="draft"]').addEventListener("click", function () {
        sendToDraft(id);
      });
      card.querySelector('[data-action="delete"]').addEventListener("click", function () {
        deleteRun(id);
      });
      var rh = card.querySelector('[data-action="redhat"]');
      if (rh) rh.addEventListener("click", function () {
        runRedhat(id, rh);
      });
      card.querySelectorAll('[data-action="accept-finding"]').forEach(function (btn) {
        btn.addEventListener("click", function () {
          resolveFinding(id, btn.getAttribute("data-finding-id"), "accept", "");
        });
      });
      card.querySelectorAll('[data-action="dismiss-finding"]').forEach(function (btn) {
        btn.addEventListener("click", function () {
          var rationale = window.prompt("Dismissal rationale (required):") || "";
          if (!rationale.trim()) return;
          resolveFinding(id, btn.getAttribute("data-finding-id"), "dismiss", rationale.trim());
        });
      });
    });
  }

  function load() {
    var ws = global.__ASSURE_PROJECT_ID__ || "default";
    return fetch("/api/runs?workspace_id=" + encodeURIComponent(ws) + "&include_findings=1", {
      credentials: "same-origin",
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (data.ok) {
          runs = data.runs || [];
          render();
        }
      });
  }

  function setPipelineStatus(message) {
    var el = $("runs-stack-pipeline-status");
    if (!el) return;
    if (!message) {
      el.hidden = true;
      el.textContent = "";
      return;
    }
    el.hidden = false;
    el.textContent = message;
  }

  function prepend(run) {
    runs.unshift(run);
    render();
  }

  function sendToDraft(runId) {
    var run = runs.filter(function (r) {
      return r.id === runId;
    })[0];
    if (!run || !global.AssureFounderDraft) return;
    global.AssureFounderDraft.appendRun(run);
  }

  function deleteRun(runId) {
    fetch("/api/runs/" + encodeURIComponent(runId), {
      method: "DELETE",
      credentials: "same-origin",
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (data.ok) {
          runs = runs.filter(function (r) {
            return r.id !== runId;
          });
          render();
        }
      });
  }

  function runRedhat(runId, btn) {
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Running…";
    }
    fetch("/api/runs/" + encodeURIComponent(runId) + "/redhat", {
      method: "POST",
      credentials: "same-origin",
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (data.ok) load();
      })
      .finally(function () {
        if (btn) {
          btn.disabled = false;
          btn.textContent = "⚡ Red-Hat";
        }
      });
  }

  function resolveFinding(runId, findingId, action, rationale) {
    fetch("/api/runs/" + encodeURIComponent(runId) + "/findings/" + encodeURIComponent(findingId), {
      method: "PATCH",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: action, dismissal_rationale: rationale }),
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (data.ok) load();
      });
  }

  function toggleCollapse() {
    collapsed = !collapsed;
    var pane = $("panel-runs");
    if (pane) pane.classList.toggle("is-collapsed", collapsed);
  }

  function init() {
    var toggle = $("runs-stack-toggle");
    if (toggle) toggle.addEventListener("click", toggleCollapse);
    document.addEventListener("assure:run-created", function () {
      load();
    });
    document.addEventListener("assure:project", function () {
      load();
    });
    load();
  }

  global.AssureRunsStack = {
    load: load,
    prepend: prepend,
    render: render,
    init: init,
    setPipelineStatus: setPipelineStatus,
  };
  document.addEventListener("DOMContentLoaded", init);
})(window);
