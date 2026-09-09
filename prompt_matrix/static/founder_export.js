/**
 * Founder workbench — export verified dossier trigger.
 */
(function (global) {
  "use strict";

  function workspaceId() {
    if (global.AssureFounderMode && typeof global.AssureFounderMode.getWorkspaceId === "function") {
      return global.AssureFounderMode.getWorkspaceId();
    }
    return global.__ASSURE_PROJECT_ID__ || "default";
  }

  function exportDossier() {
    var ws = workspaceId();
    var url = "/api/projects/" + encodeURIComponent(ws) + "/export?format=dossier-pdf";
    var a = document.createElement("a");
    a.href = url;
    a.download = "verification-dossier-" + ws + ".pdf";
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  function init() {
    var btn = document.getElementById("btn-export-dossier");
    if (btn) btn.addEventListener("click", exportDossier);
  }

  global.AssureFounderExport = { exportDossier: exportDossier, init: init };
  document.addEventListener("DOMContentLoaded", init);
})(window);
