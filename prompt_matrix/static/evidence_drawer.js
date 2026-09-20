/**
 * Founder workbench -- Evidence Inspector (right drawer).
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

  function z3StatusLabel(proof) {
    var text = String(proof || "").toLowerCase();
    if (text.indexOf("violation") >= 0 || text.indexOf("unsat") >= 0) {
      return translate("evidence.inspector.unsat", "UNSAT");
    }
    return translate("evidence.inspector.satisfiable", "SATISFIABLE");
  }

  function renderLoading() {
    var el = panel();
    if (!el) return;
    el.innerHTML =
      '<div class="evidence-inspector-skeleton" role="status" aria-busy="true">' +
      '<div class="evidence-inspector-skeleton__line evidence-inspector-skeleton__line--wide"></div>' +
      '<div class="evidence-inspector-skeleton__line evidence-inspector-skeleton__line--mid"></div>' +
      '<div class="evidence-inspector-skeleton__line evidence-inspector-skeleton__line--short"></div>' +
      '<p class="hint founder-drawer-loading">' +
      esc(translate("evidence.inspector.loading", "Loading source…")) +
      "</p></div>";
  }

  function renderEvidence(data) {
    var el = panel();
    if (!el) return;
    var page = data.page_number != null ? String(data.page_number) : "--";
    var verdict = data.verdict || "unverified";
    var verdictLabel = translate("evidence.verdict." + verdict, verdict);
    var badgeClass = "evidence-inspector-verdict__badge is-" + verdict;
    var satLabel = z3StatusLabel(data.z3_proof);
    var satClass = satLabel.indexOf("UNSAT") >= 0 ? "is-unsat" : "is-sat";
    var badgeClass2 =
      satClass === "is-unsat" ? "evidence-inspector-proof__badge is-unsat" : "evidence-inspector-proof__badge";
    el.innerHTML =
      '<section class="evidence-inspector-section">' +
      '<h3 class="evidence-inspector-section__title">' +
      esc(translate("evidence.inspector.substrate_origin", "Substrate Origin")) +
      "</h3>" +
      '<div class="evidence-inspector-origin">' +
      '<div class="evidence-inspector-origin__meta">' +
      '<span class="evidence-inspector-origin__name">' +
      esc(data.source_name || data.source_id || "--") +
      "</span>" +
      '<span class="evidence-inspector-origin__page">' +
      esc(translate("founder.drawer.page", "Page")) +
      " " +
      esc(page) +
      "</span>" +
      '<span class="evidence-inspector-verdict__badge is-' +
      verdict +
      '">' +
      esc(verdictLabel) +
      "</span>" +
      "</div>" +
      '<p class="evidence-inspector-origin__excerpt">' +
      esc(data.excerpt || "") +
      "</p>" +
      '<p class="evidence-hash hint"><code>' +
      esc(data.lock_hash || "") +
      "</code></p>" +
      "</div></section>" +
      '<section class="evidence-inspector-section">' +
      '<h3 class="evidence-inspector-section__title">' +
      esc(translate("evidence.inspector.z3_proof", "Z3 SMT Solver Proof")) +
      "</h3>" +
      '<div class="evidence-inspector-proof">' +
      '<span class="' +
      badgeClass2 +
      '">' +
      esc(satLabel) +
      "</span>" +
      '<pre class="evidence-inspector-proof__log drawer-evidence-z3">' +
      esc(data.z3_proof || "") +
      "</pre>" +
      "</div>" +
      "</section>" +
      '<section class="evidence-inspector-section">' +
      '<h3 class="evidence-inspector-section__title">' +
      esc(translate("evidence.inspector.verdict", "Evidence Verdict")) +
      "</h3>" +
      '<div class="evidence-inspector-verdict">' +
      '<span class="evidence-inspector-verdict__badge is-' +
      verdict +
      '">' +
      esc(verdictLabel) +
      "</span>" +
      (data.verdict_reason
        ? '<p class="evidence-inspector-verdict__reason">' + esc(data.verdict_reason) + "</p>"
        : "") +
      "</div></section>";
  }

  function renderError(message) {
    var el = panel();
    if (!el) return;
    el.innerHTML = '<p class="hint drawer-evidence-error">' + esc(message) + "</p>";
  }

  function fetchEvidence(lockHash) {
    renderLoading();
    return fetch("/api/locks/" + encodeURIComponent(lockHash) + "/evidence", {
      credentials: "same-origin",
    })
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

  function open(detail) {
    detail = detail || {};
    var lockHash = detail.lockHash || detail.lock_hash || "";
    if (!lockHash) return;
    if (global.AssureWorkbenchPanes && typeof global.AssureWorkbenchPanes.openDrawer === "function") {
      global.AssureWorkbenchPanes.openDrawer("evidence", { lockHash: lockHash });
      return;
    }
    fetchEvidence(lockHash);
  }

  function activate(detail) {
    detail = detail || {};
    var lockHash = detail.lockHash || detail.lock_hash || "";
    if (!lockHash) return;
    fetchEvidence(lockHash);
  }

  function openEvidenceDrawer(hash, e) {
    if (e) {
      e.preventDefault();
      e.stopPropagation();
    }
    if (!hash) return;
    open({ lockHash: hash });
  }

  function init() {
    document.addEventListener("assure:lock-pill-click", function (ev) {
      if (!isFounderShell()) return;
      var detail = (ev && ev.detail) || {};
      open(detail);
    });
  }

  global.openEvidenceDrawer = openEvidenceDrawer;
  global.AssureEvidenceDrawer = { open: open, activate: activate, init: init, openEvidenceDrawer: openEvidenceDrawer };
  document.addEventListener("DOMContentLoaded", init);
})(window);
