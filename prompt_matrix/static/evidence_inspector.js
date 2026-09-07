/**
 * Founder workbench — Evidence Inspector drawer (lock pill click).
 */
(function (global) {
  "use strict";

  var drawer = null;
  var workspaceId = "default";

  function $(id) {
    return document.getElementById(id);
  }

  function esc(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function open(detail) {
    if (!drawer) return;
    detail = detail || {};
    drawer.hidden = false;
    drawer.setAttribute("aria-hidden", "false");
    $("evidence-inspector-hash").textContent = detail.lockHash || "—";
    $("evidence-inspector-source-id").textContent = detail.sourceId || "—";
    var coords = detail.pageCoordinates || {};
    $("evidence-inspector-coords").textContent = JSON.stringify(coords, null, 2);
    $("evidence-inspector-body").innerHTML = '<p class="hint">Loading source…</p>';
    if (detail.sourceId) {
      fetch(
        "/api/projects/" +
          encodeURIComponent(workspaceId) +
          "/substrate/" +
          encodeURIComponent(detail.sourceId),
        { credentials: "same-origin" }
      )
        .then(function (r) {
          return r.json();
        })
        .then(function (data) {
          var text = (data && (data.extracted_text || data.text)) || "No extracted text for this source.";
          var page = Number(coords.page || 1);
          var marker = "<mark class=\"evidence-highlight\" id=\"evidence-page-marker\">";
          var body = esc(text).replace(/\n/g, "<br>");
          if (page > 1) {
            var chunks = text.split(/\f|\n---+\n/);
            if (chunks[page - 1]) {
              body =
                esc(chunks.slice(0, page - 1).join("\n")).replace(/\n/g, "<br>") +
                marker +
                esc(chunks[page - 1]).replace(/\n/g, "<br>") +
                "</mark>" +
                esc(chunks.slice(page).join("\n")).replace(/\n/g, "<br>");
            }
          } else {
            body = marker + body + "</mark>";
          }
          $("evidence-inspector-body").innerHTML =
            '<div class="evidence-source-doc">' + body + "</div>";
          $("evidence-inspector-filename").textContent = (data && data.filename) || detail.sourceId;
        })
        .catch(function () {
          $("evidence-inspector-body").innerHTML =
            '<p class="hint">Could not load source document.</p>';
        });
    } else {
      $("evidence-inspector-body").innerHTML = '<p class="hint">No source linked to this lock.</p>';
      $("evidence-inspector-filename").textContent = "Unanchored lock";
    }
  }

  function close() {
    if (!drawer) return;
    drawer.hidden = true;
    drawer.setAttribute("aria-hidden", "true");
  }

  function goToSource() {
    var marker = document.getElementById("evidence-page-marker");
    if (marker && marker.scrollIntoView) marker.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function init() {
    drawer = $("evidence-inspector-drawer");
    workspaceId = global.__ASSURE_PROJECT_ID__ || "default";
    var closeBtn = $("evidence-inspector-close");
    var gotoBtn = $("evidence-inspector-goto");
    if (closeBtn) closeBtn.addEventListener("click", close);
    if (gotoBtn) gotoBtn.addEventListener("click", goToSource);
    document.addEventListener("assure:lock-pill-click", function (ev) {
      open((ev && ev.detail) || {});
    });
    document.addEventListener("assure:project", function (ev) {
      workspaceId = (ev.detail && ev.detail.projectId) || workspaceId;
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && drawer && !drawer.hidden) close();
    });
  }

  global.AssureEvidenceInspector = { open: open, close: close, init: init };
  document.addEventListener("DOMContentLoaded", init);
})(window);
