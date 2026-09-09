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

  function translate(key, fallback) {
    if (typeof global.__assureT === "function") return global.__assureT(key, fallback);
    return fallback || key;
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
    if (status === "stamped") return translate("founder.runs.status.stamped", "Stamped");
    if (status === "contradiction") return translate("founder.runs.status.contradiction", "Contradiction");
    return translate("founder.runs.status.draft", "Draft");
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

  function runDisplayTitle(run) {
    var title = String(run.title || "").trim();
    var directive = String(run.directive || "").trim();
    if (title && directive && title === directive) return title;
    if (title) return title;
    return directive || translate("founder.runs.untitled", "Run");
  }

  function verifiedClaimsLabel(count) {
    var n = Number(count) || 0;
    if (n === 1) {
      return translate("founder.runs.one_verified_claim", "1 verified claim");
    }
    return translate("founder.runs.verified_claims", "{count} verified claims").replace("{count}", String(n));
  }

  function getActiveFilter() {
    if (global.AssureStateRail && typeof global.AssureStateRail.getFilter === "function") {
      return global.AssureStateRail.getFilter();
    }
    var stack = $("runs-stack");
    return (stack && stack.getAttribute("data-filter")) || "all";
  }

  function runMatchesFilter(run, filter) {
    if (filter === "all") return true;
    if (filter === "grounded") return (run.extracted_locks || []).length > 0;
    if (filter === "redhat") {
      return (run.redhat_findings || []).length > 0;
    }
    if (filter === "dossier") {
      return run.status === "stamped" && (run.extracted_locks || []).length > 0;
    }
    return true;
  }

  function emptyStateMessage(filter) {
    if (filter === "grounded") {
      return translate(
        "founder.runs.empty_grounded",
        "No grounded runs found. Attach sources to generate deterministic locks."
      );
    }
    if (filter === "redhat") {
      return translate(
        "founder.runs.empty_redhat",
        "No Red-Hat audits found. Run Red-Hat on a draft run."
      );
    }
    if (filter === "dossier") {
      return translate(
        "founder.runs.empty_dossier",
        "No export-ready runs found. Complete verification to build a dossier."
      );
    }
    return translate("founder.runs.empty_all", "No runs yet. Press ⌘K to investigate.");
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

  function emitRunsUpdated(streaming) {
    document.dispatchEvent(
      new CustomEvent("assure:runs-updated", {
        detail: { runs: runs.slice(), streaming: !!streaming },
      })
    );
  }

  function render() {
    var host = $("runs-stack-list");
    if (!host) return;
    var filter = getActiveFilter();
    var visible = runs.filter(function (run) {
      return runMatchesFilter(run, filter);
    });

    if (!runs.length) {
      host.innerHTML = '<p class="runs-stack-empty hint">' + esc(emptyStateMessage("all")) + "</p>";
      emitRunsUpdated(false);
      return;
    }

    if (!visible.length) {
      host.innerHTML = '<p class="runs-stack-empty hint">' + esc(emptyStateMessage(filter)) + "</p>";
      emitRunsUpdated(false);
      return;
    }

    host.innerHTML = visible
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
          '" data-grounded="' +
          (locks > 0 ? "1" : "0") +
          '" data-redhat="' +
          (findings.length > 0 ? "1" : "0") +
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
          esc(runDisplayTitle(run)) +
          "</h4>" +
          '<p class="run-card-meta">' +
          esc(verifiedClaimsLabel(locks) + " · " + fmtTime(run.created_at)) +
          "</p>" +
          '<div class="run-card-preview">' +
          preview +
          "</div>" +
          (findingsHtml ? '<ul class="run-findings-list">' + findingsHtml + "</ul>" : "") +
          '<div class="run-card-actions">' +
          '<button type="button" class="btn btn-primary btn-sm run-btn-draft" data-action="draft">' +
          esc(translate("founder.runs.send_draft", "Send to Draft")) +
          "</button>" +
          '<button type="button" class="btn btn-outline btn-sm run-btn-redhat" data-action="redhat">' +
          esc(translate("generate.redhat_short", "Red-Hat")) +
          "</button>" +
          '<button type="button" class="btn btn-text-subtle btn-sm run-btn-delete" data-action="delete" aria-label="' +
          esc(translate("founder.runs.delete_aria", "Delete run")) +
          '">' +
          esc(translate("founder.runs.delete", "Delete")) +
          "</button>" +
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
      if (rh)
        rh.addEventListener("click", function () {
          runRedhat(id, rh);
        });
      card.querySelectorAll('[data-action="accept-finding"]').forEach(function (btn) {
        btn.addEventListener("click", function () {
          resolveFinding(id, btn.getAttribute("data-finding-id"), "accept", "");
        });
      });
      card.querySelectorAll('[data-action="dismiss-finding"]').forEach(function (btn) {
        btn.addEventListener("click", function () {
          var rationale =
            window.prompt(translate("founder.runs.dismiss_prompt", "Dismissal rationale (required):")) || "";
          if (!rationale.trim()) return;
          resolveFinding(id, btn.getAttribute("data-finding-id"), "dismiss", rationale.trim());
        });
      });
    });
    emitRunsUpdated(false);
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
      document.dispatchEvent(new CustomEvent("assure:pipeline-status", { detail: { message: "" } }));
      return;
    }
    el.hidden = false;
    el.textContent = message;
    document.dispatchEvent(new CustomEvent("assure:pipeline-status", { detail: { message: message } }));
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
      btn.textContent = translate("founder.runs.running", "Running…");
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
          btn.textContent = translate("generate.redhat_short", "Red-Hat");
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
    var pane = $("runs-stack");
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
    document.addEventListener("assure:state-filter", function () {
      render();
    });
    load();
  }

  global.AssureRunsStack = {
    load: load,
    prepend: prepend,
    render: render,
    init: init,
    setPipelineStatus: setPipelineStatus,
    getRuns: function () {
      return runs.slice();
    },
  };
  document.addEventListener("DOMContentLoaded", init);
})(window);
