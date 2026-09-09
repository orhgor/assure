/**
 * Founder workbench — Evidence Inspector (right drawer).
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

  function toast(message, kind) {
    if (global.AssureToast && typeof global.AssureToast.show === "function") {
      global.AssureToast.show(message, kind || "info");
    }
  }

  function isFounderShell() {
    return (
      document.body.classList.contains("founder-workbench") &&
      !document.body.classList.contains("legacy-workbench")
    );
  }

  function panel() {
    return $("drawer-evidence");
  }

  function renderLoading() {
    var el = panel();
    if (!el) return;
    el.innerHTML =
      '<p class="hint founder-drawer-loading">' +
      esc(translate("evidence.inspector.loading", "Loading source…")) +
      "</p>";
  }

  function renderEvidence(data) {
    var el = panel();
    if (!el) return;
    el.innerHTML =
      '<div class="drawer-evidence-meta">' +
      '<p class="drawer-evidence-row"><strong>' +
      esc(translate("evidence.inspector.source", "Source:")) +
      "</strong> " +
      esc(data.source_name || data.source_id || "—") +
      "</p>" +
      '<p class="drawer-evidence-row"><strong>' +
      esc(translate("founder.drawer.page", "Page")) +
      ":</strong> " +
      esc(String(data.page_number || "—")) +
      "</p>" +
      '<p class="drawer-evidence-row"><strong>' +
      esc(translate("evidence.inspector.lock_hash", "Lock hash:")) +
      "</strong> <code>" +
      esc(data.lock_hash || "") +
      "</code></p>" +
      "</div>" +
      '<blockquote class="drawer-evidence-excerpt">' +
      esc(data.excerpt || "") +
      "</blockquote>" +
      '<pre class="drawer-evidence-z3">' +
      esc(data.z3_proof || "") +
      "</pre>";
  }

  function renderError(message) {
    var el = panel();
    if (!el) return;
    el.innerHTML = '<p class="hint drawer-evidence-error">' + esc(message) + "</p>";
  }

  function open(detail) {
    detail = detail || {};
    var lockHash = detail.lockHash || detail.lock_hash || "";
    if (!lockHash) return;
    if (global.AssureWorkbenchPanes && typeof global.AssureWorkbenchPanes.openDrawer === "function") {
      global.AssureWorkbenchPanes.openDrawer("evidence", { lockHash: lockHash });
      return;
    }
    renderLoading();
    fetch("/api/locks/" + encodeURIComponent(lockHash) + "/evidence", { credentials: "same-origin" })
      .then(function (res) {
        return res.json().then(function (data) {
          return { ok: res.ok && data.ok !== false, data: data };
        });
      })
      .then(function (result) {
        if (!result.ok) {
          renderError(
            (result.data && result.data.error) ||
              translate("evidence.inspector.load_error", "Could not load source document.")
          );
          toast(
            (result.data && result.data.error) ||
              translate("evidence.inspector.load_error", "Could not load source document."),
            "error"
          );
          return;
        }
        renderEvidence(result.data);
      })
      .catch(function () {
        renderError(translate("evidence.inspector.load_error", "Could not load source document."));
        toast(translate("evidence.inspector.load_error", "Could not load source document."), "error");
      });
  }

  function activate(detail) {
    detail = detail || {};
    var lockHash = detail.lockHash || detail.lock_hash || "";
    if (!lockHash) return;
    renderLoading();
    fetch("/api/locks/" + encodeURIComponent(lockHash) + "/evidence", { credentials: "same-origin" })
      .then(function (res) {
        return res.json().then(function (data) {
          return { ok: res.ok && data.ok !== false, data: data };
        });
      })
      .then(function (result) {
        if (!result.ok) {
          renderError(
            (result.data && result.data.error) ||
              translate("evidence.inspector.load_error", "Could not load source document.")
          );
          toast(
            (result.data && result.data.error) ||
              translate("evidence.inspector.load_error", "Could not load source document."),
            "error"
          );
          return;
        }
        renderEvidence(result.data);
      })
      .catch(function () {
        renderError(translate("evidence.inspector.load_error", "Could not load source document."));
        toast(translate("evidence.inspector.load_error", "Could not load source document."), "error");
      });
  }

  function init() {
    document.addEventListener("assure:lock-pill-click", function (ev) {
      if (!isFounderShell()) return;
      open((ev && ev.detail) || {});
    });
  }

  global.AssureEvidenceDrawer = { open: open, activate: activate, init: init };
  document.addEventListener("DOMContentLoaded", init);
})(window);
