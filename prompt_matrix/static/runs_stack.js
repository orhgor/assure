/**
 * Founder workbench — runs stack in left pane.
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
          '<div class="run-card-actions">' +
          '<button type="button" class="btn btn-outline btn-sm" data-action="draft">Send to Draft</button>' +
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
    });
  }

  function load() {
    var ws = global.__ASSURE_PROJECT_ID__ || "default";
    return fetch("/api/runs?workspace_id=" + encodeURIComponent(ws), { credentials: "same-origin" })
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

  global.AssureRunsStack = { load: load, prepend: prepend, render: render, init: init };
  document.addEventListener("DOMContentLoaded", init);
})(window);
