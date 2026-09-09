/**
 * Founder workbench — Red-Hat audit findings (right drawer).
 */
(function (global) {
  "use strict";

  function $(id) {
    return document.getElementById(id);
  }

  function esc(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function translate(key, fallback) {
    if (typeof global.__assureT === "function") return global.__assureT(key, fallback);
    return fallback || key;
  }

  function panel() {
    return $("drawer-redhat");
  }

  function severityClass(severity) {
    var s = String(severity || "medium").toLowerCase();
    if (s === "high" || s === "fatal" || s === "critical") return "is-fatal";
    return "is-warning";
  }

  function severityLabel(severity) {
    var s = String(severity || "medium").toLowerCase();
    if (s === "high" || s === "fatal" || s === "critical") {
      return translate("founder.drawer.severity_fatal", "Fatal");
    }
    return translate("founder.drawer.severity_warning", "Warning");
  }

  function selectedRun() {
    if (global.AssureRunsStack && typeof global.AssureRunsStack.getSelectedRunId === "function") {
      var id = global.AssureRunsStack.getSelectedRunId();
      if (id) {
        var runs = (global.AssureRunsStack.getRuns && global.AssureRunsStack.getRuns()) || [];
        return runs.filter(function (r) {
          return r.id === id;
        })[0];
      }
    }
    return null;
  }

  function findingsForDrawer() {
    var run = selectedRun();
    if (run) return run.redhat_findings || [];
    var runs = (global.AssureRunsStack && global.AssureRunsStack.getRuns()) || [];
    var out = [];
    runs.forEach(function (r) {
      (r.redhat_findings || []).forEach(function (f) {
        if (f.status === "open") out.push(Object.assign({ _runId: r.id }, f));
      });
    });
    return out;
  }

  function render() {
    var el = panel();
    if (!el) return;
    var findings = findingsForDrawer();
    if (!findings.length) {
      el.innerHTML =
        '<p class="hint">' +
        esc(translate("founder.drawer.redhat_empty", "No Red-Hat findings for this run.")) +
        "</p>";
      return;
    }
    el.innerHTML = findings
      .map(function (f) {
        var runId = f._runId || f.run_id || "";
        return (
          '<article class="drawer-redhat-card ' +
          severityClass(f.severity) +
          '" data-run-id="' +
          esc(runId) +
          '" data-finding-id="' +
          esc(f.id || "") +
          '">' +
          '<div class="drawer-redhat-severity">' +
          esc(severityLabel(f.severity)) +
          "</div>" +
          "<h4>" +
          esc(f.title || "Finding") +
          "</h4>" +
          "<p>" +
          esc(f.content || "") +
          "</p>" +
          (f.suggested_fix
            ? '<p class="drawer-redhat-fix hint"><strong>' +
              esc(translate("founder.drawer.suggested_fix", "Suggested fix")) +
              ":</strong> " +
              esc(f.suggested_fix) +
              "</p>"
            : "") +
          '<button type="button" class="btn btn-outline btn-sm drawer-accept-fix" data-action="accept-finding">' +
          esc(translate("founder.runs.accept_fix", "Accept Fix")) +
          "</button>" +
          "</article>"
        );
      })
      .join("");

    el.querySelectorAll(".drawer-accept-fix").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var card = btn.closest(".drawer-redhat-card");
        if (!card) return;
        document.dispatchEvent(
          new CustomEvent("assure:drawer-accept-fix", {
            detail: {
              runId: card.getAttribute("data-run-id"),
              findingId: card.getAttribute("data-finding-id"),
            },
          })
        );
      });
    });
  }

  function activate() {
    render();
  }

  function init() {
    document.addEventListener("assure:runs-updated", function () {
      if (
        global.AssureWorkbenchPanes &&
        global.AssureWorkbenchPanes.getDrawerMode &&
        global.AssureWorkbenchPanes.getDrawerMode() === "redhat"
      ) {
        render();
      }
    });
  }

  global.AssureRedhatDrawer = { activate: activate, render: render, init: init };
  document.addEventListener("DOMContentLoaded", init);
})(window);
